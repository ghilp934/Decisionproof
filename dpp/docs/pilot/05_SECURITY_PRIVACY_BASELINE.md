# Security and Privacy Baseline

**Decisionproof API - 보안 및 개인정보 기준**

---

## 보안 기준

### API 토큰 취급

**필수 규칙:**
- [ ] 토큰은 환경변수 또는 Vault에 저장 (코드에 하드코딩 금지)
- [ ] 로그/디버그 출력에 토큰 노출 금지
- [ ] 토큰 공유 금지 (1 고객 = 1 토큰)
- [ ] 토큰 노출 의심 시 즉시 재발급 요청

**검증 방법:**
```bash
# 토큰이 로그에 남지 않는지 확인
grep -r "<STAGING_TOKEN>" /var/log/app.log  # 결과 없어야 함
```

### Rate Limit 준수

**제한:**
- STARTER: 60 RPM

**준수 방법:**
```python
# Example: Python with rate limiting
import time
from collections import deque

class RateLimiter:
    def __init__(self, max_calls=60, period=60):
        self.calls = deque()
        self.max_calls = max_calls
        self.period = period

    def wait_if_needed(self):
        now = time.time()
        while self.calls and self.calls[0] < now - self.period:
            self.calls.popleft()
        if len(self.calls) >= self.max_calls:
            sleep_time = self.period - (now - self.calls[0])
            time.sleep(sleep_time)
        self.calls.append(now)
```

### AWS 인프라 보안 (P0)

**필수 규칙:**
- AWS는 역할 기반 자격증명(Task Role/IRSA) 우선, 정적 키 주입 방지 (Guardrails)
- S3 결과물 저장 시 SSE (Server-Side Encryption) 적용 (기본 AES256, 옵션 KMS)
- 배포 전 `aws_preflight_check.py`로 환경/계정 확인

### HTTPS 전용

**필수:**
- [ ] 모든 API 호출은 HTTPS (`https://staging-api.decisionproof.ai`)
- [ ] HTTP로 다운그레이드 금지
- [ ] 인증서 검증 비활성화 금지 (`curl -k` 금지)

---

## 개인정보 처리 체크리스트

### 처리 범위

Decisionproof API 사용 중 개인정보가 처리될 수 있는 지점:

**1. API 요청/응답 데이터**
- [ ] 고객이 `/v1/runs` 입력에 개인정보 포함 가능
- [ ] 예: 이름, 이메일, 전화번호 등
- [ ] 원칙: **최소 수집** (필요한 경우에만 포함)

**2. 로그 및 모니터링**
- [ ] API 요청 로그에 일부 데이터 기록 가능
- [ ] 보관 기간: 90일 (이후 자동 삭제)
- [ ] 접근 권한: 운영팀만 (보안 통제)

**3. 지원 티켓**
- [ ] 고객이 지원 요청 시 증빙 데이터 제출 가능
- [ ] 처리 원칙: 문제 해결 목적으로만 사용
- [ ] 삭제: 티켓 종료 후 30일 보관, 이후 삭제

### 개인정보 위탁/수탁

**위탁자:** 파일럿 고객
**수탁자:** Decisionproof (당사)
**위탁 업무:** API 서비스 제공, 로그 보관, 지원 처리

**수탁자 의무:**
- [ ] 개인정보 보안 조치 (암호화, 접근 통제)
- [ ] 보관 기간 준수 (90일 + 30일)
- [ ] 재위탁 금지 (별도 협의 없이)
- [ ] 파기 절차: 보관 기간 경과 시 자동 삭제

**참고:** 이는 체크리스트이며 법률 자문 아님. 개인정보 처리 계약은 별도 체결 권장.

### 보관 및 파기

**보관 기간:**
- API 로그: 90일
- 지원 티켓: 30일 (종료 후)
- 과금 데이터: 5년 (세법 준수)

**파기 방법:**
- 자동 삭제 스크립트 (cron)
- 복구 불가능하게 삭제

---

## 보안 사고 대응

### 의심 징후

다음 징후 발견 시 즉시 보고:
- [ ] 비정상적인 요청 패턴 (평소 대비 10배 이상)
- [ ] 타사 IP에서의 토큰 사용 감지
- [ ] 401 오류 급증 (토큰 탈취 시도)

### 보고 절차

1. **즉시 조치**: 의심 토큰 사용 중단
2. **증빙 수집**: 로그, request_id, 발생 시각
3. **보고**: ghilplip934@gmail.com (제목: [보안 사고] ...)
4. **대응**: 당사에서 토큰 무효화 + 재발급

---

## 다음 단계

1. 토큰 보안 규칙 준수 확인
2. 개인정보 처리 범위 파악 및 내부 정책 수립
3. 보안 사고 대응 절차 숙지
