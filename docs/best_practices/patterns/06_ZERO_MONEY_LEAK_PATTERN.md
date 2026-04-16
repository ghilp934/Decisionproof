# Zero Money Leak Architecture
## 금전 정확성 100% 보장 아키텍처 패턴

**Category**: Architectural Pattern
**Complexity**: ⭐⭐⭐⭐ Very High
**Performance Impact**: Medium (Multiple layers of validation)

---

## 📋 Pattern Summary

**Also Known As**: Money Accuracy Pattern, Financial Integrity Architecture

**Intent**: 분산 시스템에서 금전 거래의 정확성을 100% 보장하고, 시스템 장애 시에도 돈이 "사라지거나" "생기지" 않도록 합니다.

**Motivation**: 결제 기반 시스템에서 금전 누수(money leak)는 치명적입니다. 고객에게 청구하지 않거나(손실), 과다 청구하면(신뢰 손실) 비즈니스가 망합니다.

---

## 🎯 Problem (문제)

### 시나리오: AI Agent API 플랫폼

```
1. User가 API 요청 (예상 비용: $1.00)
2. 예산 차감 ($100.00 → $99.00 reserved)
3. Worker가 작업 수행 (실제 비용: $0.87)
4. 결과를 S3에 업로드
5. 예산 정산 ($99.00 + $0.13 refund = $99.13)
```

**문제 시나리오**:

### Scenario 1: Worker Crash (작업 중 장애)
```
1. 예산 차감 ✅ ($100 → $99)
2. Worker 시작 ✅
3. Worker crash! 💥 (SIGKILL)
4. 예산 정산 ❌ (실행 안 됨)

Result: $1.00이 영원히 "RESERVED" 상태 → Money leak!
```

### Scenario 2: S3 Upload 성공 + DB Update 실패
```
1. 예산 차감 ✅ ($100 → $99)
2. 작업 완료 ✅ (실제 비용 $0.87)
3. S3 upload ✅
4. DB update 실패 ❌ (network error)

Result: S3에 결과는 있지만, DB는 "PROCESSING" 상태
        예산 정산 안 됨 → $1.00 leak!
```

### Scenario 3: 이중 정산
```
1. 예산 차감 ✅ ($100 → $99)
2. Worker A 시작 ✅
3. Worker A가 느림 (heartbeat 놓침)
4. Reaper가 Worker A를 "죽었다" 판단
5. Worker B 시작 ✅ (same run!)
6. Worker A 정산 ✅ ($99 + $0.13 = $99.13)
7. Worker B 정산 ✅ ($99.13 + $0.13 = $99.26)

Result: $0.13 이중 refund → Money leak (반대 방향)!
```

---

## 🌍 Context (상황)

이 패턴이 필요한 경우:

```
✅ 금전 거래 시스템
   - 결제 API
   - 크레딧 기반 서비스
   - 청구/과금 시스템

✅ 분산 시스템
   - API + Worker + Background jobs
   - 여러 서비스 간 상태 동기화

✅ 시스템 장애 가능
   - Worker crash
   - Network partition
   - Database failover

✅ 100% 정확성 필수
   - Eventual consistency 불충분
   - Audit trail 필요
```

이 패턴이 **불필요한** 경우:

```
❌ 비금전 시스템 (이메일, 로깅 등)
❌ Approximate accuracy 허용
❌ Single monolithic system (분산 아님)
```

---

## ⚖️ Forces (제약 조건)

다음 요구사항들이 충돌합니다:

1. **정확성**: 100% 금전 정확성 (Zero tolerance)
2. **성능**: 빠른 응답 시간
3. **복잡도**: 간단한 코드
4. **복원력**: 장애 자동 복구

이 패턴은 **정확성을 최우선**하고, 나머지를 희생합니다.

---

## ✅ Solution (해결책)

### 핵심 아이디어: 3-Tier Protection

```
Tier 1: Reservation (예약)
   - 요청 시 즉시 예산 예약
   - Redis에 기록 (fast, durable)

Tier 2: Settlement (정산)
   - 작업 완료 후 실제 비용 정산
   - Database에 기록 (transactional)

Tier 3: Reconciliation (대사)
   - 주기적으로 불일치 탐지
   - S3 결과와 DB 상태 비교
   - AUDIT_REQUIRED 알람 (수동 개입)
```

### 불변 조건 (Invariant)

```python
# 항상 성립해야 하는 수식
DB_balance = Initial_balance - SUM(reservations) - SUM(settled_amounts)

# 또는
Initial_balance = DB_balance + SUM(reservations) + SUM(settled_amounts)

# 돈은 보존된다 (Conservation Law)
```

---

## 🛠️ Implementation (구현)

### DPP 프로젝트 실제 코드

#### Tier 1: Reservation (예약)

```python
# apps/api/dpp_api/budget/budget_manager.py

class BudgetManager:
    def reserve_budget(
        self,
        tenant_id: str,
        run_id: str,
        amount_usd_micros: int
    ) -> None:
        """
        Tier 1: 예산 예약 (Redis)

        Args:
            tenant_id: Tenant ID
            run_id: Run ID
            amount_usd_micros: 예약 금액 (microdollars)

        Raises:
            InsufficientBudgetError: 잔액 부족
        """

        balance_key = f"budget:balance:{tenant_id}"
        reservation_key = f"budget:reservation:{run_id}"

        # 현재 잔액 조회
        current_balance = int(self.redis.get(balance_key) or 0)

        # 예약된 금액 합계
        reserved_total = self._get_reserved_total(tenant_id)

        # 사용 가능 잔액
        available = current_balance - reserved_total

        # 잔액 부족 체크
        if available < amount_usd_micros:
            raise InsufficientBudgetError(
                f"Available: ${available/1_000_000:.2f}, "
                f"Required: ${amount_usd_micros/1_000_000:.2f}"
            )

        # 예약 기록 (Redis)
        self.redis.set(
            reservation_key,
            amount_usd_micros,
            ex=86400  # 24시간 TTL (stuck run 방지)
        )

        logger.info("Budget reserved", extra={
            "tenant_id": tenant_id,
            "run_id": run_id,
            "amount_usd_micros": amount_usd_micros,
            "available_before": available
        })
```

#### Tier 2: Settlement (정산)

```python
# apps/worker/dpp_worker/budget/budget_settler.py

class BudgetSettler:
    def settle_budget(
        self,
        tenant_id: str,
        run_id: str,
        actual_cost_usd_micros: int
    ) -> None:
        """
        Tier 2: 예산 정산 (Database transaction)

        Args:
            tenant_id: Tenant ID
            run_id: Run ID
            actual_cost_usd_micros: 실제 비용 (microdollars)
        """

        reservation_key = f"budget:reservation:{run_id}"
        balance_key = f"budget:balance:{tenant_id}"

        # 1. 예약 금액 조회
        reserved_amount = int(self.redis.get(reservation_key) or 0)

        # 2. Refund 계산
        refund_amount = reserved_amount - actual_cost_usd_micros

        # 3. Database transaction (원자적)
        with self.db.begin():
            # 3a. Balance 업데이트 (refund)
            self.db.execute(
                text("""
                    UPDATE budgets
                    SET balance_usd_micros = balance_usd_micros + :refund
                    WHERE tenant_id = :tenant_id
                """),
                {"refund": refund_amount, "tenant_id": tenant_id}
            )

            # 3b. Settlement 기록 (불변 원장)
            self.db.execute(
                text("""
                    INSERT INTO budget_settlements (
                        tenant_id, run_id,
                        reserved_amount, actual_cost, refund_amount,
                        settled_at
                    ) VALUES (
                        :tenant_id, :run_id,
                        :reserved, :actual, :refund,
                        NOW()
                    )
                """),
                {
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "reserved": reserved_amount,
                    "actual": actual_cost_usd_micros,
                    "refund": refund_amount
                }
            )

        # 4. Redis 예약 삭제
        self.redis.delete(reservation_key)

        # 5. Redis 잔액 업데이트 (cache)
        new_balance = int(self.redis.get(balance_key)) + refund_amount
        self.redis.set(balance_key, new_balance)

        logger.info("Budget settled", extra={
            "tenant_id": tenant_id,
            "run_id": run_id,
            "reserved": reserved_amount,
            "actual": actual_cost_usd_micros,
            "refund": refund_amount
        })
```

#### Tier 3: Reconciliation (대사)

```python
# apps/reaper/dpp_reaper/reconcile/reconciler.py

class Reconciler:
    def reconcile_money_leak(self) -> None:
        """
        Tier 3: 대사 루프 (불일치 탐지)

        Runs every 60 seconds
        """

        # 1. "PROCESSING" 상태이지만 완료된 지 5분 이상인 Run 조회
        threshold = datetime.utcnow() - timedelta(minutes=5)

        stuck_runs = self.db.query(Run).filter(
            Run.status == "PROCESSING",
            Run.started_at < threshold
        ).limit(100).all()

        for run in stuck_runs:
            self._reconcile_run(run)

    def _reconcile_run(self, run: Run) -> None:
        """개별 Run 대사"""

        # 2. S3에 결과 존재 여부 확인
        s3_exists = self.s3.head_object(
            Bucket=RESULT_BUCKET,
            Key=f"{run.tenant_id}/{run.run_id}/result.json"
        )

        # 3. Redis 예약 존재 여부
        reservation_key = f"budget:reservation:{run.run_id}"
        reservation_exists = self.redis.exists(reservation_key)

        # 4. 결정 로직
        if s3_exists and not run.result_s3_key:
            # Roll-forward: S3 있음 → 완료 처리
            logger.warning("RECONCILE: Roll-forward", extra={
                "run_id": run.run_id,
                "reason": "S3 exists but DB not updated"
            })

            self._roll_forward(run, s3_key)

        elif not s3_exists and reservation_exists:
            # Roll-back: S3 없음 + 예약 있음 → 실패 처리
            logger.warning("RECONCILE: Roll-back", extra={
                "run_id": run.run_id,
                "reason": "No S3 result but budget reserved"
            })

            self._roll_back(run)

        elif not s3_exists and not reservation_exists:
            # AUDIT_REQUIRED: 예약도 없고 결과도 없음 → 돈 사라짐!
            logger.critical("AUDIT_REQUIRED: No reservation AND no result", extra={
                "run_id": run.run_id,
                "tenant_id": run.tenant_id,
                "status": run.status
            })

            # PagerDuty alert
            self.alerting.send_critical_alert(
                title="Money Leak Suspected",
                description=f"Run {run.run_id} has no reservation AND no S3 result",
                severity="CRITICAL"
            )

    def _roll_forward(self, run: Run, s3_key: str) -> None:
        """Roll-forward: S3 결과 기반으로 완료 처리"""

        # S3 metadata에서 actual cost 읽기
        metadata = self.s3.head_object(...)["Metadata"]
        actual_cost = int(metadata["actual-cost-usd-micros"])

        # 2-phase commit으로 안전하게 finalize
        self.repo.claim_for_finalize(...)
        self.settler.settle_budget(run.tenant_id, run.run_id, actual_cost)
        self.repo.commit_finalize(
            run_id=run.run_id,
            status="COMPLETED",
            actual_cost_usd_micros=actual_cost,
            result_s3_key=s3_key
        )

    def _roll_back(self, run: Run) -> None:
        """Roll-back: 예약 취소 + 실패 처리"""

        reservation_key = f"budget:reservation:{run.run_id}"
        reserved_amount = int(self.redis.get(reservation_key) or 0)

        # 예약 취소 (refund)
        balance_key = f"budget:balance:{run.tenant_id}"
        self.redis.incrby(balance_key, reserved_amount)  # Refund
        self.redis.delete(reservation_key)

        # Run 상태 업데이트
        self.repo.update(run.run_id, status="FAILED", error="Worker crashed")

        logger.info("Rolled back run", extra={
            "run_id": run.run_id,
            "refund": reserved_amount
        })
```

---

## 📊 Architecture Diagram (아키텍처 다이어그램)

```
User Request
    ↓
┌─────────────────────────────────────────────────┐
│ API Server                                       │
│                                                  │
│  1. Reserve Budget (Redis)                      │
│     budget:reservation:{run_id} = $1.00         │
│     budget:balance:{tenant} -= $1.00            │
│                                                  │
│  2. Enqueue SQS                                 │
└──────────────────┬──────────────────────────────┘
                   ↓ SQS Message
┌─────────────────────────────────────────────────┐
│ Worker                                           │
│                                                  │
│  3. Execute Pack (actual cost: $0.87)           │
│                                                  │
│  4. Upload Result to S3                         │
│     + metadata: actual-cost=$0.87               │
│                                                  │
│  5. Settle Budget (Database transaction)        │
│     - Delete reservation                        │
│     - Refund: $1.00 - $0.87 = $0.13            │
│     - DB balance += $0.13                       │
│     - Insert settlement record                  │
│                                                  │
│  6. Update Run status = "COMPLETED"             │
└──────────────────┬──────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────┐
│ Reaper (Every 60 seconds)                       │
│                                                  │
│  7. Scan stuck runs (PROCESSING > 5 min)       │
│                                                  │
│  8. Check S3 + Redis reservation                │
│                                                  │
│  9. Reconcile:                                  │
│     - S3 exists? → Roll-forward                │
│     - S3 missing? → Roll-back                  │
│     - Both missing? → AUDIT_REQUIRED 🚨        │
└─────────────────────────────────────────────────┘
```

---

## 🧪 Chaos Testing (검증)

### Test 1: Worker Crash After Reservation

```python
def test_worker_crash_after_reservation():
    """예산 예약 후 Worker crash → Reaper가 roll-back"""

    # 1. 초기 잔액
    initial_balance = 100_000_000  # $100.00

    # 2. 예산 예약
    budget_manager.reserve_budget(tenant_id, run_id, 1_000_000)  # $1.00

    # 3. Worker crash (SIGKILL) - S3 upload 전
    worker.kill()

    # 4. Reaper 실행
    time.sleep(65)  # Reconcile loop 실행 대기
    reconciler.reconcile_money_leak()

    # 5. 검증: 예약 취소됨 (refund)
    final_balance = budget_repo.get_balance(tenant_id)
    assert final_balance == initial_balance  # $100.00

    # 6. 검증: Run 상태 "FAILED"
    run = run_repo.get_by_id(run_id)
    assert run.status == "FAILED"
```

### Test 2: S3 Upload 성공 + DB Update 실패

```python
def test_s3_success_db_failure():
    """S3 upload 성공, DB update 실패 → Reaper가 roll-forward"""

    # 1. 예산 예약
    budget_manager.reserve_budget(tenant_id, run_id, 1_000_000)

    # 2. S3 upload 성공
    s3.put_object(
        Bucket=RESULT_BUCKET,
        Key=f"{tenant_id}/{run_id}/result.json",
        Metadata={"actual-cost-usd-micros": "870000"}  # $0.87
    )

    # 3. DB update 실패 (simulate network error)
    # Run은 여전히 "PROCESSING" 상태

    # 4. Reaper 실행
    time.sleep(65)
    reconciler.reconcile_money_leak()

    # 5. 검증: Run 상태 "COMPLETED" (roll-forward)
    run = run_repo.get_by_id(run_id)
    assert run.status == "COMPLETED"
    assert run.actual_cost_usd_micros == 870000

    # 6. 검증: 예산 정산됨
    final_balance = budget_repo.get_balance(tenant_id)
    assert final_balance == initial_balance - 870000  # Refund: $0.13
```

### Test 3: 이중 Worker (Heartbeat 놓침)

```python
def test_double_worker_prevention():
    """Heartbeat 놓침 → Reaper가 재시작 → 이중 정산 방지"""

    # 1. Worker A 시작
    worker_a.process_run(run_id)

    # 2. Worker A가 느림 (heartbeat 놓침)
    time.sleep(130)  # Lease 만료 (120s)

    # 3. Reaper가 Worker B 시작
    reaper.reap_expired_leases()

    # 4. Worker A finalize 시도 (늦게 완료)
    success_a = worker_a.claim_for_finalize(run_id, version=run.version)

    # 5. Worker B finalize 시도
    success_b = worker_b.claim_for_finalize(run_id, version=run.version)

    # 6. 검증: 하나만 성공 (optimistic locking)
    assert (success_a and not success_b) or (not success_a and success_b)

    # 7. 검증: 정산 1회만
    settlements = settlement_repo.get_by_run(run_id)
    assert len(settlements) == 1
```

### Test 4: AUDIT_REQUIRED Alert

```python
def test_audit_required_alert():
    """예약도 없고 S3도 없음 → AUDIT_REQUIRED"""

    # 1. Run 생성 (PROCESSING)
    run = create_run(status="PROCESSING")

    # 2. 예약 없음 (Redis empty)
    # 3. S3 결과 없음

    # 4. Reaper 실행
    reconciler.reconcile_money_leak()

    # 5. 검증: CRITICAL 로그 생성
    logs = get_critical_logs()
    assert any("AUDIT_REQUIRED" in log for log in logs)
    assert any(run.run_id in log for log in logs)

    # 6. 검증: PagerDuty alert 전송됨
    alerts = pagerduty.get_alerts()
    assert len(alerts) == 1
    assert "Money Leak Suspected" in alerts[0].title
```

### Test 5: Invariant Verification

```python
def test_money_conservation_law():
    """불변 조건 검증: Initial = DB + Reservations + Settled"""

    initial_balance = 100_000_000  # $100.00

    # 여러 run 생성
    run_ids = []
    for i in range(10):
        run_id = create_run()
        budget_manager.reserve_budget(tenant_id, run_id, 1_000_000)
        run_ids.append(run_id)

    # 일부 완료, 일부 실패, 일부 진행 중
    for i, run_id in enumerate(run_ids):
        if i < 5:
            # 완료
            settler.settle_budget(tenant_id, run_id, 870000)
        elif i < 8:
            # 실패 (roll-back)
            reconciler._roll_back(...)
        # 나머지 3개는 진행 중 (reservation 유지)

    # 검증: 불변 조건
    db_balance = budget_repo.get_balance(tenant_id)
    reservations = budget_manager.get_total_reserved(tenant_id)
    settled = settlement_repo.get_total_settled(tenant_id)

    assert db_balance + reservations + settled == initial_balance
```

---

## 📊 Consequences (결과/장단점)

### ✅ Benefits (장점)

1. **100% 금전 정확성**
   - 5/5 Chaos tests passing
   - 불변 조건 항상 성립
   - Money leak 0건

2. **장애 복원력**
   - Worker crash 자동 복구
   - Network partition 대응
   - 이중 처리 방지

3. **Audit Trail**
   - 모든 거래 기록 (immutable log)
   - 불일치 즉시 감지
   - CRITICAL alert

4. **확장성**
   - Worker 수 증가 가능
   - Auto-scaling 지원

### ❌ Drawbacks (단점)

1. **복잡도 매우 높음**
   - 3-tier architecture
   - Reconciliation loop
   - 2-phase commit

2. **성능 오버헤드**
   - Redis + DB writes
   - S3 metadata read
   - Reconcile scan

3. **운영 부담**
   - Reaper 모니터링 필요
   - AUDIT_REQUIRED 대응 필요
   - 3개 서비스 운영 (API, Worker, Reaper)

---

## 💡 Key Takeaways

### 1. 돈은 절대 사라지지 않는다
```
Conservation Law: Initial = DB + Reservations + Settled
```

### 2. 3-Tier Defense-in-Depth
```
Tier 1: Prevent (Reservation)
Tier 2: Detect (Settlement)
Tier 3: Recover (Reconciliation)
```

### 3. AUDIT_REQUIRED는 마지막 방어선
```
자동 복구 불가능한 경우 → 수동 개입
PagerDuty alert → On-call engineer → Manual fix
```

### 4. S3 Metadata is Source of Truth
```
S3에 actual-cost-usd-micros 저장
→ Reconcile 시 이 값으로 정산
```

---

## 📚 Further Reading

- [Designing Data-Intensive Applications](https://dataintensive.net/) - Chapter 12: The Future of Data Systems
- [Building Microservices](https://samnewman.io/books/building_microservices_2nd_edition/) - Chapter 8: Resilience
- [Stripe's Idempotency System](https://stripe.com/blog/idempotency)
- [Two-Phase Commit in Distributed Systems](https://en.wikipedia.org/wiki/Two-phase_commit_protocol)

---

**Last Updated**: 2026-02-14
**Version**: 1.0
**Based on**: DPP API Platform v0.4.2.2 (MS-4, MS-5, P0-1 Critical Feedback)
**Chaos Tests**: 5/5 Passing ✅
