# Atomic Operations Pattern
## INCR-First Pattern for Race Condition-Free Rate Limiting

**Category**: Concurrency Pattern
**Complexity**: ⭐ Low
**Performance Impact**: 없음 (Redis 기반)

---

## 📋 Pattern Summary

**Also Known As**: INCR-First, Atomic Counter

**Intent**: Redis의 원자적 연산(INCR)을 활용하여, GET → Compare → SET 패턴의 race condition을 제거합니다.

**Motivation**: Rate limiting, quota 관리 등에서 동시 요청 시 정확한 카운팅이 필요합니다. GET → Compare → INCR 패턴은 race condition에 취약합니다.

---

## 🎯 Problem (문제)

### 시나리오: API Rate Limiting (10 requests/min)

```python
# ❌ 잘못된 코드 (GET → Compare → INCR)
def check_rate_limit(api_key: str) -> bool:
    rate_key = f"rate:{api_key}:minute"

    # 1. GET: 현재 카운트 조회
    current_count = redis.get(rate_key)
    if current_count is None:
        current_count = 0
    else:
        current_count = int(current_count)

    # 2. Compare: 제한 확인
    if current_count >= 10:
        raise RateLimitExceeded()

    # 3. INCR: 카운트 증가
    redis.incr(rate_key)
    redis.expire(rate_key, 60)  # 60초 TTL

    return True
```

**문제점 (Race Condition)**:

```
Time  | Thread A              | Thread B              | Redis Value
------|----------------------|----------------------|------------
00:00 | GET → 9              |                       | 9
00:01 |                       | GET → 9              | 9
00:02 | Compare: 9 < 10 ✅   |                       | 9
00:03 |                       | Compare: 9 < 10 ✅   | 9
00:04 | INCR → 10            |                       | 10
00:05 |                       | INCR → 11            | 11 ← 제한 초과!
```

**결과**: 10개 제한인데 11개 통과 (rate limit bypass!)

---

## 🌍 Context (상황)

이 패턴이 필요한 경우:

```
✅ 동시 요청이 많은 환경
   - API rate limiting
   - Quota management
   - Concurrent counter

✅ 정확한 카운팅 필수
   - 금전 관련 (quota)
   - SLA 보장 (rate limit)

✅ Redis 사용 가능
   - Atomic operations 지원
```

이 패턴이 **불필요한** 경우:

```
❌ Single-threaded application
❌ Approximate counting 허용 (정확도 불필요)
❌ Redis 없음
```

---

## ⚖️ Forces (제약 조건)

다음 요구사항들이 충돌합니다:

1. **정확성**: Race condition 절대 방지
2. **성능**: Redis 왕복 최소화
3. **복잡도**: 간단한 코드
4. **확장성**: 동시 요청 수 증가해도 안정적

이 패턴은 **정확성과 성능을 동시에** 달성합니다.

---

## ✅ Solution (해결책)

### 핵심 아이디어

GET → Compare → INCR 순서를 **INCR → Compare**로 변경합니다.

```python
# ✅ 올바른 코드 (INCR-First)
def check_rate_limit(api_key: str) -> bool:
    rate_key = f"rate:{api_key}:minute"

    # 1. INCR: 먼저 증가 (원자적)
    new_count = redis.incr(rate_key)

    # 2. TTL 설정 (첫 번째 요청만)
    if new_count == 1:
        redis.expire(rate_key, 60)

    # 3. Compare: 제한 확인
    if new_count > 10:
        # 제한 초과 시 되돌리기
        redis.decr(rate_key)
        raise RateLimitExceeded()

    return True
```

**핵심 규칙**:
1. **INCR을 먼저** 실행 (원자적 증가)
2. new_count == 1일 때만 TTL 설정
3. 제한 초과 시 DECR로 되돌리기

---

## 🛠️ Implementation (구현)

### DPP 프로젝트 실제 코드

#### Step 1: PlanEnforcer with INCR-First

```python
# apps/api/dpp_api/enforce/plan_enforcer.py

class PlanEnforcer:
    def __init__(self, redis_client: Redis):
        self.redis = redis_client

    def check_rate_limit(self, tenant_id: str, plan_limits: dict) -> None:
        """
        Rate limiting with INCR-first pattern (P1-1)

        Args:
            tenant_id: Tenant ID
            plan_limits: {"rate_limit": 10}  # 10 req/min

        Raises:
            PlanViolationError: Rate limit exceeded
        """

        rate_limit = plan_limits.get("rate_limit")
        if rate_limit is None:
            return  # No rate limit

        rate_key = f"rate:{tenant_id}:minute"

        # Phase 1: INCR-First (원자적)
        new_count = self.redis.incr(rate_key)

        # Phase 2: TTL 설정 (첫 번째 요청만)
        if new_count == 1:
            self.redis.expire(rate_key, 60)  # 60초 TTL

        # Phase 3: Limit 체크
        if new_count > rate_limit:
            # 제한 초과 - 되돌리기
            self.redis.decr(rate_key)

            # TTL 가져오기 (retry_after 계산)
            ttl = self.redis.ttl(rate_key)
            retry_after = max(1, ttl)  # 최소 1초

            raise PlanViolationError(
                status_code=429,
                title="Rate Limit Exceeded",
                detail=f"Rate limit of {rate_limit} requests/minute exceeded",
                retry_after=retry_after  # P1-2: Type-safe retry_after
            )

        logger.info("Rate limit check passed", extra={
            "tenant_id": tenant_id,
            "new_count": new_count,
            "rate_limit": rate_limit
        })
```

#### Step 2: PlanViolationError with retry_after Field

```python
# apps/api/dpp_api/enforce/plan_enforcer.py

class PlanViolationError(HTTPException):
    """P1-2: Type-safe retry_after field"""

    def __init__(
        self,
        status_code: int,
        title: str,
        detail: str,
        retry_after: int | None = None  # ← Type-safe field
    ):
        super().__init__(status_code=status_code, detail=detail)
        self.title = title
        self.retry_after = retry_after  # Seconds
```

#### Step 3: FastAPI Exception Handler with Retry-After Header

```python
# apps/api/dpp_api/main.py

@app.exception_handler(PlanViolationError)
async def plan_violation_handler(request: Request, exc: PlanViolationError):
    """P1-2: No regex parsing, just use exc.retry_after"""

    headers = {}

    # Retry-After header 추가 (P1-2)
    if exc.status_code == 429 and exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)  # No regex!

    # RFC 9457 Problem Details
    return JSONResponse(
        status_code=exc.status_code,
        headers=headers,
        content={
            "type": f"/errors/{exc.status_code}",
            "title": exc.title,
            "detail": exc.detail,
            "status": exc.status_code
        }
    )
```

---

## 🔄 Sequence Diagram (시퀀스 다이어그램)

### INCR-First Pattern 흐름

```
Client A           Client B           Redis
   |                  |                  |
   | INCR rate:X      |                  |
   |----------------->|                  | (원자적 증가)
   |    new_count=9   |                  |
   |<-----------------|                  |
   |                  | INCR rate:X      |
   |                  |----------------->| (원자적 증가)
   |                  |    new_count=10  |
   |                  |<-----------------|
   |                  |                  |
   | 9 <= 10 ✅       |                  |
   | Success          |                  |
   |                  | 10 <= 10 ✅      |
   |                  | Success          |
   |                  |                  |

   (11번째 요청)
   |                  | INCR rate:X      |
   |                  |----------------->|
   |                  |    new_count=11  | ← 제한 초과!
   |                  |<-----------------|
   |                  |                  |
   |                  | DECR rate:X      | (되돌리기)
   |                  |----------------->|
   |                  |    new_count=10  |
   |                  |<-----------------|
   |                  |                  |
   |                  | 429 Rate Limit   |
   |                  |<-----------------|
```

---

## 📊 Consequences (결과/장단점)

### ✅ Benefits (장점)

1. **Race Condition 제거**
   - INCR은 Redis의 원자적 연산
   - 동시 요청도 정확한 카운팅

2. **성능 우수**
   - Redis 왕복 1~2회 (INCR + 선택적 EXPIRE)
   - Lock 불필요 (wait 없음)

3. **간단한 구현**
   - 3줄 핵심 로직 (INCR + EXPIRE + Compare)

4. **확장성**
   - 동시 요청 수 증가해도 안정적
   - Redis cluster 사용 시 무한 확장

### ❌ Drawbacks (단점)

1. **Decrement Overhead**
   - 제한 초과 시 DECR 추가 호출
   - 하지만 성능 영향 미미

2. **Redis 의존성**
   - Redis 없으면 사용 불가
   - In-memory DB 필요

---

## 🌍 Known Uses (실제 사용 사례)

### DPP API Platform
```
Component: API PlanEnforcer
Problem: 동시 요청 시 rate limit bypass (10개 → 11개 통과)
Solution: INCR-first pattern
Result: P1-1 critical bug 해결, 100% 정확한 rate limiting
Test: 20 concurrent threads → 정확히 10개만 통과
```

### GitHub API Rate Limiting
```
X-RateLimit-Limit: 5000
X-RateLimit-Remaining: 4999
X-RateLimit-Reset: 1372700873

# Redis INCR 사용
INCR ratelimit:user:123:hour
EXPIRE ratelimit:user:123:hour 3600
```

### Stripe API Rate Limiting
```
Rate Limit: 100 requests/second

# INCR-first pattern
new_count = redis.incr(f"stripe:ratelimit:{api_key}:sec")
if new_count == 1:
    redis.expire(f"stripe:ratelimit:{api_key}:sec", 1)
if new_count > 100:
    raise RateLimitError()
```

---

## 🔗 Related Patterns (관련 패턴)

### Leaky Bucket Algorithm (대안)
```python
# Leaky Bucket: 일정 속도로 "leak"
def check_leaky_bucket(api_key: str):
    bucket_key = f"bucket:{api_key}"
    current = float(redis.get(bucket_key) or 0)
    now = time.time()

    # Leak: 시간에 비례해서 감소
    leaked = (now - last_refill) * leak_rate
    current = max(0, current - leaked)

    # Add: 요청 추가
    current += 1

    if current > bucket_size:
        raise RateLimitExceeded()

    redis.set(bucket_key, current)
```

**INCR-First vs Leaky Bucket**:
- INCR-First: 간단, 고정 윈도우 (분/시간)
- Leaky Bucket: 복잡, 유동적 윈도우 (평활화)

### Token Bucket Algorithm (대안)
```python
# Token Bucket: 토큰을 소비
def check_token_bucket(api_key: str):
    tokens = redis.get(f"tokens:{api_key}") or bucket_size

    if tokens > 0:
        redis.decr(f"tokens:{api_key}")
        return True
    else:
        raise RateLimitExceeded()
```

---

## 💡 Implementation Tips

### Tip 1: TTL은 new_count == 1일 때만
```python
# ✅ 올바른 방법 (첫 번째 요청만)
new_count = redis.incr(rate_key)
if new_count == 1:
    redis.expire(rate_key, 60)

# ❌ 잘못된 방법 (매번 TTL 재설정)
new_count = redis.incr(rate_key)
redis.expire(rate_key, 60)  # 윈도우가 계속 미뤄짐!
```

### Tip 2: DECR로 되돌리기
```python
# ✅ 제한 초과 시 되돌리기
if new_count > rate_limit:
    redis.decr(rate_key)  # 카운트 복원
    raise RateLimitExceeded()

# ❌ 되돌리지 않으면
# 다음 윈도우에도 영향 (누적 오차)
```

### Tip 3: Pipeline 사용 (성능 최적화)
```python
# ✅ Pipeline으로 왕복 최소화
pipe = redis.pipeline()
pipe.incr(rate_key)
pipe.expire(rate_key, 60)
results = pipe.execute()

new_count = results[0]
```

### Tip 4: Retry-After Header
```python
# ✅ TTL 가져와서 retry_after 계산
ttl = redis.ttl(rate_key)
retry_after = max(1, ttl)  # 최소 1초

raise RateLimitExceeded(retry_after=retry_after)

# HTTP Response Header
# Retry-After: 45
```

---

## 🧪 Testing Strategy

### Unit Test: INCR Atomicity
```python
def test_incr_atomic():
    """INCR은 원자적 연산 - race condition 없음"""

    redis_client.delete("test:counter")

    def worker():
        return redis_client.incr("test:counter")

    # 100개 스레드가 동시에 INCR
    with ThreadPoolExecutor(max_workers=100) as executor:
        futures = [executor.submit(worker) for _ in range(100)]
        results = [f.result() for f in futures]

    # 검증: 정확히 100까지 증가
    final_count = int(redis_client.get("test:counter"))
    assert final_count == 100

    # 검증: 각 스레드가 유니크한 값 받음 (1~100)
    assert sorted(results) == list(range(1, 101))
```

### Integration Test: Rate Limit Accuracy
```python
def test_rate_limit_concurrent_accuracy():
    """20개 동시 요청 → 정확히 10개만 통과 (P1-1)"""

    tenant_id = "test_tenant"
    plan_limits = {"rate_limit": 10}

    def make_request():
        try:
            enforcer.check_rate_limit(tenant_id, plan_limits)
            return "SUCCESS"
        except PlanViolationError:
            return "RATE_LIMITED"

    # 20개 스레드 동시 요청
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(make_request) for _ in range(20)]
        results = [f.result() for f in futures]

    # 검증: 정확히 10개 성공, 10개 rate limited
    assert results.count("SUCCESS") == 10
    assert results.count("RATE_LIMITED") == 10
```

### Chaos Test: High Concurrency
```python
def test_rate_limit_1000_concurrent():
    """1000개 동시 요청에서도 정확성 보장"""

    tenant_id = "stress_test"
    plan_limits = {"rate_limit": 100}

    def make_request():
        try:
            enforcer.check_rate_limit(tenant_id, plan_limits)
            return True
        except PlanViolationError:
            return False

    # 1000개 동시 요청
    with ThreadPoolExecutor(max_workers=1000) as executor:
        futures = [executor.submit(make_request) for _ in range(1000)]
        results = [f.result() for f in futures]

    # 검증: 정확히 100개만 성공
    success_count = sum(results)
    assert success_count == 100

    # 검증: Redis 카운트도 100
    final_count = int(redis_client.get(f"rate:{tenant_id}:minute"))
    assert final_count == 100
```

---

## 📚 Further Reading

- [Redis INCR Command](https://redis.io/commands/incr)
- [Rate Limiting Patterns](https://cloud.google.com/architecture/rate-limiting-strategies)
- [Stripe API Rate Limiting](https://stripe.com/docs/rate-limits)
- [Token Bucket vs Leaky Bucket](https://en.wikipedia.org/wiki/Token_bucket)

---

**Last Updated**: 2026-02-14
**Version**: 1.0
**Based on**: DPP API Platform v0.4.2.2 (P1-1, P1-2 Critical Feedback)
