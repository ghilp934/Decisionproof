#!/usr/bin/env python3
"""
db_url_verify_full_check.py — DATABASE_URL SSL 모드 강제 검증

입력:  DATABASE_URL 환경변수 (값 자체를 출력·로그 금지)
출력:  sslmode=verify-full 여부 PASS/FAIL + 마스킹된 host 요약만

종료 코드:
    0  PASS  (sslmode=verify-full 확인됨)
    1  FAIL  (sslmode 불일치 또는 미설정)
    2  ERROR (파싱 오류 또는 환경변수 미설정)

규칙 (Non-Negotiables):
    - DATABASE_URL 전체 문자열 출력 금지
    - 패스워드 절대 출력 금지 (urlparse 결과도 마스킹)
    - host는 앞 3자 + *** + 나머지 도메인 형태로 마스킹
    - 오류 메시지에 실제 값 포함 금지
"""

import os
import sys
from urllib.parse import urlparse, parse_qs


def mask_host(host: str) -> str:
    """
    호스트 마스킹 (값 은닉).

    예: aws-0-ap-northeast-2.pooler.supabase.com
      → aws***pooler.supabase.com
    """
    if not host:
        return "[empty]"
    parts = host.split(".")
    if len(parts) >= 2:
        first = parts[0][:3] + "***"
        return first + "." + ".".join(parts[1:])
    return host[:3] + "***"


def main() -> int:
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        print("FAIL: DATABASE_URL is not set or empty", file=sys.stderr)
        return 2

    try:
        parsed = urlparse(raw)
    except Exception:
        # 파싱 실패 — 값 포함 메시지 금지
        print("ERROR: DATABASE_URL parse error (detail hidden for security)", file=sys.stderr)
        return 2

    # sslmode는 query string에서 추출
    qs = parse_qs(parsed.query, keep_blank_values=True)
    sslmode_list = qs.get("sslmode", [])
    sslmode = sslmode_list[0] if sslmode_list else None

    # host 마스킹 (값 노출 없이 식별 가능한 요약)
    masked_host = mask_host(parsed.hostname or "")
    port = parsed.port or "(default)"
    scheme = parsed.scheme or "(none)"

    print(
        f"DB_SSL_CHECK scheme={scheme} host={masked_host} port={port} "
        f"sslmode_found={sslmode!r}"
    )

    if sslmode != "verify-full":
        print(
            f"FAIL: sslmode={sslmode!r} — required=verify-full. "
            f"Update DATABASE_URL in AWS Secrets Manager (decisionproof/staging/dpp-secrets)."
        )
        return 1

    print("PASS: sslmode=verify-full confirmed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
