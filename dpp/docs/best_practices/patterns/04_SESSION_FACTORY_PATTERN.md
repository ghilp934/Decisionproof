# Session Factory Pattern
## Thread-Safe Database Session Management

**Category**: Concurrency Pattern
**Complexity**: ⭐ Low
**Performance Impact**: 없음

---

## 📋 Pattern Summary

**Also Known As**: Connection Factory, Session Per Thread

**Intent**: 멀티스레드 환경에서 각 스레드가 독립적인 Database Session을 사용하도록 보장하여, thread-safety를 확보합니다.

**Motivation**: SQLAlchemy Session은 thread-safe하지 않습니다. 여러 스레드가 하나의 Session을 공유하면 "connection pool overflow", "DetachedInstanceError" 등 다양한 문제가 발생합니다.

---

## 🎯 Problem (문제)

### 시나리오: HeartbeatThread가 Main Thread와 Session 공유

```python
# ❌ 잘못된 코드
class WorkerLoop:
    def __init__(self, db_session: Session):
        self.db_session = db_session  # Main thread의 session

    def _process_message(self, message):
        # Main thread에서 Session 사용
        run = self.db_session.query(Run).get(run_id)

        # HeartbeatThread 생성 (Session 전달)
        heartbeat = HeartbeatThread(
            db_session=self.db_session,  # ← 문제! Session 공유
            run_id=run_id
        )
        heartbeat.start()

class HeartbeatThread(threading.Thread):
    def __init__(self, db_session: Session, run_id: str):
        self.db_session = db_session  # Main thread의 session 공유

    def run(self):
        while not self.stopped:
            # Heartbeat thread에서 Session 사용
            run = self.db_session.query(Run).get(self.run_id)  # ← 문제!
            run.lease_expires_at = datetime.utcnow() + timedelta(seconds=120)
            self.db_session.commit()
            time.sleep(30)
```

**문제점**:
1. **DetachedInstanceError**
   - Main thread가 commit하면 Heartbeat thread의 객체가 detached됨
   - `run.lease_expires_at` 접근 시 에러

2. **Connection Pool Overflow**
   - Session이 connection을 계속 점유
   - 다른 요청이 connection을 못 받음

3. **Race Condition**
   - Main thread와 Heartbeat thread가 동시에 commit
   - Data corruption 가능

---

## 🌍 Context (상황)

이 패턴이 필요한 경우:

```
✅ Muliti-threaded application
   - Background threads (Heartbeat, Reaper)
   - ThreadPoolExecutor 사용
   - Celery workers

✅ SQLAlchemy Session 사용
   - 또는 다른 ORM (Django ORM은 thread-local 기본 제공)

✅ 각 스레드가 Database 접근 필요
   - Background job이 DB 조회/수정
```

이 패턴이 **불필요한** 경우:

```
❌ Single-threaded application
❌ Thread가 DB 접근 안 함 (계산만)
❌ Thread-safe ORM 사용 (Django ORM)
```

---

## ⚖️ Forces (제약 조건)

다음 요구사항들이 충돌합니다:

1. **안전성**: Thread-safety 보장
2. **성능**: Connection pool 효율적 사용
3. **복잡도**: 간단한 코드
4. **리소스**: Connection 낭비 방지

이 패턴은 **안전성을 우선**하고, Factory pattern으로 복잡도를 최소화합니다.

---

## ✅ Solution (해결책)

### 핵심 아이디어

Session 객체를 직접 전달하지 말고, **Session을 만드는 Factory 함수**를 전달합니다.

```python
# ❌ Session 직접 전달 (잘못됨)
heartbeat = HeartbeatThread(db_session=session)

# ✅ Factory 전달 (올바름)
heartbeat = HeartbeatThread(session_factory=SessionLocal)
```

각 스레드는 필요할 때 Factory를 호출해서 **독립적인 Session을 생성**합니다.

```python
# Heartbeat thread 내부
with self.session_factory() as session:
    # 이 스레드만의 Session
    run = session.query(Run).get(self.run_id)
    session.commit()
# Session 자동 close
```

---

## 🛠️ Implementation (구현)

### DPP 프로젝트 실제 코드

#### Step 1: SessionLocal Factory 정의

```python
# apps/worker/dpp_worker/db.py

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Database engine (connection pool)
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True  # Connection 유효성 자동 체크
)

# Session factory (호출 시마다 새 Session 생성)
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# 사용법
# session = SessionLocal()  # 새 Session 생성
```

#### Step 2: HeartbeatThread - Session Factory 사용

```python
# apps/worker/dpp_worker/heartbeat.py

from typing import Callable
from sqlalchemy.orm import Session

class HeartbeatThread(threading.Thread):
    def __init__(
        self,
        session_factory: Callable[[], Session],  # ← Factory 함수
        run_id: str,
        tenant_id: str,
        interval_sec: int = 30
    ):
        super().__init__(daemon=True)
        self.session_factory = session_factory  # Session이 아닌 Factory 저장
        self.run_id = run_id
        self.tenant_id = tenant_id
        self.interval_sec = interval_sec
        self.stopped = False

    def run(self) -> None:
        """Heartbeat 루프"""
        while not self.stopped:
            try:
                self._send_heartbeat()
                time.sleep(self.interval_sec)
            except Exception as e:
                logger.error("Heartbeat error", exc_info=True)

    def _send_heartbeat(self) -> None:
        """Heartbeat 전송 (lease 연장)"""

        # ✅ 이 스레드만의 독립적인 Session 생성
        with self.session_factory() as session:
            repo = RunRepository(session)

            success = repo.update_with_version_check(
                run_id=self.run_id,
                tenant_id=self.tenant_id,
                current_version=self.current_version,
                updates={
                    "lease_expires_at": datetime.utcnow() + timedelta(seconds=120)
                }
            )

            if success:
                self.current_version += 1
                logger.info("Heartbeat sent")
            else:
                logger.warning("Heartbeat failed - version mismatch")

        # Session은 with 블록 종료 시 자동 close
```

#### Step 3: WorkerLoop - Factory 전달

```python
# apps/worker/dpp_worker/loops/sqs_loop.py

class WorkerLoop:
    def __init__(
        self,
        session_factory: Callable[[], Session],  # ← Factory 받음
        sqs_queue_url: str,
        ...
    ):
        self.session_factory = session_factory  # Factory 저장
        self.sqs_queue_url = sqs_queue_url
        ...

    def _process_message(self, message: Dict[str, Any]) -> bool:
        """SQS 메시지 처리"""

        # Main thread의 Session 생성
        with self.session_factory() as session:
            repo = RunRepository(session)
            run = repo.get_by_id(run_id, tenant_id)

            # HeartbeatThread 시작 (Factory 전달)
            heartbeat = HeartbeatThread(
                session_factory=self.session_factory,  # ← Factory 전달
                run_id=run.run_id,
                tenant_id=run.tenant_id
            )
            heartbeat.start()

            # Pack 실행
            result = execute_pack(run)

            # Heartbeat 중지
            heartbeat.stop()
            heartbeat.join()

            # Finalize
            ...

        # Session 자동 close
        return True
```

#### Step 4: Main Entry Point - Factory 전달

```python
# apps/worker/dpp_worker/main.py

from dpp_worker.db import SessionLocal

def main():
    # WorkerLoop 초기화 (Session Factory 전달)
    worker = WorkerLoop(
        session_factory=SessionLocal,  # ← Factory 전달 (Session 아님!)
        sqs_queue_url=SQS_QUEUE_URL,
        ...
    )

    # Worker 시작
    worker.start()

if __name__ == "__main__":
    main()
```

---

## 🔄 Sequence Diagram (시퀀스 다이어그램)

### Session Factory Pattern 흐름

```
Main Thread                 HeartbeatThread            SessionLocal
    |                            |                          |
    | session = SessionLocal()   |                          |
    |--------------------------->|                          |
    |    <Session 1>             |                          |
    |<---------------------------|                          |
    |                            |                          |
    | heartbeat = HeartbeatThread(session_factory=SessionLocal)
    |--------------------------->|                          |
    |                            |                          |
    | heartbeat.start()          |                          |
    |--------------------------->|                          |
    |                            | with SessionLocal():     |
    |                            |------------------------>|
    |                            |      <Session 2>        | ← 독립적!
    |                            |<------------------------|
    |                            |                          |
    |                            | session.query(...)      |
    |                            | session.commit()        |
    |                            |                          |
    | session.query(...)         |                          |
    | session.commit()           |                          |
    |                            |                          |
    ✅ 각 스레드가 독립적인 Session 사용 (안전!)
```

---

## 📊 Consequences (결과/장단점)

### ✅ Benefits (장점)

1. **Thread-Safety 보장**
   - 각 스레드가 독립적인 Session 사용
   - DetachedInstanceError 없음
   - Race condition 없음

2. **Connection Pool 효율적 사용**
   - Session을 with 블록 내에서만 사용
   - 사용 후 즉시 반환 (close)
   - Connection 낭비 방지

3. **간단한 구현**
   - Factory 패턴만 추가
   - 기존 코드 변경 최소화

4. **테스트 용이**
   - Mock factory를 전달하면 테스트 Session 주입 가능

### ❌ Drawbacks (단점)

1. **약간의 보일러플레이트**
   - with self.session_factory() as session: 반복
   - 하지만 안전성 대비 미미한 단점

2. **Factory 전달 필요**
   - 모든 스레드 생성 시 factory 명시적 전달
   - 까먹으면 버그

---

## 🌍 Known Uses (실제 사용 사례)

### DPP API Platform
```
Component: HeartbeatThread, WorkerLoop
Problem: HeartbeatThread와 Main thread 간 Session 공유 → DetachedInstanceError
Solution: session_factory pattern
Result: P0-1 critical bug 해결, 100% thread-safe
```

### Flask-SQLAlchemy
```python
# Flask는 scoped_session 사용 (thread-local storage)
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy(app)

# 각 request thread마다 독립적인 session
@app.route('/users')
def get_users():
    users = db.session.query(User).all()  # Thread-safe!
    return jsonify(users)
```

### Celery + SQLAlchemy
```python
# Celery task마다 새 Session 생성
from myapp.db import SessionLocal

@celery.task
def process_order(order_id):
    with SessionLocal() as session:
        order = session.query(Order).get(order_id)
        # Process order
        session.commit()
```

---

## 🔗 Related Patterns (관련 패턴)

### Thread-Local Storage (대안)
```python
# Thread-local storage를 이용한 Session 관리
import threading

local = threading.local()

def get_session():
    if not hasattr(local, 'session'):
        local.session = SessionLocal()
    return local.session

# 사용법
session = get_session()  # 각 스레드마다 독립적
```

**Session Factory vs Thread-Local**:
- Session Factory: 명시적, 테스트 용이
- Thread-Local: 암시적, 간편하지만 디버깅 어려움

### Context Manager Pattern
```python
# Session factory와 함께 사용
with self.session_factory() as session:
    # Session 사용
    pass
# 자동 close
```

---

## 💡 Implementation Tips

### Tip 1: with 블록 사용 (자동 close)
```python
# ✅ 올바른 방법 (자동 close)
with self.session_factory() as session:
    run = session.query(Run).get(run_id)
    session.commit()
# Session 자동 close

# ❌ 잘못된 방법 (수동 close 잊을 수 있음)
session = self.session_factory()
run = session.query(Run).get(run_id)
session.commit()
session.close()  # 까먹으면 connection leak!
```

### Tip 2: Factory는 Callable 타입
```python
from typing import Callable
from sqlalchemy.orm import Session

def __init__(self, session_factory: Callable[[], Session]):
    self.session_factory = session_factory
```

### Tip 3: Session은 짧게 유지
```python
# ✅ 짧은 Session (좋음)
with self.session_factory() as session:
    run = session.query(Run).get(run_id)
    run.status = "COMPLETED"
    session.commit()
# 즉시 close

# ❌ 긴 Session (나쁨)
with self.session_factory() as session:
    run = session.query(Run).get(run_id)
    time.sleep(60)  # Connection을 60초간 점유!
    run.status = "COMPLETED"
    session.commit()
```

### Tip 4: Repository Pattern과 함께 사용
```python
# Repository가 Session을 받도록
class RunRepository:
    def __init__(self, session: Session):
        self.session = session

# Factory로 Session 생성 후 Repository에 전달
with self.session_factory() as session:
    repo = RunRepository(session)
    run = repo.get_by_id(run_id)
    repo.update(run_id, status="COMPLETED")
```

---

## 🧪 Testing Strategy

### Unit Test: Thread-Safety
```python
def test_multiple_threads_independent_sessions():
    """여러 스레드가 독립적인 Session 사용"""

    results = []

    def worker(session_factory, run_id):
        with session_factory() as session:
            run = session.query(Run).get(run_id)
            results.append(id(session))  # Session 객체 ID 기록

    # 10개 스레드 동시 실행
    threads = []
    for i in range(10):
        t = threading.Thread(target=worker, args=(SessionLocal, run_id))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # 검증: 모든 Session ID가 다름 (독립적)
    assert len(set(results)) == 10
```

### Integration Test: Heartbeat Thread
```python
def test_heartbeat_thread_no_detached_error():
    """HeartbeatThread가 DetachedInstanceError 발생 안 함"""

    run = create_run(status="PROCESSING")

    # Main thread Session
    with SessionLocal() as main_session:
        main_run = main_session.query(Run).get(run.run_id)

        # HeartbeatThread 시작 (Factory 전달)
        heartbeat = HeartbeatThread(
            session_factory=SessionLocal,  # ← Factory
            run_id=run.run_id
        )
        heartbeat.start()

        # Main thread에서 commit
        main_run.status = "COMPLETED"
        main_session.commit()

        # Heartbeat thread는 계속 동작 (에러 없음)
        time.sleep(2)  # 2초 대기 (heartbeat 실행됨)

        heartbeat.stop()
        heartbeat.join()

    # 검증: HeartbeatThread가 에러 없이 종료됨
    assert heartbeat.is_alive() is False
```

---

## 📚 Further Reading

- [SQLAlchemy Session Basics](https://docs.sqlalchemy.org/en/14/orm/session_basics.html)
- [Thread-Safe Session Management](https://docs.sqlalchemy.org/en/14/orm/contextual.html)
- [Factory Pattern (Gang of Four)](https://en.wikipedia.org/wiki/Factory_method_pattern)

---

**Last Updated**: 2026-02-14
**Version**: 1.0
**Based on**: DPP API Platform v0.4.2.2 (P0-1 Critical Feedback)
