# 2-Phase Commit Pattern
## 분산 트랜잭션의 원자성 보장 패턴

**Category**: Architectural Pattern
**Complexity**: ⭐⭐⭐ High
**Performance Impact**: Medium (2 DB calls instead of 1)

---

## 📋 Pattern Summary

**Also Known As**: Two-Phase Commit, 2PC

**Intent**: 여러 독립적인 작업(Database + S3 upload 등)을 원자적으로 완료하여, "부분 완료" 상태를 방지합니다.

**Motivation**: Database transaction만으로는 S3 upload 같은 외부 시스템 작업을 포함할 수 없습니다. 2-Phase Commit은 이런 분산 작업을 논리적으로 하나의 트랜잭션처럼 취급합니다.

---

## 🎯 Problem (문제)

### 시나리오: Worker가 Run을 처리하고 결과를 S3에 업로드

```
1. Run 처리 (DPP pack 실행)
2. 결과를 S3에 업로드
3. Database 상태를 "COMPLETED"로 업데이트
4. 예산 정산
```

**문제점**:
- S3 upload가 성공했는데 DB update가 실패하면?
  - S3에 파일은 있지만 DB는 "PROCESSING" 상태 (orphan file)
- DB update가 성공했는데 S3 upload가 없으면?
  - DB는 "COMPLETED"인데 결과 파일 없음 (money leak!)

**핵심 이슈**: Database transaction은 S3 같은 외부 시스템을 포함할 수 없습니다.

---

## 🌍 Context (상황)

이 패턴이 필요한 경우:

```
✅ 여러 독립적인 시스템 간 작업이 원자적으로 완료되어야 함
   - Database + S3
   - Database + 외부 API 호출
   - Database + Redis

✅ "부분 완료" 상태가 치명적인 경우
   - 금전 거래 (돈만 빠지고 상품 안 줌)
   - 파일 업로드 (메타데이터만 있고 파일 없음)

✅ Eventual consistency로 충분하지 않은 경우
   - Strong consistency 필요
   - 불일치 허용 불가
```

이 패턴이 **불필요한** 경우:

```
❌ 단일 Database transaction으로 해결 가능
❌ Eventual consistency 허용 (나중에 맞추면 됨)
❌ 작업이 독립적 (순서 상관없음)
```

---

## ⚖️ Forces (제약 조건)

다음 요구사항들이 충돌합니다:

1. **원자성**: 모든 작업이 성공하거나 모두 실패해야 함
2. **성능**: 여러 번 DB 접근은 느림
3. **복잡도**: 간단한 코드가 좋음
4. **복구 가능성**: 장애 시 자동 복구 필요

이 패턴은 **원자성과 복구 가능성을 우선**하고, 성능과 복잡도를 일부 희생합니다.

---

## ✅ Solution (해결책)

### 핵심 아이디어

작업을 3단계로 나눕니다:

```
Phase 1: CLAIM (예약)
  - "나 이 작업 할 거야" 선언
  - Database에 finalize_stage = "CLAIMED" 기록
  - 아직 돌이킬 수 있음

Phase 2: EXECUTE (실행)
  - 실제 작업 수행 (S3 upload)
  - 성공하면 Phase 3로, 실패하면 rollback

Phase 3: COMMIT (확정)
  - finalize_stage = "COMMITTED"
  - 이제 완료됨, 돌이킬 수 없음
```

### 핵심 규칙

```
1. CLAIM 단계에서 충돌 감지 (optimistic locking)
2. EXECUTE 단계는 멱등성 보장 (재시도 가능)
3. COMMIT 단계는 단순 상태 변경 (실패 확률 최소화)
4. 각 단계 사이에 장애가 발생해도 복구 가능
```

---

## 🛠️ Implementation (구현)

### DPP 프로젝트 실제 코드

#### Step 1: finalize_stage Column 추가 (Alembic Migration)

```python
# alembic/versions/xxx_add_finalize_stage.py
def upgrade():
    op.add_column('runs', sa.Column('finalize_stage', sa.String(20), nullable=True))
    # PENDING → CLAIMED → COMMITTED
    op.execute("UPDATE runs SET finalize_stage = 'COMMITTED' WHERE status IN ('COMPLETED', 'FAILED')")
    op.execute("UPDATE runs SET finalize_stage = 'PENDING' WHERE status = 'PROCESSING'")
```

#### Step 2: Phase 1 - CLAIM (예약)

```python
# apps/worker/dpp_worker/loops/sqs_loop.py

def _process_message(self, message: Dict[str, Any]) -> bool:
    run_id = message["run_id"]

    # Phase 1: CLAIM (원자적으로 예약)
    try:
        claimed = self.repo.claim_for_finalize(
            run_id=run_id,
            tenant_id=tenant_id,
            current_version=run.version  # Optimistic locking
        )

        if not claimed:
            # 다른 Worker가 먼저 claim함 (LOSER)
            logger.warning("Claim failed - another worker won")
            return False  # 메시지 삭제하지 않음

    except ClaimError as e:
        logger.error("Claim error", exc_info=True)
        return False
```

```python
# apps/worker/dpp_worker/repositories/run_repository.py

def claim_for_finalize(self, run_id: str, tenant_id: str, current_version: int) -> bool:
    """Phase 1: CLAIM - 원자적으로 finalize 예약"""

    result = self.db.execute(
        text("""
            UPDATE runs
            SET
                finalize_stage = 'CLAIMED',
                version = version + 1,
                updated_at = NOW()
            WHERE
                run_id = :run_id
                AND tenant_id = :tenant_id
                AND version = :current_version  -- Optimistic locking
                AND finalize_stage = 'PENDING'  -- 아직 claim 안 된 것만
            RETURNING version
        """),
        {"run_id": run_id, "tenant_id": tenant_id, "current_version": current_version}
    )

    row = result.fetchone()
    if row is None:
        return False  # Claim 실패 (다른 worker가 먼저 함)

    self.db.commit()
    return True  # Claim 성공 (WINNER)
```

#### Step 3: Phase 2 - EXECUTE (실행)

```python
# apps/worker/dpp_worker/loops/sqs_loop.py (계속)

    # Phase 2: EXECUTE (실제 작업 수행)
    try:
        # S3 upload (멱등성 보장 - 같은 키로 여러 번 업로드해도 OK)
        s3_key = f"{tenant_id}/{run_id}/result.json"
        self.s3.upload(
            bucket=RESULT_BUCKET,
            key=s3_key,
            data=result_json,
            metadata={
                "actual-cost-usd-micros": str(actual_cost),
                "run-id": run_id,
                "tenant-id": tenant_id
            }
        )

        logger.info("S3 upload successful", extra={
            "run_id": run_id,
            "s3_key": s3_key,
            "actual_cost": actual_cost
        })

    except Exception as e:
        logger.error("S3 upload failed", exc_info=True)

        # Rollback: CLAIMED → PENDING
        self.repo.rollback_claim(run_id, tenant_id)
        raise  # 재시도 위해 예외 전파
```

#### Step 4: Phase 3 - COMMIT (확정)

```python
# apps/worker/dpp_worker/loops/sqs_loop.py (계속)

    # Phase 3: COMMIT (확정)
    try:
        self.repo.commit_finalize(
            run_id=run_id,
            tenant_id=tenant_id,
            status="COMPLETED",
            actual_cost_usd_micros=actual_cost,
            result_s3_key=s3_key
        )

        logger.info("Finalize committed", extra={
            "run_id": run_id,
            "finalize_stage": "COMMITTED"
        })

        return True  # 성공! SQS 메시지 삭제

    except Exception as e:
        logger.critical("Commit failed - manual intervention needed", exc_info=True)
        # CLAIMED 상태로 남음 → Reaper가 나중에 복구
        return False
```

```python
# apps/worker/dpp_worker/repositories/run_repository.py

def commit_finalize(
    self,
    run_id: str,
    tenant_id: str,
    status: str,
    actual_cost_usd_micros: int,
    result_s3_key: str
) -> None:
    """Phase 3: COMMIT - finalize 확정"""

    self.db.execute(
        text("""
            UPDATE runs
            SET
                finalize_stage = 'COMMITTED',
                status = :status,
                actual_cost_usd_micros = :actual_cost,
                result_s3_key = :s3_key,
                version = version + 1,
                completed_at = NOW(),
                updated_at = NOW()
            WHERE
                run_id = :run_id
                AND tenant_id = :tenant_id
                AND finalize_stage = 'CLAIMED'  -- CLAIMED 상태인 것만
        """),
        {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "status": status,
            "actual_cost": actual_cost_usd_micros,
            "s3_key": result_s3_key
        }
    )

    self.db.commit()
```

---

## 🔄 State Diagram (상태 다이어그램)

```
PENDING (초기 상태)
   ↓
   ├─ [CLAIM 성공] → CLAIMED (예약됨)
   │                    ↓
   │                    ├─ [S3 upload 성공] → EXECUTE 완료
   │                    │                        ↓
   │                    │                        └─ [COMMIT 성공] → COMMITTED (완료)
   │                    │
   │                    └─ [S3 upload 실패] → Rollback → PENDING
   │
   └─ [CLAIM 실패] → PENDING (다른 worker가 처리)
```

### 장애 시나리오별 복구

| 장애 시점 | 상태 | 복구 방법 |
|----------|------|----------|
| CLAIM 전 | PENDING | Worker 재시도 (SQS visibility timeout) |
| CLAIM 후, EXECUTE 전 | CLAIMED | Reaper가 rollback → PENDING |
| EXECUTE 후, COMMIT 전 | CLAIMED | Reaper가 S3 확인 → roll-forward → COMMITTED |
| COMMIT 후 | COMMITTED | 완료 (no action needed) |

---

## 📊 Consequences (결과/장단점)

### ✅ Benefits (장점)

1. **원자성 보장**
   - Database + S3 작업이 논리적으로 원자적
   - "부분 완료" 상태 없음

2. **복구 가능성**
   - 각 단계별 장애 복구 가능
   - Reaper가 CLAIMED 상태 감지 및 처리

3. **동시성 제어**
   - CLAIM 단계에서 충돌 감지 (optimistic locking)
   - 여러 Worker가 동시에 처리해도 안전

4. **관찰성**
   - finalize_stage로 진행 상황 추적 가능
   - "어디서 멈췄나?" 즉시 파악

### ❌ Drawbacks (단점)

1. **성능 오버헤드**
   - DB 호출 2회 (CLAIM + COMMIT)
   - 단일 transaction보다 느림

2. **복잡도 증가**
   - 3단계 로직 구현 필요
   - Rollback 로직 추가 필요

3. **Reaper 의존성**
   - CLAIMED 상태로 stuck되면 Reaper 필요
   - 추가 컴포넌트 운영 필요

---

## 🌍 Known Uses (실제 사용 사례)

### DPP API Platform
```
Component: Worker (SQS → S3 → Database)
Problem: S3 upload 성공 후 DB update 실패 시 money leak
Solution: 2-Phase Commit (CLAIM → S3 upload → COMMIT)
Result: 5/5 Chaos tests passing, Zero money leak
```

### Google Spanner
```
Distributed Database의 트랜잭션 commit
- Prepare phase: 모든 shard에 "준비 완료?" 확인
- Commit phase: 모든 shard에 "커밋하라" 지시
```

### Database Migration Tools (Alembic, Flyway)
```
Schema migration의 안전한 적용
- Lock migration table (CLAIM)
- Execute migration (EXECUTE)
- Update version (COMMIT)
```

---

## 🔗 Related Patterns (관련 패턴)

### Optimistic Locking Pattern
```
관계: 2-Phase Commit의 CLAIM 단계에서 사용
목적: 동시에 여러 Worker가 claim하는 것 방지
```

### Reconciliation Pattern
```
관계: 2-Phase Commit 실패 시 복구 메커니즘
목적: CLAIMED 상태로 stuck된 Run을 자동 복구
```

### Saga Pattern (대안)
```
차이점:
- 2PC: Strong consistency (즉시 확정)
- Saga: Eventual consistency (보상 트랜잭션)

언제 Saga 선택:
- Long-running transaction (수분~수시간)
- Partial failure 허용 가능
```

---

## 💡 Implementation Tips

### Tip 1: EXECUTE 단계는 멱등성 보장
```python
# ✅ 멱등성 있음 (여러 번 실행해도 결과 동일)
s3.put_object(Bucket="...", Key="fixed-key", Body=data)

# ❌ 멱등성 없음 (실행할 때마다 새 키)
s3.put_object(Bucket="...", Key=f"{uuid.uuid4()}", Body=data)
```

### Tip 2: COMMIT 단계는 최대한 단순하게
```python
# ✅ 단순 상태 변경 (실패 확률 낮음)
UPDATE runs SET finalize_stage = 'COMMITTED' WHERE ...

# ❌ 복잡한 로직 (실패 확률 높음)
UPDATE runs SET finalize_stage = 'COMMITTED',
               summary = compute_summary(),  -- 복잡한 계산
               ...
```

### Tip 3: Timeout 설정
```python
# CLAIM 후 30분 이내에 COMMIT 안 되면 Reaper가 복구
CLAIM_TIMEOUT_MINUTES = 30

# Reaper 스캔
stuck_claims = db.query(Run).filter(
    Run.finalize_stage == "CLAIMED",
    Run.updated_at < now() - timedelta(minutes=30)
)
```

---

## 🧪 Testing Strategy

### Unit Test: CLAIM 충돌
```python
def test_claim_race_condition():
    """두 Worker가 동시에 claim하면 하나만 성공"""

    # Worker 1, 2 동시에 claim 시도
    with ThreadPoolExecutor(max_workers=2) as executor:
        future1 = executor.submit(worker1.claim_for_finalize, run_id, version=1)
        future2 = executor.submit(worker2.claim_for_finalize, run_id, version=1)

    # 하나만 True (WINNER), 하나는 False (LOSER)
    results = [future1.result(), future2.result()]
    assert results.count(True) == 1
    assert results.count(False) == 1
```

### Integration Test: S3 실패 시 Rollback
```python
def test_s3_failure_rollback(monkeypatch):
    """S3 upload 실패 시 CLAIMED → PENDING으로 rollback"""

    # S3 upload 실패하도록 mock
    monkeypatch.setattr(s3, "upload", lambda *args, **kwargs: raise_error())

    # Process message
    worker._process_message(message)

    # 검증: finalize_stage가 PENDING으로 롤백됨
    run = db.query(Run).get(run_id)
    assert run.finalize_stage == "PENDING"
```

### Chaos Test: Worker 강제 종료
```python
def test_worker_killed_during_execute():
    """EXECUTE 중 Worker 강제 종료 → Reaper 복구"""

    # 1. CLAIM 성공
    worker.claim_for_finalize(run_id, version=1)

    # 2. S3 upload 성공
    s3.upload(...)

    # 3. Worker 강제 종료 (SIGKILL) - COMMIT 전
    worker.kill()

    # 4. Reaper 실행
    reaper.reconcile_stuck_claims()

    # 5. 검증: S3 존재하므로 roll-forward → COMMITTED
    run = db.query(Run).get(run_id)
    assert run.finalize_stage == "COMMITTED"
    assert run.status == "COMPLETED"
```

---

## 📚 Further Reading

- [Two-Phase Commit Protocol (Wikipedia)](https://en.wikipedia.org/wiki/Two-phase_commit_protocol)
- [Google Spanner: Becoming a SQL System](https://research.google/pubs/pub46103/)
- [Designing Data-Intensive Applications](https://dataintensive.net/) - Chapter 9: Consistency and Consensus

---

**Last Updated**: 2026-02-14
**Version**: 1.0
**Based on**: DPP API Platform v0.4.2.2 (MS-4 Implementation)
