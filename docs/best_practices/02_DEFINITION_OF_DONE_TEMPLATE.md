# Definition of Done (DoD) Template
## "완료" 기준 명확화 템플릿

**목적**: "이제 끝이다"를 여러 번 말하지 않기 위해, 각 단계별 "완료" 기준을 **초기에 명확히** 정의합니다.

**핵심 원칙**: "완료"는 주관적이지 않습니다. 체크리스트로 객관화합니다.

---

## 🎯 DoD의 3단계 구조

대부분의 프로젝트는 다음 3단계로 진행됩니다:

```
Phase 1: MVP (Minimum Viable Product)
  ↓
Phase 2: Production Ready (Hardening)
  ↓
Phase 3: Production Deployment (Go-live)
```

각 단계마다 **다른 "완료" 기준**을 적용합니다.

---

## ✅ Phase 1: MVP 완료 기준

**목표**: 핵심 기능이 동작하는지 검증 (프로토타입 수준)

### 1.1 Feature Completeness
```
[ ] 핵심 유저 스토리 구현 완료
    - User Story 1: [제목]
      - Acceptance Criteria 1: ✅
      - Acceptance Criteria 2: ✅
    - User Story 2: [제목]
      - ...

[ ] 핵심 API 엔드포인트 동작 확인
    - POST /v1/runs: ✅ 201 Created
    - GET /v1/runs/{id}: ✅ 200 OK
    - ...

[ ] Happy Path 시나리오 통과
    예: "사용자가 Run을 제출하고 결과를 받을 수 있다"
```

### 1.2 Test Coverage (MVP Level)
```
[ ] 핵심 경로 테스트 존재
    - Happy path tests: ✅
    - Basic error handling: ✅

[ ] Test Pass Rate
    - Development: > 90% (일부 skip 허용)
    - Flaky tests 있어도 됨 (문서화 필수)

[ ] Manual Testing 완료
    - 로컬 환경에서 E2E 시나리오 검증
```

**💡 MVP에서는 100% 완벽 불필요**: 핵심 기능만 동작하면 됩니다.

### 1.3 Documentation (MVP Level)
```
[ ] README.md - Quick Start 섹션
    - Installation steps
    - How to run locally
    - Basic API usage example

[ ] Code Comments (중요 로직만)
    - 복잡한 알고리즘에만 주석
    - 모든 함수에 docstring 불필요
```

### 1.4 Code Quality (MVP Level)
```
[ ] 코드 리뷰 완료 (1명 이상)
    - 명백한 버그 없음
    - 심각한 보안 이슈 없음

[ ] Git Commit 정리
    - 의미있는 커밋 메시지
    - 불필요한 debug code 제거
```

**MVP 완료 승인 기준**:
- ✅ 핵심 기능 동작
- ✅ 기본 테스트 통과 (> 90%)
- ✅ README로 다른 사람이 실행 가능

---

## ✅ Phase 2: Production Ready 완료 기준

**목표**: 프로덕션 환경에서 안전하게 운영 가능한 수준

### 2.1 Feature Completeness (Production Level)
```
[ ] 모든 유저 스토리 구현 완료
    - Edge cases 처리
    - Error handling 완전히 구현

[ ] API 완전성
    - 모든 엔드포인트 구현
    - Error responses (4xx, 5xx) 정의
    - Rate limiting 구현
    - Authentication/Authorization 구현
```

### 2.2 Test Coverage (Production Level)
```
[ ] Comprehensive Test Suite
    - Unit tests: > 70% code coverage
    - Integration tests: All API endpoints
    - E2E tests: Critical user journeys

[ ] Test Pass Rate
    - **100% Pass Rate** (Zero tolerance)
    - No flaky tests
    - No skipped tests (without explicit reason)

[ ] Chaos Testing (if applicable)
    - Money-critical operations: 100% chaos tests
    - Concurrency tests (race conditions)
    - Failure injection tests (DB down, Redis down)

[ ] Regression Test Suite
    - All critical bugs have regression tests
    - P0/P1 issues covered by tests
```

**💡 Lesson Learned**: Production Ready에서는 "100% 통과"가 필수입니다. 4개 skip도 허용하지 않습니다.

### 2.3 Security & Compliance
```
[ ] Security Audit 완료
    - No hardcoded credentials (P0-2 compliance)
    - Environment variables only
    - Secrets management (AWS Secrets Manager, etc.)

[ ] Dependency Vulnerability Scan
    - pip-audit: No known vulnerabilities
    - Dependency versions pinned

[ ] OWASP Top 10 Check
    - SQL Injection: ✅ Protected (ORM parameterized queries)
    - XSS: ✅ Protected (input sanitization)
    - CSRF: ✅ Protected (if applicable)

[ ] Compliance Requirements (if applicable)
    - GDPR, HIPAA, PCI-DSS, etc.
```

### 2.4 Performance & Scalability
```
[ ] Performance Testing
    - Load test: 100 req/s sustained (API)
    - Latency: p95 < 500ms
    - Database query optimization (no N+1)

[ ] Scalability Verification
    - Horizontal scaling tested (2+ instances)
    - No singleton bottlenecks
    - Stateless design verified
```

### 2.5 Observability
```
[ ] Logging
    - Structured logging (JSON format)
    - Log levels appropriate (INFO, WARN, ERROR, CRITICAL)
    - Sensitive data not logged

[ ] Metrics
    - Prometheus metrics exposed
    - Key business metrics tracked

[ ] Tracing
    - trace_id propagation (end-to-end)
    - Distributed tracing setup (if multi-service)

[ ] Alerting
    - CRITICAL alerts defined (PagerDuty, etc.)
    - Alert runbooks written
```

### 2.6 Documentation (Production Level)
```
[ ] README.md - Complete
    - Quick Start
    - Architecture Overview
    - Development Guide
    - Production Deployment Guide

[ ] Implementation Report
    - Design decisions documented
    - Architecture patterns explained
    - Known limitations listed

[ ] API Documentation
    - OpenAPI/Swagger complete
    - All endpoints documented
    - Example requests/responses

[ ] Runbooks
    - Incident response procedures
    - Common troubleshooting steps
    - Rollback procedures
```

### 2.7 Code Quality (Production Level)
```
[ ] Code Review (2+ reviewers)
    - No code smells
    - SOLID principles followed
    - Design patterns appropriate

[ ] Static Analysis
    - Linter passing (flake8, pylint)
    - Type checking (mypy, if applicable)

[ ] Refactoring Complete
    - No TODOs for critical paths
    - Technical debt documented
```

**Production Ready 완료 승인 기준**:
- ✅ 100% 테스트 통과 (regression + chaos)
- ✅ 보안 검증 완료 (no hardcoded secrets)
- ✅ 문서 완전 (README + Implementation Report + Runbooks)
- ✅ Observability 설정 (logging + metrics + tracing)

---

## ✅ Phase 3: Production Deployment 완료 기준

**목표**: 실제 사용자에게 서비스 제공 가능

### 3.1 Infrastructure Ready
```
[ ] Production Environment 구축
    - Database: RDS Multi-AZ, backups enabled
    - Cache: Redis Multi-AZ, persistence enabled
    - Message Queue: SQS with DLQ
    - Storage: S3 with versioning

[ ] Kubernetes Manifests (or equivalent)
    - Deployments: API, Worker, Reaper
    - Services: LoadBalancer, ClusterIP
    - ConfigMaps: Environment variables
    - Secrets: Sensitive data (not in code)
    - HPA: Auto-scaling configured

[ ] IAM Roles & Permissions
    - Least privilege principle
    - IRSA configured (if EKS)
    - No root access
```

### 3.2 Deployment Automation
```
[ ] CI/CD Pipeline
    - Automated tests on PR
    - Automated build on merge
    - Automated deployment (staging → production)

[ ] Deployment Script
    - Security checks (no hardcoded credentials)
    - Test suite execution
    - Migration verification
    - Image build & push
    - Health check verification

[ ] Rollback Plan
    - One-command rollback
    - Database rollback tested
    - Rollback SLA defined (< 5 minutes)
```

### 3.3 Pre-Deployment Checklist
```
[ ] Security Checklist (P0-2)
    - No hardcoded AWS credentials
    - IAM roles verified
    - Secrets in Secrets Manager
    - Environment variables NOT set (SQS_ENDPOINT_URL, etc.)

[ ] Database Migration
    - Alembic migration clean (no drift)
    - Migration tested on staging
    - Backup taken before migration

[ ] Smoke Testing
    - E2E smoke test passed on staging
    - All critical paths verified
    - Performance baseline established
```

### 3.4 Monitoring & Alerting
```
[ ] Dashboards
    - System health dashboard (Grafana)
    - Business metrics dashboard
    - Error rate, latency, throughput

[ ] Alerts
    - CRITICAL alerts configured
    - PagerDuty integration
    - Alert fatigue prevented (no false positives)

[ ] On-Call Rotation
    - On-call schedule defined
    - Runbooks accessible
    - Escalation policy defined
```

### 3.5 Post-Deployment Verification
```
[ ] Deployment Success Criteria (Day 1)
    - All pods healthy (kubectl get pods)
    - Health checks passing (/health, /readyz)
    - Smoke test passed on production
    - No CRITICAL alerts
    - trace_id visible in logs

[ ] Week 1 Success Criteria
    - 99.9% uptime
    - 0 critical incidents
    - Average latency < 100ms (p95)
    - Error rate < 0.1%

[ ] Month 1 Success Criteria
    - 99.95% uptime
    - Auto-scaling functioning
    - Cost per request within budget
    - Customer satisfaction > 4.5/5
```

**Production Deployment 완료 승인 기준**:
- ✅ Infrastructure provisioned & tested
- ✅ CI/CD pipeline functional
- ✅ Monitoring & alerting operational
- ✅ Smoke test passed on production
- ✅ Day 1 success criteria met

---

## 📋 DoD Template (Fill-in-the-Blank)

### Your Project DoD

```markdown
# Definition of Done: [Project Name]

## MVP Completion
- [ ] Core features: [List 3-5 features]
- [ ] Test pass rate: > ___%
- [ ] Documentation: README Quick Start

**Approval**: [Name/Date]

---

## Production Ready Completion
- [ ] All features complete
- [ ] Test pass rate: 100%
- [ ] Security audit: No hardcoded secrets
- [ ] Performance: p95 < ___ms
- [ ] Documentation: README + Implementation Report + Runbooks

**Approval**: [Name/Date]

---

## Production Deployment Completion
- [ ] Infrastructure: [List resources]
- [ ] CI/CD: Automated deployment
- [ ] Monitoring: Dashboards + Alerts
- [ ] Smoke test: Passed on production
- [ ] Day 1 criteria: [List success metrics]

**Approval**: [Name/Date]
```

---

## 🚨 Anti-Patterns (피해야 할 것)

### ❌ "거의 다 됐어요" (Almost Done)
```
문제: "90% 완료"는 없습니다. 완료 아니면 미완료입니다.
해결: 체크리스트로 객관화. 10개 중 9개 완료 = 90% 완료 (명확)
```

### ❌ "이번이 진짜 마지막" (Final Changes)
```
문제: "마지막"이 여러 번 나오면 신뢰 저하
해결: 초기에 DoD 3단계 (MVP → Production Ready → Deployment) 명확히
```

### ❌ "테스트는 나중에" (Tests Later)
```
문제: 나중은 없습니다. 기술 부채로 쌓입니다.
해결: MVP부터 테스트 기준 명시 (> 90% pass rate)
```

### ❌ "문서는 배포 전에" (Docs Before Deploy)
```
문제: 배포 직전에 문서 쓰면 품질 낮음
해결: 각 단계마다 문서 요구사항 명시 (README → Report → Runbooks)
```

---

## 💡 Lessons Learned from DPP Project

### 1. "100% 통과"는 Production Ready부터
- MVP: > 90% 허용
- Production Ready: 100% 필수
- 초기부터 100% 요구하면 burnout

### 2. "패치" vs "새 기능" 구분
- 패치: DoD 만족을 위한 수정 (P0-1, P0-2)
- 새 기능: DoD 외 추가 (billing system)
- 구분하면 "끝났다고 했잖아요" 방지

### 3. 문서는 "완료"의 일부
- Implementation Report 없으면 Production Ready 아님
- Runbook 없으면 Deployment 완료 아님

### 4. Security는 "선택"이 아님
- P0-2 같은 보안 이슈는 Production Ready 필수
- 초기 DoD에 보안 체크리스트 포함

---

## 📚 Related Documents

- [01_PROJECT_KICKOFF_CHECKLIST.md](01_PROJECT_KICKOFF_CHECKLIST.md)
- [03_MILESTONE_CHECKLIST.md](03_MILESTONE_CHECKLIST.md)
- [04_PRE_DEPLOYMENT_CHECKLIST.md](04_PRE_DEPLOYMENT_CHECKLIST.md) (Phase 1 완료 후 생성)

---

## 🎯 Quick Reference (TL;DR)

**DoD 3단계**:
1. **MVP**: 핵심 기능 동작 + > 90% 테스트 통과 + README Quick Start
2. **Production Ready**: 모든 기능 완료 + 100% 테스트 통과 + 보안 검증 + 완전한 문서
3. **Production Deployment**: Infrastructure + CI/CD + Monitoring + Smoke Test 통과

**핵심 원칙**:
- "완료"는 체크리스트로 객관화
- 각 단계마다 다른 기준 적용
- 초기에 합의, 중간에 변경 최소화

---

**Last Updated**: 2026-02-14
**Version**: 1.0
**Based on**: DPP API Platform v0.4.2.2 Project Experience
