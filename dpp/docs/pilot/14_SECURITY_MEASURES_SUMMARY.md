# Security Measures Summary

**Decisionproof Paid Pilot - 안전성 확보조치 요약표(고객 공유용)**

문서 버전: 2026-02-16

---

## 목적

본 문서는 파일럿 고객이 빠르게 확인할 수 있도록, Decisionproof의 **기술적·관리적 보호조치(요약)**를 표 형태로 정리합니다.

- 본 문서는 “정책/설계 기준”을 우선으로 제시합니다.
- 실제 구현/운영 상태는 `05_SECURITY_PRIVACY_BASELINE.md` 및 운영 로그/증적을 근거로 확정합니다.

참고: 국내 “개인정보의 안전성 확보조치 기준”(행정규칙)에는 접근권한 관리, 접속기록 보관·점검, 암호화 등 보호조치 항목이 포함됩니다.

---

## 1) 요약 표 (체크리스트)

| 영역 | 파일럿 기본 원칙(정책) | 증빙/근거(문서/로그) | 상태 |
|---|---|---|---|
| 접근통제 | 최소권한, 역할 기반 권한(RBAC) | `05_SECURITY_PRIVACY_BASELINE.md` | [ ] |
| 인증 | Bearer 토큰, 토큰 회수/재발급 절차 | `03_SUPPORT_AND_ESCALATION.md` | [ ] |
| 암호화(전송) | TLS 적용(HTTPS) | 운영 설정/인프라 구성 | [ ] |
| 암호화(저장) | 민감 데이터 암호화 또는 상응 조치 | `05_SECURITY_PRIVACY_BASELINE.md` | [ ] |
| AWS 인프라 | 역할 기반 자격증명(IRSA/Task Role), S3 SSE(AES256/KMS), Preflight 검증 | `05_SECURITY_PRIVACY_BASELINE.md`, `scripts/aws_preflight_check.py` | [ ] |
| 로그/접속기록 | 접속기록 보관 및 위·변조 방지, 정기 점검 | 로그 정책/감사 로그 | [ ] |
| 취약점/패치 | 중요 패치 우선 적용, 변경 공지 | `09_CHANGELOG_AND_CONTACTS.md` | [ ] |
| 백업/복구 | 정기 백업 및 복구 테스트(범위 명시) | 백업 정책/리포트 | [ ] |
| 사고 대응 | 탐지→대응→복구→재발방지 | `03_SUPPORT_AND_ESCALATION.md`, `12_DISPUTE_PLAYBOOK.md` | [ ] |
| 데이터 최소화 | 필요한 범위만 수집/보관 | `08_OFFBOARDING_AND_DATA_RETENTION.md` | [ ] |
| 파기/반환 | 종료 시 반환/삭제 절차 | `08_OFFBOARDING_AND_DATA_RETENTION.md` | [ ] |

---

## 2) 고객에게 제공 가능한 “증거” 예시

- (로그) Usage Ledger ID, request_id 기반 사용량 리포트
- (정책) 데이터 보관/삭제 정책(오프보딩 문서)
- (운영) 장애 공지/포스트모템(요약)

---

## 참고(출처)

- 개인정보의 안전성 확보조치 기준(행정규칙, law.go.kr)
- 개인정보보호위원회(PIPC) 보호조치 해설/가이드(자료실)

본 문서는 법률 자문이 아니며, 실제 준수 범위는 고객 환경 및 적용 법령에 따라 달라질 수 있습니다.
