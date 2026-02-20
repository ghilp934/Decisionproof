#!/usr/bin/env python3
"""P6.3 Webhook Concurrency Bomb — Production Rehearsal Script.

Sends N identical webhook requests simultaneously to prove the idempotency gate
under concurrent load. Only 1 request should trigger business processing (DB commit);
all others should receive 200 already_processed with zero side effects.

Usage:
    python p6_3_webhook_concurrency_bomb.py \\
        --url http://localhost:8000/webhooks/paypal \\
        --n 5 \\
        --payload .local/webhook_payload.json \\
        --headers .local/webhook_headers.json \\
        --timeout 10

Output:
    status_code | latency_ms | response_body (truncated)
    ── summary: 200=5, 4xx=0, 5xx=0
    ── processed=1, already_processed=4

Security note:
    This script logs payload_hash/size only (not raw body content).
    Headers file must be kept in .local/ (gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any


def _compute_payload_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


async def _send_one(
    session: Any,
    url: str,
    raw_body: bytes,
    headers: dict[str, str],
    timeout: float,
    idx: int,
) -> dict[str, Any]:
    """Send a single request and return structured result."""
    start = time.monotonic()
    try:
        resp = await session.post(url, content=raw_body, headers=headers, timeout=timeout)
        elapsed_ms = (time.monotonic() - start) * 1000
        try:
            body = resp.json()
        except Exception:
            body = {"_raw": resp.text[:200]}
        return {
            "idx": idx,
            "status": resp.status_code,
            "latency_ms": round(elapsed_ms, 1),
            "response_status": body.get("status", "unknown"),
            "error_code": body.get("error_code"),
        }
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        return {
            "idx": idx,
            "status": -1,
            "latency_ms": round(elapsed_ms, 1),
            "response_status": "network_error",
            "error_code": type(exc).__name__,
        }


async def _run_bomb(
    url: str,
    raw_body: bytes,
    headers: dict[str, str],
    n: int,
    timeout: float,
) -> list[dict[str, Any]]:
    """Fire N requests concurrently and collect results."""
    try:
        import httpx
    except ImportError:
        print("ERROR: httpx is required. Install: pip install httpx", file=sys.stderr)
        sys.exit(1)

    async with httpx.AsyncClient() as client:
        tasks = [
            _send_one(client, url, raw_body, headers, timeout, i)
            for i in range(1, n + 1)
        ]
        results = await asyncio.gather(*tasks)

    return list(results)


def _print_report(
    results: list[dict[str, Any]],
    payload_hash: str,
    payload_size: int,
) -> int:
    """Print per-request table + summary. Returns exit code (0=pass, 1=fail)."""
    print()
    print("=" * 68)
    print(f"  P6.3 Webhook Concurrency Bomb Results")
    print(f"  payload_hash={payload_hash[:16]}...  size={payload_size}B")
    print("=" * 68)
    print(f"  {'#':>3}  {'HTTP':>4}  {'ms':>7}  {'response_status':<22}  error_code")
    print("  " + "-" * 64)

    status_counts: dict[int, int] = {}
    response_status_counts: dict[str, int] = {}

    for r in sorted(results, key=lambda x: x["idx"]):
        sc = r["status"]
        rs = r["response_status"]
        status_counts[sc] = status_counts.get(sc, 0) + 1
        response_status_counts[rs] = response_status_counts.get(rs, 0) + 1

        flag = ""
        if rs == "processed":
            flag = " ◀ FIRST"
        elif sc >= 500:
            flag = " ◀ ERROR"

        err = r.get("error_code") or ""
        print(
            f"  {r['idx']:>3}  {sc:>4}  {r['latency_ms']:>7.1f}  {rs:<22}  {err}{flag}"
        )

    print()
    print("  ── HTTP status summary ──────────────────────")
    for code in sorted(status_counts):
        label = "2xx" if 200 <= code < 300 else ("4xx" if 400 <= code < 500 else ("5xx" if code >= 500 else "ERR"))
        print(f"     {code} ({label}): {status_counts[code]}")

    print()
    print("  ── Idempotency summary ──────────────────────")
    for rs, count in sorted(response_status_counts.items()):
        print(f"     {rs}: {count}")

    processed = response_status_counts.get("processed", 0)
    total_2xx = sum(v for k, v in status_counts.items() if 200 <= k < 300)
    total_5xx = sum(v for k, v in status_counts.items() if k >= 500)

    print()
    ok = processed == 1 and total_5xx == 0
    verdict = "PASS" if ok else "FAIL"
    print(f"  Verdict: {verdict}")
    if not ok:
        if processed != 1:
            print(f"    FAIL: expected 1 'processed', got {processed}")
        if total_5xx > 0:
            print(f"    FAIL: {total_5xx} 5xx error(s) — check server logs")
    print("=" * 68)
    print()

    return 0 if ok else 1


def _load_json_file(path_str: str, label: str) -> dict:
    path = Path(path_str)
    if not path.exists():
        print(f"ERROR: {label} file not found: {path}", file=sys.stderr)
        print(
            f"  Copy the sample: cp {path.with_name(path.stem + '.sample' + path.suffix)} {path}",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: {label} file is not valid JSON ({path}): {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="P6.3: Send N concurrent identical webhooks to verify idempotency gate",
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Webhook endpoint URL (e.g. http://localhost:8000/webhooks/paypal)",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=5,
        help="Number of concurrent requests (default: 5)",
    )
    parser.add_argument(
        "--payload",
        default=".local/webhook_payload.json",
        help="Path to webhook payload JSON file (default: .local/webhook_payload.json)",
    )
    parser.add_argument(
        "--headers",
        default=".local/webhook_headers.json",
        help="Path to webhook headers JSON file (default: .local/webhook_headers.json)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds (default: 10)",
    )
    args = parser.parse_args()

    # Load inputs
    payload_data = _load_json_file(args.payload, "payload")
    headers_data = _load_json_file(args.headers, "headers")

    raw_body = json.dumps(payload_data, ensure_ascii=False).encode("utf-8")
    payload_hash = _compute_payload_hash(raw_body)
    payload_size = len(raw_body)

    # Merge Content-Type if missing
    headers: dict[str, str] = {str(k): str(v) for k, v in headers_data.items()}
    headers.setdefault("Content-Type", "application/json")

    print(f"  Target URL   : {args.url}")
    print(f"  Concurrency  : {args.n} simultaneous requests")
    print(f"  Payload hash : {payload_hash[:32]}...")
    print(f"  Payload size : {payload_size} bytes")
    print(f"  Timeout      : {args.timeout}s per request")
    print()
    print(f"  Firing {args.n} identical requests...")

    results = asyncio.run(_run_bomb(args.url, raw_body, headers, args.n, args.timeout))
    exit_code = _print_report(results, payload_hash, payload_size)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
