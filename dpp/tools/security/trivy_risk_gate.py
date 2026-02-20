#!/usr/bin/env python3
"""
trivy_risk_gate.py — Trivy JSON → RiskScore 컷오프 게이트

사용법:
    python trivy_risk_gate.py <trivy_json_1> [<trivy_json_2> ...]

종료 코드:
    0  SECURITY_CUTOFF_OK   (risk <= 70.00)
    2  SECURITY_CUTOFF_EXCEEDED (risk > 70.00)
    3  인수 오류

RiskScore 환산:
    CVSS 0–10 스케일 → RiskScore 0–100 스케일
    RiskScore = max_cvss * 10.0
    컷오프: RiskScore > 70.00 (= CVSS > 7.0)
"""

import json
import sys
from pathlib import Path

THRESHOLD_RISK: float = 70.00   # 0–100 스케일
THRESHOLD_CVSS: float = THRESHOLD_RISK / 10.0   # = 7.0


def extract_scores(vuln: dict) -> list[float]:
    """취약점 딕셔너리에서 CVSS 점수 목록 추출 (v3 우선, 없으면 v2)."""
    scores: list[float] = []
    cvss = vuln.get("CVSS") or {}
    for _, obj in cvss.items():
        if not isinstance(obj, dict):
            continue
        v3 = obj.get("V3Score")
        v2 = obj.get("V2Score")
        if isinstance(v3, (int, float)):
            scores.append(float(v3))
        elif isinstance(v2, (int, float)):
            scores.append(float(v2))
    return scores


def main(paths: list[str]) -> int:
    max_cvss: float = 0.0
    max_hit: tuple | None = None  # (path, cve, pkg, installed, fixed, score)

    for p in paths:
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except FileNotFoundError:
            print(f"ERROR: file not found: {p}")
            return 3
        except json.JSONDecodeError as e:
            print(f"ERROR: invalid JSON in {p}: {e}")
            return 3

        results = data.get("Results") or []
        for r in results:
            vulns = r.get("Vulnerabilities") or []
            for v in vulns:
                scores = extract_scores(v)
                if not scores:
                    continue
                s = max(scores)
                if s > max_cvss:
                    max_cvss = s
                    max_hit = (
                        p,
                        v.get("VulnerabilityID", "N/A"),
                        v.get("PkgName", "N/A"),
                        v.get("InstalledVersion", "N/A"),
                        v.get("FixedVersion", "N/A"),
                        s,
                    )

    risk = round(max_cvss * 10.0, 1)
    print(
        f"TRIVY_MAX_CVSS={max_cvss:.1f} "
        f"TRIVY_RISK={risk:.1f} "
        f"THRESHOLD_RISK={THRESHOLD_RISK:.2f}"
    )

    if max_hit:
        p, cve, pkg, inst, fixed, s = max_hit
        print(
            f"TOP_HIT file={p} cve={cve} pkg={pkg} "
            f"installed={inst} fixed={fixed} cvss={s:.1f}"
        )

    if max_cvss > THRESHOLD_CVSS:
        # 실패 시 즉시 인지 가능한 한 줄 로그
        if max_hit:
            _, cve, pkg, _, _, _ = max_hit
            print(
                f"SECURITY_CUTOFF_EXCEEDED "
                f"max_cvss={max_cvss:.1f} risk={risk:.1f} "
                f"cve={cve} pkg={pkg}"
            )
        else:
            print(
                f"SECURITY_CUTOFF_EXCEEDED "
                f"max_cvss={max_cvss:.1f} risk={risk:.1f}"
            )
        return 2

    print("SECURITY_CUTOFF_OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: trivy_risk_gate.py <trivy_json_1> [<trivy_json_2> ...]",
            file=sys.stderr,
        )
        sys.exit(3)
    sys.exit(main(sys.argv[1:]))
