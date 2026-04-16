# Milestone Checklist
## 각 마일스톤 완료 시 검증 항목

**목적**: 마일스톤(MS)을 완료할 때마다 일관된 기준으로 검증하여, 기술 부채를 쌓지 않고 다음 단계로 넘어갑니다.

**언제 사용**: 각 MS 완료 직전, 이 체크리스트를 실행합니다.

---

## 🎯 Milestone Completion Process (5단계)

```
1. Feature Implementation (기능 구현)
   ↓
2. Testing & Validation (테스트 검증)
   ↓
3. Documentation Update (문서 업데이트)
   ↓
4. Code Review & Git Commit (리뷰 및 커밋)
   ↓
5. Retrospective & Next Steps (회고 및 다음 단계)
```

---

## ✅ Step 1: Feature Implementation

### 1.1 Acceptance Criteria 확인
```
[ ] 모든 User Story의 Acceptance Criteria 만족
    - User Story 1:
      - AC 1: ✅
      - AC 2: ✅
    - User Story 2:
      - AC 1: ✅

[ ] Edge Cases 처리
    - Invalid input handling
    - Boundary conditions
    - Null/empty checks

[ ] Error Handling
    - All exceptions caught and logged
    - User-friendly error messages
    - RFC 9457 compliance (if applicable)
```

**💡 Tip**: Acceptance Criteria를 체크리스트로 만들어두면 놓치지 않습니다.

### 1.2 Integration Points 확인
```
[ ] Upstream Dependencies
    - API → Database: 연결 확인
    - API → Redis: 연결 확인
    - API → SQS: 메시지 전송 확인

[ ] Downstream Dependencies
    - Worker → SQS: 메시지 수신 확인
    - Worker → S3: 업로드 확인
    - Reaper → Database: 조회 확인

[ ] Data Flow Verification
    - API → Worker → Reaper: trace_id 추적 가능
```

---

## ✅ Step 2: Testing & Validation

### 2.1 Test Execution
```
[ ] Run Full Test Suite
    ```bash
    cd apps/api
    python -m pytest -v
    ```
    - Expected: All tests passing (or meets DoD criteria)

[ ] Test Results Documentation
    - Total tests: ___
    - Passed: ___
    - Failed: ___ (if any, document reason)
    - Skipped: ___ (if any, document reason)

[ ] Pass Rate Verification
    - MVP: > 90%
    - Production Ready: 100%
```

### 2.2 Manual Testing (Critical Paths)
```
[ ] Happy Path Scenario
    - Scenario 1: [Description]
      - Steps: 1, 2, 3
      - Expected: ✅
    - Scenario 2: [Description]
      - Steps: 1, 2, 3
      - Expected: ✅

[ ] Error Scenarios
    - Invalid API key: 401 Unauthorized ✅
    - Rate limit exceeded: 429 Too Many Requests ✅
    - Insufficient budget: 400 Bad Request ✅

[ ] E2E Scenario (if applicable)
    - Submit run → Process → Complete → Verify result
    - trace_id propagation verified
```

### 2.3 Regression Testing
```
[ ] Previous MS Features Still Working
    - MS-0 features: ✅
    - MS-1 features: ✅
    - MS-2 features: ✅

[ ] No Breaking Changes (unless documented)
    - API contract unchanged (or versioned)
    - Database schema backward compatible
```

**💡 Lesson Learned**: DPP 프로젝트에서 MS-4 완료 후 MS-1 budget 기능이 깨진 적이 있었습니다. Regression test가 이를 방지합니다.

---

## ✅ Step 3: Documentation Update

### 3.1 README.md Update
```
[ ] Quick Start 섹션 업데이트
    - New dependencies? (pip install, docker-compose)
    - New environment variables? (DATABASE_URL, etc.)

[ ] Features 섹션 업데이트
    - New milestone features added

[ ] Architecture Overview Update (if changed)
    - New components? (Reaper, etc.)
    - New data flows? (SQS → Worker)
```

### 3.2 Implementation Report Update (if applicable)
```
[ ] Milestone Section 추가
    - MS-X: [Title]
    - Implementation approach
    - Design decisions
    - Challenges & solutions

[ ] Architecture Diagrams Update
    - Sequence diagrams
    - Component diagrams
```

### 3.3 Code Comments & Docstrings
```
[ ] Complex Logic Commented
    - 2-phase commit logic
    - Optimistic locking logic
    - Race condition handling

[ ] Public APIs Documented
    - Function signatures
    - Parameters & return types
    - Example usage
```

**💡 Tip**: 문서는 코드 작성 직후에 쓰세요. 나중으로 미루면 기억이 흐려집니다.

---

## ✅ Step 4: Code Review & Git Commit

### 4.1 Self Code Review
```
[ ] Code Quality Check
    - No debug print statements
    - No commented-out code (unless TODO)
    - No hardcoded values (use constants/env vars)

[ ] Security Check
    - No hardcoded credentials (P0-2)
    - No sensitive data in logs
    - Input validation present

[ ] Performance Check
    - No N+1 queries
    - Database indexes present
    - No unbounded loops
```

### 4.2 Peer Code Review (if applicable)
```
[ ] Reviewer 1: [Name] - Approved ✅
    - Comments addressed: ✅

[ ] Reviewer 2: [Name] - Approved ✅
    - Comments addressed: ✅
```

### 4.3 Git Commit
```
[ ] Staged Files Verification
    ```bash
    git status
    ```
    - Only relevant files staged
    - No accidental .env or .pyc files

[ ] Commit Message Format
    ```
    feat(MS-X): [Brief description]

    [Detailed description]

    - Change 1
    - Change 2

    Tests: [Test results summary]

    Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
    ```

[ ] Commit Execution
    ```bash
    git commit -m "..."
    git log --oneline -1  # Verify commit
    ```
```

**💡 DPP Example**:
```
feat(MS-4): Implement 2-phase finalize (Claim → Upload → Commit)

2-phase commit ensures atomicity of S3 upload and budget settlement.

- Add finalize_stage column (PENDING → CLAIMED → COMMITTED)
- Implement optimistic locking with version column
- Add ClaimError for race condition handling

Tests: 112 passed, 2 skipped (100% success rate)

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
```

---

## ✅ Step 5: Retrospective & Next Steps

### 5.1 Retrospective (Quick)
```
[ ] What Went Well?
    - [예: "2-phase commit 패턴이 race condition 완전히 해결"]

[ ] What Could Be Improved?
    - [예: "초기 설계 시 optimistic locking 고려했으면 refactoring 불필요"]

[ ] Lessons Learned
    - [예: "SELECT ... FOR UPDATE는 PostgreSQL만 지원, SQLite 테스트 주의"]

[ ] Technical Debt (if any)
    - [예: "Reaper reconcile loop 성능 개선 필요 (TODO: MS-6)"]
```

**💡 Tip**: Retrospective는 5분 이내로 짧게. 길면 안 합니다.

### 5.2 Update Memory System (if long-term project)
```
[ ] MEMORY.md Update
    - 핵심 교훈 추가 (< 5 lines)
    - 예: "2-phase commit은 finalize_stage + version column 필수"

[ ] Topic File Update (if applicable)
    - patterns.md: 새 패턴 추가
    - debugging.md: 디버깅 팁 추가
```

### 5.3 Next Milestone Planning
```
[ ] Next MS Scope Defined
    - MS-(X+1): [Title]
    - Features: [List 3-5 features]

[ ] Blockers Identified
    - [예: "MS-5 시작 전 Redis 설치 필요"]

[ ] Dependencies Clarified
    - [예: "MS-5는 MS-4 완료 후에만 가능 (finalize 로직 필요)"]
```

---

## 📋 Milestone Completion Report Template

각 MS 완료 후 간단한 리포트를 작성합니다 (5-10분 소요):

```markdown
# Milestone Completion Report: MS-X

**Date**: YYYY-MM-DD
**Duration**: X days
**Status**: ✅ COMPLETED

## Features Delivered
- Feature 1: [Brief description]
- Feature 2: [Brief description]
- Feature 3: [Brief description]

## Test Results
- Total Tests: ___
- Pass Rate: ___%
- Critical Tests: [List]

## Documentation Updated
- [x] README.md
- [x] Implementation Report
- [ ] API Docs (not applicable)

## Git Commit
- Commit: [hash]
- Message: [First line of commit message]

## Retrospective
**What Went Well**:
- [Item 1]

**What Could Be Improved**:
- [Item 1]

**Lessons Learned**:
- [Item 1]

**Technical Debt**:
- [Item 1] (TODO: MS-Y)

## Next Steps
- Next MS: MS-(X+1)
- Scope: [Brief description]
- Blockers: [None/List]
```

**💡 DPP Example**:
```markdown
# Milestone Completion Report: MS-4

**Date**: 2026-02-12
**Duration**: 1 day
**Status**: ✅ COMPLETED

## Features Delivered
- 2-phase finalize (Claim → Upload → Commit)
- Optimistic locking with version column
- ClaimError for race condition handling

## Test Results
- Total Tests: 112
- Pass Rate: 100% (2 skipped - environment)
- Critical Tests: test_finalize_race_condition, test_2phase_commit

## Documentation Updated
- [x] README.md (2-phase commit section)
- [x] IMPLEMENTATION_REPORT.md (MS-4 section)
- [x] DEV_NOTES.md (optimistic locking pattern)

## Git Commit
- Commit: abc1234
- Message: "feat(MS-4): Implement 2-phase finalize..."

## Retrospective
**What Went Well**:
- Optimistic locking 패턴이 race condition 완전히 해결
- version column 추가로 stale update 방지

**What Could Be Improved**:
- 초기 설계 시 finalize_stage를 고려했으면 refactoring 불필요

**Lessons Learned**:
- SELECT ... FOR UPDATE는 PostgreSQL 전용 (SQLite 테스트 주의)
- Version column은 모든 상태 변경에 일관되게 적용 필요

**Technical Debt**:
- None

## Next Steps
- Next MS: MS-5 (Reaper & Reconciliation)
- Scope: Lease expiry detection, reconcile loop, AUDIT_REQUIRED alerts
- Blockers: None
```

---

## 🚨 Common Mistakes (피해야 할 것)

### ❌ "테스트는 다 돌렸어요... 아마도?"
```
문제: "아마도"는 없습니다. 명시적으로 실행하고 결과 기록.
해결: pytest -v 실행 후 스크린샷 또는 로그 저장
```

### ❌ "문서는 나중에 일괄 업데이트"
```
문제: 나중은 없습니다. 기억이 흐려집니다.
해결: MS 완료 직후 즉시 문서 업데이트 (5분 투자)
```

### ❌ "커밋 메시지: 'update'"
```
문제: 3개월 후 이 커밋이 뭔지 모름.
해결: Conventional Commits 형식 사용 (feat/fix/docs)
```

### ❌ "회고 생략"
```
문제: 같은 실수 반복, 교훈 손실
해결: 5분 회고 (What went well? What could improve?)
```

---

## 💡 Time-Saving Tips

### 1. 체크리스트 자동화
```bash
# 스크립트: check_milestone.sh
#!/bin/bash

echo "🧪 Running tests..."
cd apps/api && python -m pytest -v

echo "📝 Checking documentation..."
git diff --name-only | grep -E "(README|IMPLEMENTATION_REPORT)"

echo "🔍 Checking for hardcoded secrets..."
grep -r "aws_access_key_id" apps/ | grep -v "LocalStack"

echo "✅ Checklist complete!"
```

### 2. 커밋 메시지 템플릿
```bash
# .gitmessage
feat(MS-X): [Brief description]

[Detailed description]

- Change 1
- Change 2

Tests: [Test results]

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

# 설정:
# git config commit.template .gitmessage
```

### 3. MS 완료 보고서 템플릿
```
docs/milestones/MS-X_COMPLETION.md 템플릿을 복사해서 작성
```

---

## 🎯 Quick Reference (TL;DR)

**MS 완료 5단계**:
1. **Feature**: Acceptance Criteria 모두 만족
2. **Testing**: Full test suite 실행 (DoD 기준 만족)
3. **Documentation**: README + Report 업데이트
4. **Git Commit**: Self review + Peer review + Commit
5. **Retrospective**: 5분 회고 + Next MS planning

**핵심 원칙**:
- 체크리스트 기반으로 일관성 유지
- 문서는 즉시 업데이트 (나중은 없음)
- 회고는 짧게 (5분 이내)

---

## 📚 Related Documents

- [01_PROJECT_KICKOFF_CHECKLIST.md](01_PROJECT_KICKOFF_CHECKLIST.md)
- [02_DEFINITION_OF_DONE_TEMPLATE.md](02_DEFINITION_OF_DONE_TEMPLATE.md)
- [04_PRE_DEPLOYMENT_CHECKLIST.md](04_PRE_DEPLOYMENT_CHECKLIST.md) (Phase 1 완료 후 생성)

---

**Last Updated**: 2026-02-14
**Version**: 1.0
**Based on**: DPP API Platform v0.4.2.2 Project Experience (MS-0 to MS-6)
