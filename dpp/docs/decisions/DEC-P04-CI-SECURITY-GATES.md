# DEC-P04-CI-SECURITY-GATES: CI Security Scanning Gates — Spec Lock

**Decision ID**: DEC-P04-CI-SECURITY-GATES
**Phase**: Phase 4.2
**Status**: LOCKED
**Date**: 2026-02-20
**Author**: DPP DevOps

---

## 결정 (Decision)

RC Gates CI 워크플로우(`.github/workflows/rc_gates.yml`)에 SAST(CodeQL) 및 컨테이너 이미지 취약점 스캔(Trivy)을 필수 게이트로 추가. RiskScore > 70.00이면 RC Gates 실행 전 빌드를 즉시 중단.

---

## 컷오프 규칙 (Non-Negotiable)

| 항목 | 값 |
|------|-----|
| 스캔 도구 | Trivy (`aquasecurity/trivy-action@v0.34.0`) |
| 스캔 대상 | 로컬 빌드 이미지 3종 (`decisionproof-{api,worker,reaper}:rc5`) |
| CVSS 스케일 | 0–10 (원본) |
| RiskScore 환산 | `RiskScore = max_cvss × 10.0` (0–100 스케일) |
| 컷오프 | `RiskScore > 70.00` → 즉시 FAIL |
| 컷오프 구현체 | `dpp/tools/security/trivy_risk_gate.py` (exit 2) |
| CVSS 우선순위 | V3Score 우선, 없으면 V2Score |

**계산 예시**:
- CVSS 7.1 → RiskScore 71.0 → **FAIL**
- CVSS 7.0 → RiskScore 70.0 → PASS (경계값 포함)
- CVSS 9.8 → RiskScore 98.0 → **FAIL**

---

## SAST 규칙

| 항목 | 값 |
|------|-----|
| 도구 | CodeQL (`github/codeql-action/*@v4`) |
| 언어 | Python |
| 쿼리셋 | `security-and-quality` |
| 적용 대상 | same-repo branch PR, push, schedule |
| Fork PR | skip (권한 제한) |
| 권한 | job-level `security-events: write` (최소권한 원칙) |

---

## 실행 순서

```
sast_codeql (SAST — CodeQL)
    └─ rc_gates_linux (Trivy 스캔 → RiskScore 컷오프 → RC Gates)
```

- `sast_codeql` 실패 → `rc_gates_linux` 진행 불가
- `sast_codeql` skip(fork PR) → `rc_gates_linux` 정상 진행
- RiskScore > 70.00 → RC Gates 실행 전 job FAIL

---

## 금지 사항 (NON-NEGOTIABLES)

- ❌ ECR/Registry 이미지 푸시 금지 (Phase 4.2 범위 아님 — 로컬 빌드·스캔만)
- ❌ `exit-code: '1'` Trivy 옵션으로 컷오프 우회 금지 (커스텀 게이트가 제어)
- ❌ DEC 결정 문서 없이 컷오프 기준(70.00) 변경 금지
- ❌ `continue-on-error: true`로 보안 게이트 스텝 우회 금지
- ❌ `--skip-db-update` 등 Trivy DB 업데이트 비활성화 금지

---

## 증적 경로

```
dpp/evidence/01_ci/security/
├── trivy_image_api.json       # decisionproof-api:rc5 전체 스캔 결과
├── trivy_image_worker.json    # decisionproof-worker:rc5 전체 스캔 결과
├── trivy_image_reaper.json    # decisionproof-reaper:rc5 전체 스캔 결과
└── trivy_summary.txt          # RiskScore 요약 + 컷오프 결과
```

---

## 관련 파일

| 역할 | 파일 |
|------|------|
| 워크플로우 | `.github/workflows/rc_gates.yml` |
| 컷오프 구현체 | `dpp/tools/security/trivy_risk_gate.py` |
| 운영 런북 | `ops/runbooks/staging_dry_run.md` (Phase 4.2 섹션) |
