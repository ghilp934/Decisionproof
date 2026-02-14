# Design Patterns Catalog
## DPP 프로젝트에서 검증된 재사용 가능한 패턴 모음

**목적**: 실전에서 검증된 디자인 패턴을 카탈로그화하여, 유사한 문제에 즉시 적용 가능한 솔루션을 제공합니다.

**기반**: DPP API Platform v0.4.2.2 프로젝트 경험

---

## 📚 Pattern Index

### Architectural Patterns (아키텍처 패턴)
1. **[2-Phase Commit Pattern](02_2PHASE_COMMIT_PATTERN.md)** - 분산 트랜잭션 원자성 보장
2. **[Zero Money Leak Architecture](06_ZERO_MONEY_LEAK_PATTERN.md)** - 금전 정확성 100% 보장
3. **[Optimistic Locking Pattern](03_OPTIMISTIC_LOCKING_PATTERN.md)** - 동시성 제어 (낙관적 잠금)

### Concurrency Patterns (동시성 패턴)
4. **[Session Factory Pattern](04_SESSION_FACTORY_PATTERN.md)** - Thread-Safe Database Session
5. **[Atomic Operations Pattern](05_ATOMIC_OPERATIONS_PATTERN.md)** - Race Condition 방지

### Observability Patterns (관찰성 패턴)
6. **Trace Propagation Pattern** - End-to-End 추적성
7. **Critical Alert Pattern** - 즉각 대응 필요 이벤트

### Resilience Patterns (복원력 패턴)
8. **Chaos Testing Pattern** - 장애 주입 테스트
9. **Reconciliation Pattern** - 상태 불일치 자동 복구

---

## 🎯 패턴 선택 가이드

### Use Case: 금전 거래 시스템
```
필수 패턴:
✅ 2-Phase Commit Pattern (거래 원자성)
✅ Zero Money Leak Architecture (정확성)
✅ Optimistic Locking Pattern (동시성)
✅ Reconciliation Pattern (복구)

선택 패턴:
⚪ Chaos Testing Pattern (높은 신뢰성 요구 시)
```

### Use Case: 분산 워크플로 시스템
```
필수 패턴:
✅ Trace Propagation Pattern (디버깅)
✅ Session Factory Pattern (멀티스레드)

선택 패턴:
⚪ 2-Phase Commit Pattern (강한 일관성 필요 시)
⚪ Reconciliation Pattern (약한 일관성 허용 시)
```

### Use Case: 고성능 API 서버
```
필수 패턴:
✅ Atomic Operations Pattern (Rate limiting)
✅ Optimistic Locking Pattern (리소스 경쟁)

선택 패턴:
⚪ Session Factory Pattern (백그라운드 스레드 사용 시)
```

---

## 📊 패턴 비교표

| Pattern | Problem | Solution | Complexity | Performance Impact |
|---------|---------|----------|------------|-------------------|
| 2-Phase Commit | 분산 트랜잭션 원자성 | Claim → Upload → Commit | ⭐⭐⭐ High | 약간 느림 (2 DB calls) |
| Optimistic Locking | 동시 업데이트 충돌 | Version column + retry | ⭐⭐ Medium | 거의 없음 |
| Session Factory | 스레드 간 세션 공유 | Factory pattern | ⭐ Low | 없음 |
| Atomic Operations | Race condition | INCR-first | ⭐ Low | 없음 (Redis) |
| Zero Money Leak | 금전 누수 | Reservation + Settlement | ⭐⭐⭐⭐ Very High | 중간 (Redis + S3) |
| Trace Propagation | 분산 추적 | trace_id 전파 | ⭐ Low | 거의 없음 |
| Reconciliation | 상태 불일치 | 주기적 스캔 + 복구 | ⭐⭐⭐ High | 낮음 (백그라운드) |
| Chaos Testing | 장애 시뮬레이션 | 테스트 프레임워크 | ⭐⭐ Medium | N/A (테스트) |

---

## 🚀 Quick Start: 패턴 적용 순서

### Step 1: 핵심 문제 식별
```
질문: 우리 시스템의 핵심 위험은?
  - 금전 누수? → Zero Money Leak Architecture
  - 동시성 버그? → Optimistic Locking + Atomic Operations
  - 디버깅 어려움? → Trace Propagation
  - 시스템 장애? → Reconciliation + Chaos Testing
```

### Step 2: 패턴 조합 결정
```
예시: 결제 기반 AI API 플랫폼 (DPP)
  1. Zero Money Leak Architecture (핵심)
  2. 2-Phase Commit Pattern (거래 원자성)
  3. Optimistic Locking Pattern (동시성)
  4. Reconciliation Pattern (복구)
  5. Session Factory Pattern (멀티스레드)
  6. Atomic Operations Pattern (Rate limiting)
  7. Trace Propagation Pattern (디버깅)
  8. Chaos Testing Pattern (검증)
```

### Step 3: 우선순위 결정
```
Phase 1 (MVP):
  - Optimistic Locking
  - Trace Propagation

Phase 2 (Production Ready):
  - 2-Phase Commit
  - Zero Money Leak Architecture
  - Reconciliation
  - Session Factory
  - Atomic Operations

Phase 3 (Production Hardening):
  - Chaos Testing
  - Critical Alert
```

---

## 💡 패턴별 핵심 개념 (Quick Reference)

### 1. 2-Phase Commit Pattern
```python
# Phase 1: Claim (예약)
run.finalize_stage = "CLAIMED"
run.version += 1

# Phase 2: Execute (실행)
s3.upload(result)

# Phase 3: Commit (확정)
run.finalize_stage = "COMMITTED"
run.version += 1
```
**핵심**: 작업을 여러 단계로 나누어 각 단계가 원자적으로 완료되도록 보장

---

### 2. Optimistic Locking Pattern
```python
# 버전 체크와 동시에 업데이트
UPDATE runs
SET status = 'COMPLETED', version = version + 1
WHERE run_id = '...' AND version = 5  # 현재 버전이 5일 때만

# affected_rows == 0 이면 다른 프로세스가 먼저 업데이트함
```
**핵심**: Version column으로 stale update 방지

---

### 3. Session Factory Pattern
```python
# ❌ 잘못된 방법 (스레드 간 세션 공유)
def __init__(self, db_session: Session):
    self.db_session = db_session  # 위험!

# ✅ 올바른 방법 (각 스레드가 새 세션 생성)
def __init__(self, session_factory: Callable[[], Session]):
    self.session_factory = session_factory

def run(self):
    with self.session_factory() as session:
        # 이 스레드만의 세션
```
**핵심**: 스레드마다 독립적인 세션 사용

---

### 4. Atomic Operations Pattern
```python
# ❌ 잘못된 방법 (GET → Compare → INCR)
count = redis.get(key)
if count < limit:
    redis.incr(key)  # Race condition!

# ✅ 올바른 방법 (INCR-first)
new_count = redis.incr(key)
if new_count == 1:
    redis.expire(key, 60)
if new_count > limit:
    redis.decr(key)
    raise RateLimitError()
```
**핵심**: Redis INCR은 원자적 연산, GET → INCR은 아님

---

### 5. Zero Money Leak Architecture
```python
# 3-tier protection
1. Reservation (예약): Redis에 기록
2. Settlement (정산): DB에 차감
3. Reconciliation (대사): 불일치 탐지

# Invariant (불변 조건)
DB Balance = Initial - SUM(Reservations) - SUM(Settled)
```
**핵심**: 돈은 절대 사라지지도, 생기지도 않음 (보존 법칙)

---

### 6. Trace Propagation Pattern
```python
# API 진입점
trace_id = str(uuid.uuid4())
logger.info("Request received", extra={"trace_id": trace_id})

# SQS 메시지
sqs.send_message(Body=json.dumps({"trace_id": trace_id, ...}))

# Worker
logger.info("Processing run", extra={"trace_id": msg["trace_id"]})

# Reaper
logger.info("Reconciling run", extra={"trace_id": run.trace_id})
```
**핵심**: 모든 로그에 동일한 trace_id 포함 → 전체 흐름 추적 가능

---

### 7. Reconciliation Pattern
```python
# 주기적 스캔 (30초마다)
stuck_runs = db.query(Run).filter(
    Run.status == "PROCESSING",
    Run.lease_expires_at < now()
)

# 정합성 검증
for run in stuck_runs:
    if s3_exists(run):
        # Roll-forward (결과 있음 → 완료 처리)
        run.status = "COMPLETED"
    else:
        # Roll-back (결과 없음 → 실패 처리)
        run.status = "FAILED"
```
**핵심**: 시스템 상태와 실제 상태 불일치를 주기적으로 탐지 및 복구

---

### 8. Chaos Testing Pattern
```python
# 장애 주입 테스트
def test_money_leak_chaos():
    # 1. 정상 실행
    # 2. Worker 강제 종료 (SIGKILL)
    # 3. Reaper 실행
    # 4. 검증: 잔액 일치하는지

    assert initial_balance - actual_cost == final_balance
```
**핵심**: 최악의 시나리오를 테스트로 재현 → 신뢰성 검증

---

## 🔗 패턴 간 관계도

```
Zero Money Leak Architecture (최상위 목표)
    ├─ 2-Phase Commit Pattern (거래 원자성)
    │   └─ Optimistic Locking Pattern (동시성 제어)
    │
    ├─ Reconciliation Pattern (복구)
    │   └─ Trace Propagation Pattern (디버깅)
    │
    └─ Chaos Testing Pattern (검증)
        ├─ Session Factory Pattern (멀티스레드 안전)
        └─ Atomic Operations Pattern (Race condition 방지)
```

---

## 📖 상세 문서 링크

### Architectural Patterns
- **[2-Phase Commit Pattern](02_2PHASE_COMMIT_PATTERN.md)** - 분산 트랜잭션 원자성
- **[Optimistic Locking Pattern](03_OPTIMISTIC_LOCKING_PATTERN.md)** - 동시성 제어
- **[Zero Money Leak Architecture](06_ZERO_MONEY_LEAK_PATTERN.md)** - 금전 정확성

### Concurrency Patterns
- **[Session Factory Pattern](04_SESSION_FACTORY_PATTERN.md)** - Thread-Safe Session
- **[Atomic Operations Pattern](05_ATOMIC_OPERATIONS_PATTERN.md)** - Race Condition 방지

### 기타 패턴 (Phase 3에서 추가 예정)
- Trace Propagation Pattern
- Critical Alert Pattern
- Chaos Testing Pattern
- Reconciliation Pattern

---

## 🎯 패턴 적용 체크리스트

새 프로젝트 시작 시:

```
[ ] 핵심 위험 식별 (금전, 동시성, 장애 등)
[ ] 적용할 패턴 선택 (2-3개 핵심 패턴)
[ ] 패턴 조합 검증 (상호 충돌 없는지)
[ ] 우선순위 결정 (MVP vs Production Ready)
[ ] 패턴별 상세 문서 읽기
[ ] 코드베이스에 적용
[ ] 테스트로 검증 (특히 Chaos Testing)
```

---

## 💡 Anti-Patterns (피해야 할 것)

### ❌ "일단 만들고 나중에 패턴 적용"
```
문제: 나중은 없습니다. Refactoring 비용 >> 초기 설계 비용
해결: 설계 단계에서 패턴 선택 (1시간 투자로 1주일 절약)
```

### ❌ "모든 패턴 다 적용"
```
문제: Over-engineering, 복잡도 증가
해결: 핵심 위험 2-3개만 식별 → 관련 패턴만 적용
```

### ❌ "패턴 문서만 읽고 안 써봄"
```
문제: 이해했다고 착각, 실제로 못 씀
해결: 작은 프로토타입으로 직접 구현 → 체화
```

### ❌ "팀원과 패턴 공유 안 함"
```
문제: 일관성 없는 코드베이스
해결: 패턴 카탈로그 문서화 + 코드 리뷰 시 참조
```

---

## 🏆 Success Stories (실전 적용 사례)

### DPP API Platform v0.4.2.2
```
Challenge: AI Agent 결제 API 플랫폼 구축
  - 요구사항: Zero Money Leak (금전 누수 절대 불가)
  - 복잡도: 분산 시스템 (API, Worker, Reaper)

Applied Patterns:
  ✅ Zero Money Leak Architecture
  ✅ 2-Phase Commit Pattern
  ✅ Optimistic Locking Pattern
  ✅ Reconciliation Pattern
  ✅ Session Factory Pattern (P0-1)
  ✅ Atomic Operations Pattern (P1-1)
  ✅ Trace Propagation Pattern
  ✅ Chaos Testing Pattern

Results:
  ✅ 133 tests passing (100% success rate)
  ✅ 5/5 Chaos tests passing (Zero money leak)
  ✅ Production Ready in 6 milestones
  ✅ 0 critical bugs in production
```

---

## 📚 Further Reading

### Books
- "Enterprise Integration Patterns" by Gregor Hohpe
- "Designing Data-Intensive Applications" by Martin Kleppmann
- "Patterns of Enterprise Application Architecture" by Martin Fowler

### Online Resources
- [Microservices Patterns](https://microservices.io/patterns/)
- [Cloud Design Patterns](https://docs.microsoft.com/azure/architecture/patterns/)
- [AWS Well-Architected Framework](https://aws.amazon.com/architecture/well-architected/)

---

**Last Updated**: 2026-02-14
**Version**: 1.0
**Based on**: DPP API Platform v0.4.2.2 Project Experience
