# Project Kickoff Checklist
## 프로젝트 시작 전 필수 정의 사항

**목적**: "나중에 혼란"을 방지하고, 모든 참여자가 같은 방향을 보도록 초기 설정을 명확히 합니다.

**언제 사용**: 새 프로젝트 시작 시, 킥오프 미팅에서 이 체크리스트를 함께 작성합니다.

---

## ✅ Phase 1: 프로젝트 범위 정의

### 1.1 Problem Statement (문제 정의)
```
[ ] 해결하려는 문제가 무엇인가?
    예: "AI Agent가 결제 없이 API를 무제한 사용하는 문제"

[ ] 왜 지금 해결해야 하는가? (긴급성)
    예: "프로덕션 런칭 전 반드시 필요"

[ ] 성공 기준은 무엇인가?
    예: "Zero Money Leak 달성, 99.9% uptime"
```

### 1.2 Scope (범위)
```
[ ] MVP (Minimum Viable Product) 범위 정의
    - 핵심 기능만 포함 (nice-to-have 제외)
    - 예: MS-0~MS-3 (Database, Budget, SQS, Worker)

[ ] Out of Scope (명시적으로 제외)
    - 나중에 추가할 기능 명시
    - 예: "Multi-region deployment는 v2.0에서"

[ ] 점진적 목표 설정 (Incremental Goals)
    - Phase 1: MVP (핵심 기능)
    - Phase 2: Production Ready (강화)
    - Phase 3: Production Deployment (배포)
```

**💡 Lesson Learned**: "진짜 마지막" 문제는 초기에 전체 로드맵을 명확히 하지 않아서 발생합니다. MVP → Production Ready → Deployment를 분리하면 예상치 못한 "추가 작업"이 줄어듭니다.

---

## ✅ Phase 2: 기술적 의사결정

### 2.1 Technology Stack
```
[ ] Backend Framework
    예: FastAPI (async support, OpenAPI 자동 생성)

[ ] Database
    예: PostgreSQL 15+ (ACID, JSON support)

[ ] Message Queue
    예: AWS SQS (managed, scalable)

[ ] Cache/State Store
    예: Redis 7.0+ (atomic operations)

[ ] Deployment Platform
    예: Kubernetes (EKS)
```

### 2.2 Architecture Patterns
```
[ ] 핵심 아키텍처 패턴 선택 및 문서화
    예: "2-Phase Commit for financial transactions"

[ ] Concurrency Strategy
    예: "Optimistic Locking with version column"

[ ] Error Handling Strategy
    예: "RFC 9457 Problem Details for all errors"

[ ] Observability Strategy
    예: "trace_id propagation across all services"
```

**💡 Lesson Learned**: 아키텍처 패턴을 초기에 문서화하면, 나중에 "왜 이렇게 했지?" 하는 의문을 줄일 수 있습니다.

---

## ✅ Phase 3: Definition of Done (DoD)

### 3.1 "완료"의 정의
```
[ ] MVP 완료 기준 정의 (DEFINITION_OF_DONE_TEMPLATE.md 참조)
    - 기능 동작 여부
    - 테스트 통과 기준
    - 문서화 수준

[ ] Production Ready 기준 정의
    - 보안 검증
    - 성능 테스트
    - Chaos Testing

[ ] Deployment 완료 기준
    - 배포 자동화
    - Monitoring 설정
    - Rollback Plan
```

**💡 Lesson Learned**: DoD를 초기에 합의하면 "이제 끝이다"가 여러 번 나오지 않습니다.

---

## ✅ Phase 4: 프로젝트 구조 & 컨벤션

### 4.1 Directory Structure Philosophy
```
[ ] 디렉토리 구조 철학 문서화
    예:
    - apps/api/: API 서버 코드
    - apps/worker/: Background worker
    - apps/reaper/: Cleanup service
    - alembic/: DB migrations
    - k8s/: Kubernetes manifests
    - docs/: Documentation
    - tests/: Integration tests

[ ] 기능별 디렉토리 네이밍 규칙
    예: "billing 관련은 apps/api/dpp_api/billing/"
```

**💡 Lesson Learned**: 초기에 디렉토리 철학을 명확히 하면, "billing_service.py를 어디에 두지?" 같은 고민이 줄어듭니다.

### 4.2 Coding Conventions
```
[ ] Naming Conventions
    - Variables: snake_case
    - Classes: PascalCase
    - Constants: UPPER_SNAKE_CASE

[ ] Git Commit Message Format
    - Conventional Commits: feat/fix/docs/refactor
    - 예: "feat(P0-1): Add thread-safe session factory pattern"

[ ] Code Review Standards
    - PR size: < 500 lines (권장)
    - Review turnaround: < 24 hours
```

---

## ✅ Phase 5: 테스트 전략

### 5.1 Test Coverage Requirements
```
[ ] Unit Test Coverage Target
    예: "> 70% coverage for critical paths"

[ ] Integration Test Requirements
    예: "All API endpoints must have E2E tests"

[ ] Chaos Testing Requirements (if applicable)
    예: "Money-critical operations require chaos tests"
```

### 5.2 Test Pass Rate
```
[ ] Acceptable Pass Rate
    - Development: > 95%
    - Pre-Production: 100% (Zero tolerance)
    - Production: 100% (Mandatory)

[ ] Flaky Test Policy
    예: "Flaky tests must be fixed within 1 sprint"
```

**💡 Lesson Learned**: "100% 통과" 기준을 초기에 합의하면, 나중에 "4개 skip 괜찮나요?" 같은 불안감이 없습니다.

---

## ✅ Phase 6: 문서화 전략

### 6.1 Documentation Requirements
```
[ ] README.md 구조
    - Quick Start
    - Architecture Overview
    - Development Guide
    - Deployment Guide

[ ] Implementation Report 작성 여부
    예: "각 마일스톤 완료 시 IMPLEMENTATION_REPORT.md 업데이트"

[ ] API Documentation
    예: "OpenAPI/Swagger 자동 생성 + 수동 설명 추가"

[ ] Runbook Documentation
    예: "Production incident 대응 절차 문서화"
```

### 6.2 Memory System (Long-term Projects)
```
[ ] Memory System 설정 (Claude Code 등)
    - MEMORY.md: 핵심 교훈 (< 200 lines)
    - Topic files: 상세 노트 (debugging.md, patterns.md)

[ ] Session Transcript 저장 여부
    예: "중요한 의사결정은 .jsonl에 기록"
```

**💡 Lesson Learned**: 긴 프로젝트에서는 메모리 시스템이 컨텍스트 압축 문제를 완화합니다.

---

## ✅ Phase 7: 커뮤니케이션 프로토콜

### 7.1 Feedback Protocol
```
[ ] 피드백 주기
    예: "각 마일스톤 완료 시 리뷰"

[ ] 피드백 형식
    예: "P0 (Critical), P1 (Important), P2 (Nice-to-have)"

[ ] "완료" 확인 방법
    예: "명시적 승인: '좋아, 다음 단계로!'"
```

### 7.2 Change Request Protocol
```
[ ] 요구사항 변경 프로세스
    - Scope 변경: 명시적 DoD 재정의
    - 기능 추가: 새 마일스톤 vs. 현재 스프린트
    - 버그 수정: 즉시 vs. 다음 스프린트

[ ] "패치" vs "새 기능" 구분
    - 패치: 현재 DoD 만족을 위한 수정 (즉시)
    - 새 기능: DoD 외 추가 요구사항 (별도 계획)
```

**💡 Lesson Learned**: "패치"와 "새 기능"을 구분하면, Kubernetes manifests 후 billing system 추가 같은 상황에서 혼동이 없습니다.

---

## ✅ Phase 8: 리스크 관리

### 8.1 Technical Risks
```
[ ] 예상되는 기술적 위험 식별
    예: "SQS 중복 메시지 처리 (at-least-once delivery)"

[ ] Mitigation Strategy
    예: "Idempotency key + UniqueConstraint"
```

### 8.2 Project Risks
```
[ ] 예상되는 프로젝트 위험 식별
    예: "요구사항 변경으로 인한 일정 지연"

[ ] Mitigation Strategy
    예: "점진적 배포, MVP 우선 완료"
```

---

## 📋 Kickoff Meeting Agenda (Example)

```markdown
## Project Kickoff Meeting
**Date**: YYYY-MM-DD
**Participants**: [Names]

### Agenda
1. Problem Statement 확인 (10분)
2. Scope & MVP 정의 (15분)
3. Technology Stack 합의 (10분)
4. Definition of Done 초안 작성 (15분)
5. Directory Structure 합의 (10분)
6. 테스트 전략 합의 (10분)
7. 커뮤니케이션 프로토콜 확인 (5분)
8. Next Steps & Action Items (5분)

### Decisions Made
- [ ] MVP Scope: [기록]
- [ ] DoD: [링크 to DEFINITION_OF_DONE.md]
- [ ] Directory Structure: [링크 to ARCHITECTURE.md]
- [ ] Test Pass Rate: 100% for pre-production
- [ ] Feedback Protocol: P0/P1/P2 classification

### Action Items
- [ ] Person A: Create initial directory structure
- [ ] Person B: Set up database schema (Alembic)
- [ ] Person C: Write first integration test
```

---

## 🎯 Quick Start (TL;DR)

프로젝트 시작 전 **반드시 정의**할 5가지:

1. **MVP Scope**: 무엇을 만들 것인가? (명확한 범위)
2. **Definition of Done**: 언제 "완료"인가? (각 단계별)
3. **Directory Structure**: 코드를 어디에 둘 것인가? (철학)
4. **Test Strategy**: 테스트 기준은? (100% pass rate?)
5. **Communication Protocol**: 어떻게 소통할 것인가? (P0/P1/P2, 패치 vs 신규)

이 5가지를 킥오프에서 합의하면 **"진짜 마지막" 문제의 80%가 해결됩니다**.

---

## 📚 Related Documents

- [02_DEFINITION_OF_DONE_TEMPLATE.md](02_DEFINITION_OF_DONE_TEMPLATE.md)
- [03_MILESTONE_CHECKLIST.md](03_MILESTONE_CHECKLIST.md)
- [BEST_PRACTICES.md](../BEST_PRACTICES.md) (Phase 3에서 생성)

---

**Last Updated**: 2026-02-14
**Version**: 1.0
**Based on**: DPP API Platform v0.4.2.2 Project Experience
