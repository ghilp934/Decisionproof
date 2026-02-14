# Optimistic Locking Pattern
## Version Column을 이용한 동시성 제어 패턴

**Category**: Concurrency Pattern
**Complexity**: ⭐⭐ Medium
**Performance Impact**: 거의 없음

---

## 📋 Pattern Summary

**Also Known As**: Optimistic Concurrency Control, Version-based Locking

**Intent**: 동시에 여러 프로세스가 같은 데이터를 수정할 때, "stale update" (오래된 데이터로 덮어쓰기)를 방지합니다.

**Motivation**: Database row-level lock 없이도 동시성을 안전하게 제어할 수 있습니다. Lock을 기다리지 않아서 성능이 좋습니다.

---

## 🎯 Problem (문제)

### 시나리오: 두 Worker가 동시에 같은 Run을 처리

```
시간축:
00:00 - Worker A: Run 조회 (status="QUEUED", version=1)
00:01 - Worker B: Run 조회 (status="QUEUED", version=1)
00:02 - Worker A: status를 "PROCESSING"으로 업데이트
00:03 - Worker B: status를 "PROCESSING"으로 업데이트  ← 문제!
```

**문제점**:
- Worker A의 업데이트를 Worker B가 덮어씀 (lost update)
- Worker A가 설정한 `lease_expires_at`이 사라짐
- Worker B는 Worker A가 이미 처리 중인지 모름

**결과**:
- 두 Worker가 동시에 같은 Run 처리 (중복 처리)
- 예산 이중 차감
- Money leak!

---

## 🌍 Context (상황)

이 패턴이 필요한 경우:

```
✅ 동시에 여러 프로세스가 같은 데이터 수정 가능
   - 여러 Worker instance
   - Auto-scaling으로 인스턴스 증가
   - Multi-threaded application

✅ "먼저 시작한 사람이 이긴다" 정책
   - WINNER: 먼저 업데이트한 프로세스
   - LOSER: 나중에 업데이트 시도한 프로세스 (재시도)

✅ Row-level lock 회피 (성능 이유)
   - SELECT ... FOR UPDATE는 느림
   - Deadlock 위험
```

이 패턴이 **불필요한** 경우:

```
❌ Single instance (동시성 없음)
❌ Read-only operations
❌ Append-only data (update 없음)
```

---

## ⚖️ Forces (제약 조건)

다음 요구사항들이 충돌합니다:

1. **안전성**: Lost update 절대 방지
2. **성능**: Lock을 기다리지 않고 빠르게
3. **확장성**: Worker 수 증가해도 안정적
4. **복잡도**: 간단한 코드

이 패턴은 **안전성과 성능을 동시에** 달성하고, 약간의 재시도 로직 추가만으로 해결합니다.

---

## ✅ Solution (해결책)

### 핵심 아이디어

```sql
-- version column 추가
ALTER TABLE runs ADD COLUMN version INTEGER DEFAULT 1;

-- 업데이트 시 version 체크 + 증가
UPDATE runs
SET
    status = 'PROCESSING',
    version = version + 1,  -- 자동 증가
    updated_at = NOW()
WHERE
    run_id = '...'
    AND version = 5  -- ← 현재 version일 때만 업데이트

-- affected_rows 확인
-- 1이면 성공 (WINNER)
-- 0이면 실패 (LOSER - 다른 프로세스가 먼저 업데이트함)
```

### 핵심 규칙

```
1. 모든 UPDATE에 version 조건 추가
2. UPDATE 성공 시 version 자동 증가 (version + 1)
3. affected_rows == 0이면 다른 프로세스가 먼저 업데이트함
4. LOSER는 재시도 (최신 version으로 다시 조회)
```

---

## 🛠️ Implementation (구현)

### DPP 프로젝트 실제 코드

#### Step 1: Version Column 추가 (Alembic Migration)

```python
# alembic/versions/xxx_add_version_column.py

def upgrade():
    # 1. version column 추가 (nullable=True로 시작)
    op.add_column('runs', sa.Column('version', sa.Integer(), nullable=True))

    # 2. 기존 데이터에 version=1 설정
    op.execute("UPDATE runs SET version = 1 WHERE version IS NULL")

    # 3. nullable=False로 변경
    op.alter_column('runs', 'version', nullable=False)

    # 4. Default value 설정
    op.alter_column('runs', 'version', server_default=sa.text('1'))
```

#### Step 2: Repository Method with Version Check

```python
# apps/worker/dpp_worker/repositories/run_repository.py

class RunRepository:
    def update_with_version_check(
        self,
        run_id: str,
        tenant_id: str,
        current_version: int,
        updates: dict
    ) -> bool:
        """
        Optimistic locking을 이용한 안전한 업데이트

        Args:
            run_id: Run ID
            tenant_id: Tenant ID
            current_version: 현재 알고 있는 version
            updates: 업데이트할 필드들 (dict)

        Returns:
            True if update succeeded (WINNER)
            False if update failed (LOSER - version mismatch)
        """

        # SET 절 생성
        set_clause = ", ".join([f"{k} = :{k}" for k in updates.keys()])
        set_clause += ", version = version + 1, updated_at = NOW()"

        # SQL 실행
        result = self.db.execute(
            text(f"""
                UPDATE runs
                SET {set_clause}
                WHERE
                    run_id = :run_id
                    AND tenant_id = :tenant_id
                    AND version = :current_version  -- ← Optimistic locking
            """),
            {
                "run_id": run_id,
                "tenant_id": tenant_id,
                "current_version": current_version,
                **updates  # status='PROCESSING', etc.
            }
        )

        self.db.commit()

        # affected_rows 확인
        affected_rows = result.rowcount
        return affected_rows == 1  # 1이면 성공, 0이면 실패
```

#### Step 3: Heartbeat Thread with Version Check

```python
# apps/worker/dpp_worker/heartbeat.py

class HeartbeatThread(threading.Thread):
    def _send_heartbeat(self) -> None:
        """30초마다 heartbeat 전송 (lease 연장)"""

        with self.session_factory() as session:
            repo = RunRepository(session)

            # Optimistic locking으로 안전하게 업데이트
            success = repo.update_with_version_check(
                run_id=self.run_id,
                tenant_id=self.tenant_id,
                current_version=self.current_version,  # ← 현재 version
                updates={
                    "lease_expires_at": datetime.utcnow() + timedelta(seconds=120)
                }
            )

            if success:
                # WINNER: 업데이트 성공
                self.current_version += 1  # version 증가
                logger.info("Heartbeat sent", extra={
                    "run_id": self.run_id,
                    "version": self.current_version
                })
            else:
                # LOSER: 다른 프로세스가 먼저 업데이트함
                logger.warning("Heartbeat failed - version mismatch", extra={
                    "run_id": self.run_id,
                    "expected_version": self.current_version
                })

                # Finalize가 완료되었는지 확인
                latest_run = repo.get_by_id(self.run_id, self.tenant_id)
                if latest_run.finalize_stage == "COMMITTED":
                    logger.info("Run already finalized - stopping heartbeat")
                    self.stop()
```

#### Step 4: Claim with Version Check (2-Phase Commit)

```python
# apps/worker/dpp_worker/repositories/run_repository.py

def claim_for_finalize(
    self,
    run_id: str,
    tenant_id: str,
    current_version: int
) -> bool:
    """
    2-Phase Commit의 CLAIM 단계 (optimistic locking 사용)
    """

    result = self.db.execute(
        text("""
            UPDATE runs
            SET
                finalize_stage = 'CLAIMED',
                version = version + 1,  -- ← version 증가
                updated_at = NOW()
            WHERE
                run_id = :run_id
                AND tenant_id = :tenant_id
                AND version = :current_version  -- ← Optimistic locking
                AND finalize_stage = 'PENDING'  -- 아직 claim 안 된 것만
        """),
        {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "current_version": current_version
        }
    )

    self.db.commit()

    # affected_rows 확인
    if result.rowcount == 0:
        logger.warning("Claim failed - version mismatch or already claimed")
        return False  # LOSER

    logger.info("Claim succeeded")
    return True  # WINNER
```

---

## 🔄 Sequence Diagram (시퀀스 다이어그램)

### 성공 케이스 (WINNER)

```
Worker A                    Database
   |                            |
   | SELECT (version=5)         |
   |--------------------------->|
   |         version=5          |
   |<---------------------------|
   |                            |
   | UPDATE ... WHERE version=5 |
   |--------------------------->|
   |     affected_rows=1        |
   |<---------------------------| (version now 6)
   |                            |
   ✅ Success (WINNER)
```

### 실패 케이스 (LOSER)

```
Worker A          Worker B          Database
   |                 |                  |
   | SELECT (v=5)    |                  |
   |---------------->|                  |
   |     v=5         |                  |
   |<----------------|                  |
   |                 | SELECT (v=5)     |
   |                 |----------------->|
   |                 |      v=5         |
   |                 |<-----------------|
   |                 |                  |
   | UPDATE v=5      |                  |
   |---------------->|                  |
   |  affected=1 ✅  |                  |
   |<----------------|                  | (v now 6)
   |                 |                  |
   |                 | UPDATE v=5       |
   |                 |----------------->|
   |                 |  affected=0 ❌   |
   |                 |<-----------------| (version mismatch!)
   |                 |                  |
   |                 ❌ LOSER (재시도)
```

---

## 📊 Consequences (결과/장단점)

### ✅ Benefits (장점)

1. **Lost Update 방지**
   - 항상 최신 버전으로만 업데이트
   - Stale data로 덮어쓰기 불가능

2. **Lock-Free (높은 성능)**
   - SELECT ... FOR UPDATE 불필요
   - Deadlock 위험 없음
   - 대기 시간 없음

3. **확장성**
   - Worker 수 증가해도 안정적
   - Auto-scaling 가능

4. **간단한 구현**
   - version column 1개 추가
   - WHERE 절에 version 조건만 추가

### ❌ Drawbacks (단점)

1. **재시도 로직 필요**
   - LOSER는 재시도해야 함
   - 높은 경쟁 시 재시도 빈번

2. **Version Column 관리**
   - 모든 UPDATE에 version 조건 추가 필요
   - 실수로 빼먹으면 버그

3. **Pessimistic Locking보다 복잡한 로직**
   - SELECT ... FOR UPDATE는 단순 (기다리기만 하면 됨)
   - Optimistic은 재시도 로직 필요

---

## 🌍 Known Uses (실제 사용 사례)

### DPP API Platform
```
Component: Worker, Reaper
Problem: 여러 Worker가 동시에 같은 Run 처리 시도
Solution: version column + WHERE version = :current_version
Result: Lost update 0건, 동시성 안전 보장
```

### Hibernate ORM (Java)
```java
@Entity
public class Product {
    @Version
    private int version;  // Hibernate가 자동 관리
}

// Hibernate가 자동으로 version 체크 + 증가
entityManager.merge(product);  // OptimisticLockException 발생 가능
```

### Django ORM (Python)
```python
from django.db.models import F

# Optimistic locking (version 체크)
affected = Product.objects.filter(
    id=product_id,
    version=current_version
).update(
    status='SOLD',
    version=F('version') + 1
)

if affected == 0:
    raise ConcurrentModificationError()
```

### MongoDB
```javascript
// MongoDB의 optimistic locking
db.products.update(
    { _id: productId, version: 5 },  // version 체크
    { $set: { status: 'SOLD' }, $inc: { version: 1 } }  // version 증가
)

// modifiedCount == 0이면 version mismatch
```

---

## 🔗 Related Patterns (관련 패턴)

### Pessimistic Locking (대안)
```sql
-- Pessimistic locking (SELECT ... FOR UPDATE)
SELECT * FROM runs WHERE run_id = '...' FOR UPDATE;
-- 다른 프로세스는 여기서 대기 (block)

UPDATE runs SET status = 'PROCESSING' WHERE run_id = '...';
```

**언제 Pessimistic 선택**:
- 경쟁이 매우 높음 (재시도가 더 비쌈)
- 대기 시간 허용 가능
- 간단한 로직 선호

**언제 Optimistic 선택**:
- 경쟁이 낮거나 중간
- 성능 우선 (lock 회피)
- 확장성 중요

### 2-Phase Commit Pattern
```
관계: 2PC의 CLAIM 단계에서 optimistic locking 사용
목적: 동시에 여러 Worker가 claim하는 것 방지
```

### CAS (Compare-And-Swap)
```
관계: Optimistic locking의 low-level 구현
예시: Redis WATCH + MULTI + EXEC
```

---

## 💡 Implementation Tips

### Tip 1: Version은 항상 증가
```python
# ✅ 올바른 방법
SET version = version + 1

# ❌ 잘못된 방법 (덮어쓰기)
SET version = 6  # 동시 업데이트 시 충돌 가능
```

### Tip 2: 재시도는 Exponential Backoff
```python
MAX_RETRIES = 3

for attempt in range(MAX_RETRIES):
    success = repo.update_with_version_check(...)

    if success:
        break  # WINNER

    # LOSER - 재시도
    if attempt < MAX_RETRIES - 1:
        sleep_time = (2 ** attempt) * 0.1  # 0.1s, 0.2s, 0.4s
        time.sleep(sleep_time)

        # 최신 version으로 다시 조회
        run = repo.get_by_id(run_id, tenant_id)
        current_version = run.version
    else:
        raise ConcurrentModificationError("Max retries exceeded")
```

### Tip 3: Version Mismatch는 정상적인 케이스
```python
# ✅ 올바른 로깅 (WARNING 수준)
if not success:
    logger.warning("Version mismatch - another process won", extra={
        "run_id": run_id,
        "expected_version": current_version
    })

# ❌ 잘못된 로깅 (ERROR 수준)
# Version mismatch는 에러가 아니라 정상적인 경쟁 결과
```

### Tip 4: Read에도 Version 반환
```python
# ✅ Version을 함께 반환
def get_by_id(self, run_id: str) -> Run:
    run = self.db.query(Run).filter(Run.run_id == run_id).first()
    # run.version 포함됨
    return run

# ❌ Version 빠뜨리면 optimistic locking 불가능
```

---

## 🧪 Testing Strategy

### Unit Test: Version Mismatch
```python
def test_update_with_stale_version():
    """Stale version으로 업데이트 시도 → 실패"""

    # 1. Run 생성 (version=1)
    run = create_run(status="QUEUED", version=1)

    # 2. 다른 프로세스가 먼저 업데이트 (version=2)
    repo.update_with_version_check(
        run_id=run.run_id,
        current_version=1,
        updates={"status": "PROCESSING"}
    )  # Success (version now 2)

    # 3. Stale version으로 업데이트 시도 (version=1)
    success = repo.update_with_version_check(
        run_id=run.run_id,
        current_version=1,  # Stale!
        updates={"status": "COMPLETED"}
    )

    # 검증: 실패
    assert success is False

    # 검증: 상태는 "PROCESSING" (첫 번째 업데이트 유지)
    run = repo.get_by_id(run.run_id)
    assert run.status == "PROCESSING"
    assert run.version == 2
```

### Integration Test: Concurrent Updates
```python
def test_concurrent_updates():
    """여러 스레드가 동시에 업데이트 → 하나만 성공"""

    run = create_run(status="QUEUED", version=1)

    def update_worker():
        return repo.update_with_version_check(
            run_id=run.run_id,
            current_version=1,
            updates={"status": "PROCESSING"}
        )

    # 10개 스레드가 동시에 업데이트 시도
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(update_worker) for _ in range(10)]
        results = [f.result() for f in futures]

    # 검증: 정확히 1개만 성공
    assert results.count(True) == 1
    assert results.count(False) == 9

    # 검증: version은 2로 증가
    run = repo.get_by_id(run.run_id)
    assert run.version == 2
```

### Chaos Test: Retry Success
```python
def test_retry_after_version_mismatch():
    """Version mismatch 후 재시도 → 성공"""

    run = create_run(status="QUEUED", version=1)

    # 첫 번째 시도: 실패 (다른 프로세스가 먼저 업데이트)
    repo.update_with_version_check(run.run_id, current_version=1, updates={"status": "PROCESSING"})

    # 재시도: 최신 version으로 다시 조회
    run = repo.get_by_id(run.run_id)
    current_version = run.version  # 2

    # 두 번째 시도: 성공
    success = repo.update_with_version_check(
        run_id=run.run_id,
        current_version=current_version,  # 2
        updates={"status": "COMPLETED"}
    )

    assert success is True
    run = repo.get_by_id(run.run_id)
    assert run.status == "COMPLETED"
    assert run.version == 3
```

---

## 📚 Further Reading

- [Optimistic Locking (Martin Fowler)](https://martinfowler.com/eaaCatalog/optimisticOfflineLock.html)
- [Hibernate Optimistic Locking](https://docs.jboss.org/hibernate/orm/6.0/userguide/html_single/Hibernate_User_Guide.html#locking-optimistic)
- [Designing Data-Intensive Applications](https://dataintensive.net/) - Chapter 7: Transactions

---

**Last Updated**: 2026-02-14
**Version**: 1.0
**Based on**: DPP API Platform v0.4.2.2 (MS-4, P0-1 Critical Feedback)
