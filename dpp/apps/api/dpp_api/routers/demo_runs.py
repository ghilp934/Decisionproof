"""Demo Runs Router — Hardened Marketplace Mini Demo endpoints.

Public surface: EXACTLY 2 endpoints.
  POST /v1/demo/runs            (operationId: demo_run_create)
  GET  /v1/demo/runs/{run_id}   (operationId: demo_run_get)

Auth model (Rapid-only):
  - X-RapidAPI-Proxy-Secret == env RAPIDAPI_PROXY_SECRET  (필수, Fail-Closed)
  - Authorization: Bearer == env DP_DEMO_SHARED_TOKEN     (선택, 직접 호출 시 검증)
    → RapidAPI Runtime 경유 시 Bearer 헤더 없음 → Proxy Secret 단독으로 충분
  - On failure → 401 application/problem+json

Plan resolution:
  - X-RapidAPI-Subscription header → BASIC / PRO (default: BASIC)

Rate limits (Redis sliding window, separate POST vs GET buckets):
  - BASIC: POST 6/min, GET 24/min  |  PRO: POST 24/min, GET 96/min
  - Body size > 4096 bytes → 413
  - question > 512 chars → 422

Result delivery (COMPLETED):
  - result_inline  (<= 8KiB)
  - result_download (presigned_url TTL=600s, sha256, expires_at)
  - Fresh presigned_url generated on EVERY GET — never stored in state

AI disclosure:
  - meta.ai_generated = true, meta.ai_disclosure = AI_DISCLOSURE
  - result_inline.is_ai_generated = true, result_inline.disclaimer = AI_DISCLOSURE
  - Response headers: X-DP-AI-Generated: true, X-DP-AI-Disclosure: <string>

Retention / Tombstone:
  - BASIC: 7d retention, PRO: 30d
  - Expiry → tombstone (90d), then 404
  - After expiry: owner → 410, non-owner → 404

Zombie enforcement:
  - BASIC: 5m hard timeout, PRO: 10m → TIMEOUT status, frees active slot
"""

import hashlib
import hmac
import json
import logging
import math
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from dpp_api.pricing.problem_details import create_problem_details_response
from dpp_api.schemas_demo import AI_DISCLOSURE, DemoRunCreateRequest
from dpp_api.utils.sanitize import sanitize_log_value

logger = logging.getLogger(__name__)

# ─── Plan configuration ───────────────────────────────────────────────────────

PLAN_LIMITS: dict[str, dict[str, Any]] = {
    "BASIC": {
        "post_rpm": 6,
        "get_rpm": 24,
        "poll_min_interval_s": 3,
        "poll_max_count": 40,
        "max_active": 1,
        "zombie_timeout_s": 300,   # 5 minutes
        "retention_days": 7,
        "poll_delay_ms": 3000,
    },
    "PRO": {
        "post_rpm": 24,
        "get_rpm": 96,
        "poll_min_interval_s": 2,
        "poll_max_count": 60,
        "max_active": 3,
        "zombie_timeout_s": 600,   # 10 minutes
        "retention_days": 30,
        "poll_delay_ms": 2000,
    },
}

TOMBSTONE_TTL_S = 90 * 24 * 3600   # 90 days in seconds
MAX_BODY_BYTES = 4096
MAX_QUESTION_LEN = 512

# Mock result template (deterministic for demos)
_MOCK_RESULT_BASE = {
    "decision": "APPROVED",
    "confidence_score": 0.927,
    "reasoning": (
        "Based on the submitted question, the decision system has evaluated "
        "the request against available criteria and determined approval with "
        "high confidence."
    ),
    "factors": [
        {"name": "relevance",    "score": 0.95, "weight": 0.4},
        {"name": "completeness", "score": 0.88, "weight": 0.3},
        {"name": "clarity",      "score": 0.92, "weight": 0.3},
    ],
}


# ─── Redis key helpers ────────────────────────────────────────────────────────

def _rk_run(run_id: str) -> str:
    return f"demo:run:{run_id}"

def _rk_tombstone(run_id: str) -> str:
    return f"demo:tombstone:{run_id}"

def _rk_rate_post(actor_key: str) -> str:
    return f"demo:rate:post:{actor_key}"

def _rk_rate_get(actor_key: str) -> str:
    return f"demo:rate:get:{actor_key}"

def _rk_active(actor_key: str) -> str:
    return f"demo:active:{actor_key}"

def _rk_poll_count(actor_key: str, run_id: str) -> str:
    return f"demo:poll:count:{actor_key}:{run_id}"

def _rk_poll_last(actor_key: str, run_id: str) -> str:
    return f"demo:poll:last:{actor_key}:{run_id}"


# ─── In-memory fallback store (NOT suitable for multi-replica) ────────────────

_mem: dict[str, tuple[str, Optional[float]]] = {}
_mem_lock = threading.Lock()


def _mem_clean_expired() -> None:
    now = time.time()
    with _mem_lock:
        expired = [k for k, (_, ex) in _mem.items() if ex and now > ex]
        for k in expired:
            del _mem[k]


def _mem_set(key: str, value: str, ex: Optional[int] = None) -> None:
    expire_at = time.time() + ex if ex else None
    with _mem_lock:
        _mem[key] = (value, expire_at)


def _mem_get(key: str) -> Optional[str]:
    with _mem_lock:
        entry = _mem.get(key)
        if entry is None:
            return None
        value, expire_at = entry
        if expire_at and time.time() > expire_at:
            del _mem[key]
            return None
        return value


def _mem_incr(key: str, ex: Optional[int] = None) -> int:
    with _mem_lock:
        entry = _mem.get(key)
        if entry is None or (entry[1] and time.time() > entry[1]):
            new_val = 1
            expire_at = time.time() + ex if ex else None
        else:
            new_val = int(entry[0]) + 1
            expire_at = entry[1]
        _mem[key] = (str(new_val), expire_at)
        return new_val


def _mem_delete(key: str) -> None:
    with _mem_lock:
        _mem.pop(key, None)


def _mem_decr(key: str) -> int:
    with _mem_lock:
        entry = _mem.get(key)
        current = int(entry[0]) if entry and not (entry[1] and time.time() > entry[1]) else 0
        new_val = max(0, current - 1)
        _mem[key] = (str(new_val), entry[1] if entry else None)
        return new_val


# ─── Storage abstraction (Redis → in-memory fallback) ─────────────────────────

def _store_get(key: str) -> Optional[str]:
    try:
        from dpp_api.db.redis_client import get_redis
        return get_redis().get(key)
    except Exception:
        return _mem_get(key)


def _store_set(key: str, value: str, ex: Optional[int] = None) -> None:
    try:
        from dpp_api.db.redis_client import get_redis
        r = get_redis()
        if ex:
            r.setex(key, ex, value)
        else:
            r.set(key, value)
    except Exception:
        _mem_set(key, value, ex)


def _store_incr(key: str, ex: Optional[int] = None) -> int:
    try:
        from dpp_api.db.redis_client import get_redis
        r = get_redis()
        val = r.incr(key)
        if ex and int(val) == 1:   # Set TTL only on first increment
            r.expire(key, ex)
        return int(val)
    except Exception:
        return _mem_incr(key, ex)


def _store_decr(key: str) -> int:
    try:
        from dpp_api.db.redis_client import get_redis
        r = get_redis()
        val = r.decr(key)
        return max(0, int(val))
    except Exception:
        return _mem_decr(key)


def _store_delete(key: str) -> None:
    try:
        from dpp_api.db.redis_client import get_redis
        get_redis().delete(key)
    except Exception:
        _mem_delete(key)


# ─── Problem response helpers ─────────────────────────────────────────────────

def _make_instance() -> str:
    """RFC 9457 opaque instance URI — unique per response."""
    return f"urn:decisionproof:trace:{uuid.uuid4()}"


def _p401(detail: str) -> JSONResponse:
    return create_problem_details_response(
        type_uri="https://api.decisionproof.io.kr/problems/unauthorized",
        title="Unauthorized",
        status=401,
        detail=detail,
        instance=_make_instance(),
    )


def _p413() -> JSONResponse:
    return create_problem_details_response(
        type_uri="https://api.decisionproof.io.kr/problems/request-too-large",
        title="Request Entity Too Large",
        status=413,
        detail=f"Request body exceeds {MAX_BODY_BYTES} bytes.",
        instance=_make_instance(),
    )


def _p422(detail: str) -> JSONResponse:
    return create_problem_details_response(
        type_uri="https://api.decisionproof.io.kr/problems/validation-error",
        title="Unprocessable Entity",
        status=422,
        detail=detail,
        instance=_make_instance(),
    )


def _p429(detail: str, retry_after: int) -> JSONResponse:
    return create_problem_details_response(
        type_uri="https://iana.org/assignments/http-problem-types#quota-exceeded",
        title="Too Many Requests",
        status=429,
        detail=detail,
        instance=_make_instance(),
        headers={"Retry-After": str(retry_after)},
    )


def _p404(detail: str = "Run not found.") -> JSONResponse:
    return create_problem_details_response(
        type_uri="https://api.decisionproof.io.kr/problems/not-found",
        title="Not Found",
        status=404,
        detail=detail,
        instance=_make_instance(),
    )


def _p410(detail: str = "Run has expired and its data has been purged.") -> JSONResponse:
    return create_problem_details_response(
        type_uri="https://api.decisionproof.io.kr/problems/gone",
        title="Gone",
        status=410,
        detail=detail,
        instance=_make_instance(),
    )


# ─── Auth dependency ───────────────────────────────────────────────────────────

async def _verify_rapid_auth(request: Request) -> None:
    """Validate X-RapidAPI-Proxy-Secret (required) and Authorization: Bearer (optional).

    FAIL-CLOSED: If RAPIDAPI_PROXY_SECRET is not configured, all demo
    requests are rejected with 503. This prevents silent bypass when the
    secret is accidentally omitted in deployment. RAPIDAPI_PROXY_SECRET
    must always be set in production — there is no dev-mode bypass.

    DP_DEMO_SHARED_TOKEN is an optional second auth layer:
    - RapidAPI Runtime 경유: Bearer 헤더 없음 → Proxy Secret 단독으로 충분 (정상)
    - 직접 호출: Bearer 헤더 존재 시 검증, 틀리면 401
    - Bearer 헤더가 없으면 건너뜀 (RapidAPI 프로덕션 플로우)
    """
    expected_proxy = os.getenv("RAPIDAPI_PROXY_SECRET", "").strip()
    if not expected_proxy:
        # Server misconfiguration — fail closed, never bypass
        raise HTTPException(
            status_code=503,
            detail="missing RAPIDAPI_PROXY_SECRET",
        )

    proxy_secret = request.headers.get("X-RapidAPI-Proxy-Secret", "")
    if not hmac.compare_digest(proxy_secret.encode(), expected_proxy.encode()):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing X-RapidAPI-Proxy-Secret",
        )

    expected_token = os.getenv("DP_DEMO_SHARED_TOKEN", "")
    if expected_token:
        auth = request.headers.get("Authorization", "")
        if auth:  # RapidAPI Runtime은 Bearer 없음 → 없으면 건너뜀
            if not auth.startswith("Bearer "):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid Authorization Bearer token format",
                )
            token = auth[7:]
            if not hmac.compare_digest(token.encode(), expected_token.encode()):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid Authorization Bearer token",
                )


# ─── Plan resolution ──────────────────────────────────────────────────────────

def _resolve_plan(request: Request) -> str:
    """Derive plan from X-RapidAPI-Subscription header. Default: BASIC."""
    sub = request.headers.get("X-RapidAPI-Subscription", "").upper()
    return "PRO" if sub == "PRO" else "BASIC"


# ─── Actor key derivation (HMAC — no raw PII stored) ─────────────────────────

def _derive_actor_key(request: Request) -> str:
    """Derive stable, non-reversible actor key from request identity.

    Priority:
      1. X-RapidAPI-User header (stable per Rapid user)
      2. HMAC of Authorization header (fallback)

    Never stores raw user identifier. Only the HMAC output is used.
    """
    rapid_user = request.headers.get("X-RapidAPI-User", "").strip()
    if rapid_user:
        identifier = rapid_user
    else:
        auth = request.headers.get("Authorization", "")
        identifier = hashlib.sha256(auth.encode("utf-8")).hexdigest()

    salt = os.getenv("DEMO_ACTOR_KEY_SALT", "demo-actor-v1").encode("utf-8")
    return hmac.new(salt, identifier.encode("utf-8"), hashlib.sha256).hexdigest()


# ─── S3 helper (optional — graceful fallback if unavailable) ─────────────────

def _get_s3_for_demo():
    """Get S3 client for demo result storage. Returns None if unavailable."""
    try:
        from dpp_api.storage.s3_client import get_s3_client
        return get_s3_client()
    except Exception as e:
        logger.warning("demo: S3 client unavailable: %s", e)
        return None


def _store_result_in_s3(
    run_id: str, result_bytes: bytes
) -> Optional[tuple[str, str]]:
    """Upload result to S3. Returns (bucket, key) or None on failure."""
    s3 = _get_s3_for_demo()
    if s3 is None:
        return None
    try:
        key = f"demo/{run_id}/result.json"
        s3.upload_bytes(
            result_bytes, s3.bucket, key, content_type="application/json"
        )
        return (s3.bucket, key)
    except Exception as e:
        logger.warning("demo: S3 upload failed for %s: %s", sanitize_log_value(run_id), e)
        return None


def _generate_presigned_url(
    bucket: str, key: str
) -> Optional[tuple[str, str]]:
    """Generate fresh presigned URL. Returns (url, expires_at_iso) or None."""
    s3 = _get_s3_for_demo()
    if s3 is None:
        return None
    try:
        url, expires_at = s3.generate_presigned_url(bucket, key, ttl_seconds=600)
        return (url, expires_at.isoformat())
    except Exception as e:
        logger.warning("demo: presigned URL generation failed: %s", e)
        return None


# ─── Rate limit helpers ───────────────────────────────────────────────────────

def _check_rpm(bucket_key: str, limit: int) -> Optional[int]:
    """Check and increment a 60-second sliding window RPM bucket.

    Returns None if allowed, or retry_after_seconds if blocked.
    """
    count = _store_incr(bucket_key, ex=60)
    if count > limit:
        return 60   # Fixed: bucket resets in up to 60s
    return None


# ─── Zombie enforcement (applied on GET for active runs) ─────────────────────

def _maybe_enforce_zombie(run_data: dict, actor_key: str) -> dict:
    """If run is active and past zombie timeout, transition to TIMEOUT."""
    if run_data.get("status") not in ("QUEUED", "PROCESSING"):
        return run_data

    limits = PLAN_LIMITS[run_data.get("plan", "BASIC")]
    created_at = datetime.fromisoformat(run_data["created_at"])
    age_s = (datetime.now(timezone.utc) - created_at).total_seconds()

    if age_s > limits["zombie_timeout_s"]:
        run_data["status"] = "TIMEOUT"
        _store_decr(_rk_active(run_data.get("actor_key", actor_key)))
        run_id = run_data["run_id"]
        retention_until = datetime.fromisoformat(run_data["retention_until"])
        ttl = max(1, int((retention_until - datetime.now(timezone.utc)).total_seconds()))
        _store_set(_rk_run(run_id), json.dumps(run_data), ex=ttl)

    return run_data


# ─── Tombstone helpers ────────────────────────────────────────────────────────

def _create_tombstone(run_id: str, owner_key: str) -> None:
    """Store tombstone (no PII). Kept 90 days."""
    now = datetime.now(timezone.utc)
    tombstone = {
        "run_id": run_id,
        "owner_key": owner_key,
        "expired_at": now.isoformat(),
        "tombstone_purge_at": (now + timedelta(days=90)).isoformat(),
    }
    _store_set(_rk_tombstone(run_id), json.dumps(tombstone), ex=TOMBSTONE_TTL_S)


# ─── Router ───────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/v1/demo/runs", tags=["demo"])

_PROBLEM_JSON_MEDIA = "application/problem+json"

_401_RESPONSE = {
    "description": "Missing or invalid authentication credentials",
    "content": {
        _PROBLEM_JSON_MEDIA: {
            "example": {
                "type": "https://api.decisionproof.io.kr/problems/unauthorized",
                "title": "Unauthorized",
                "status": 401,
                "detail": "Invalid or missing X-RapidAPI-Proxy-Secret",
            }
        }
    },
}

_422_RESPONSE = {
    "description": "Request validation failed (extra key, question too long, etc.)",
    "content": {
        _PROBLEM_JSON_MEDIA: {
            "example": {
                "type": "https://api.decisionproof.io.kr/problems/validation-error",
                "title": "Unprocessable Entity",
                "status": 422,
                "detail": "Extra inputs are not permitted",
            }
        }
    },
}

_429_RESPONSE = {
    "description": "Rate limit exceeded",
    "content": {
        _PROBLEM_JSON_MEDIA: {
            "example": {
                "type": "https://iana.org/assignments/http-problem-types#quota-exceeded",
                "title": "Too Many Requests",
                "status": 429,
                "detail": "POST rate limit exceeded (6/min BASIC)",
            }
        }
    },
}


# ─── POST /v1/demo/runs ───────────────────────────────────────────────────────

@router.post(
    "",
    status_code=202,
    operation_id="demo_run_create",
    summary="Submit a Demo Decision Run",
    description=(
        "Submit a question for AI-powered decision evaluation. "
        "Returns a run_id for polling. Auth: X-RapidAPI-Proxy-Secret + Bearer token. "
        "Plans: BASIC (6 POST/min, 1 concurrent) | PRO (24 POST/min, 3 concurrent)."
    ),
    responses={
        401: _401_RESPONSE,
        413: {
            "description": "Request body exceeds 4096 bytes",
            "content": {_PROBLEM_JSON_MEDIA: {"example": {"type": "...", "title": "Request Entity Too Large", "status": 413}}},
        },
        422: _422_RESPONSE,
        429: _429_RESPONSE,
    },
)
async def create_demo_run(
    request: Request,
    _auth: None = Depends(_verify_rapid_auth),
) -> JSONResponse:
    """POST /v1/demo/runs — Create a new demo decision run."""

    # ── Plan / actor ──────────────────────────────────────────────────────────
    plan = _resolve_plan(request)
    actor_key = _derive_actor_key(request)
    limits = PLAN_LIMITS[plan]

    # ── POST RPM rate limit ───────────────────────────────────────────────────
    retry_after = _check_rpm(_rk_rate_post(actor_key), limits["post_rpm"])
    if retry_after is not None:
        return _p429(
            f"POST rate limit exceeded ({limits['post_rpm']}/min for {plan} plan)",
            retry_after,
        )

    # ── Body size guard (before Pydantic parse) ───────────────────────────────
    body_bytes = await request.body()
    if len(body_bytes) > MAX_BODY_BYTES:
        return _p413()

    # ── JSON parse ────────────────────────────────────────────────────────────
    try:
        raw = json.loads(body_bytes) if body_bytes else {}
    except json.JSONDecodeError:
        return _p422("Request body is not valid JSON.")

    # ── Pydantic validation (strict: extra='forbid') ───────────────────────────
    try:
        req = DemoRunCreateRequest.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(loc) for loc in first.get("loc", []))
        msg = first.get("msg", "Validation error")
        return _p422(f"Invalid field '{field}': {msg}")

    # ── Concurrency limit ─────────────────────────────────────────────────────
    active_str = _store_get(_rk_active(actor_key))
    active_count = int(active_str) if active_str else 0
    if active_count >= limits["max_active"]:
        return _p429(
            f"Max concurrent active runs ({limits['max_active']}) reached for {plan} plan.",
            retry_after=900,
        )

    # ── Generate run ──────────────────────────────────────────────────────────
    run_id = f"demo_{uuid.uuid4().hex[:16]}"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    retention_until = (now + timedelta(days=limits["retention_days"])).isoformat()
    retention_ttl = limits["retention_days"] * 24 * 3600

    # Derive owner_key (same logic as actor_key but with a different context label)
    owner_key = actor_key  # For demo, owner = actor

    # Hash inputs (no raw question stored)
    question = req.inputs.question
    inputs_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
    inputs_len = len(question)

    # ── Build mock AI result (synchronous "processing" for demo) ──────────────
    result_payload = dict(_MOCK_RESULT_BASE)
    result_payload["generated_at"] = now_iso
    result_payload["is_ai_generated"] = True
    result_payload["disclaimer"] = AI_DISCLOSURE
    result_bytes = json.dumps(result_payload, ensure_ascii=False).encode("utf-8")
    result_sha256 = hashlib.sha256(result_bytes).hexdigest()

    # Try S3 upload (optional — graceful fallback)
    s3_info = _store_result_in_s3(run_id, result_bytes)

    # ── Persist run state ─────────────────────────────────────────────────────
    run_data: dict[str, Any] = {
        "run_id": run_id,
        "status": "COMPLETED",   # Immediate for demo (no real async worker)
        "plan": plan,
        "created_at": now_iso,
        "owner_key": owner_key,
        "actor_key": actor_key,
        "inputs_hash": inputs_hash,
        "inputs_len": inputs_len,
        "result_sha256": result_sha256,
        "retention_until": retention_until,
    }
    if s3_info:
        run_data["result_bucket"] = s3_info[0]
        run_data["result_key"] = s3_info[1]
    else:
        # Fallback: store result inline in Redis (≤ 8KiB)
        run_data["result_inline_json"] = result_payload

    _store_set(_rk_run(run_id), json.dumps(run_data), ex=retention_ttl)

    # No active slot consumed (run is immediately COMPLETED)

    # ── Build receipt ─────────────────────────────────────────────────────────
    poll_url = f"/v1/demo/runs/{run_id}"
    receipt = {
        "run_id": run_id,
        "status": "QUEUED",     # Spec-compliant receipt status
        "poll_url": poll_url,
        "created_at": now_iso,
        "poll": {
            "recommended_delay_ms": limits["poll_delay_ms"],
        },
        "meta": {
            "ai_generated": True,
            "ai_disclosure": AI_DISCLOSURE,
            "plan": plan,
        },
    }

    return JSONResponse(
        status_code=202,
        content=receipt,
        headers={
            "Cache-Control": "no-store",
            "X-DP-AI-Generated": "true",
            "X-DP-AI-Disclosure": AI_DISCLOSURE,
        },
    )


# ─── GET /v1/demo/runs/{run_id} ───────────────────────────────────────────────

@router.get(
    "/{run_id}",
    status_code=200,
    operation_id="demo_run_get",
    summary="Poll Demo Run Status",
    description=(
        "Poll the status of a demo run. Returns COMPLETED with inline result "
        "and a fresh presigned download URL (TTL=600s). "
        "Min polling interval: BASIC 3s, PRO 2s. Max polls: BASIC 40, PRO 60."
    ),
    responses={
        401: _401_RESPONSE,
        404: {
            "description": "Run not found (or expired tombstone purged)",
            "content": {_PROBLEM_JSON_MEDIA: {"example": {"type": "...", "title": "Not Found", "status": 404}}},
        },
        410: {
            "description": "Run has expired and data has been purged (owner only)",
            "content": {_PROBLEM_JSON_MEDIA: {"example": {"type": "...", "title": "Gone", "status": 410}}},
        },
        429: _429_RESPONSE,
    },
)
async def get_demo_run(
    run_id: str,
    request: Request,
    _auth: None = Depends(_verify_rapid_auth),
) -> JSONResponse:
    """GET /v1/demo/runs/{run_id} — Poll demo run status."""

    # ── Plan / actor ──────────────────────────────────────────────────────────
    plan = _resolve_plan(request)
    actor_key = _derive_actor_key(request)
    limits = PLAN_LIMITS[plan]

    # ── GET RPM rate limit ────────────────────────────────────────────────────
    retry_after = _check_rpm(_rk_rate_get(actor_key), limits["get_rpm"])
    if retry_after is not None:
        return _p429(
            f"GET rate limit exceeded ({limits['get_rpm']}/min for {plan} plan)",
            retry_after,
        )

    # ── Tombstone check (expiry / deletion) ───────────────────────────────────
    tombstone_str = _store_get(_rk_tombstone(run_id))
    if tombstone_str:
        try:
            tombstone = json.loads(tombstone_str)
            if tombstone.get("owner_key") == actor_key:
                return _p410()
        except (json.JSONDecodeError, KeyError):
            pass
        return _p404()

    # ── Load run ──────────────────────────────────────────────────────────────
    run_str = _store_get(_rk_run(run_id))
    if not run_str:
        return _p404()

    try:
        run_data: dict = json.loads(run_str)
    except json.JSONDecodeError:
        logger.error("demo: corrupt run_data for %s", sanitize_log_value(run_id))
        return _p404()

    # ── Retention / expiry check ──────────────────────────────────────────────
    retention_until = datetime.fromisoformat(run_data["retention_until"])
    if datetime.now(timezone.utc) > retention_until:
        _create_tombstone(run_id, run_data.get("owner_key", ""))
        _store_delete(_rk_run(run_id))
        if run_data.get("owner_key") == actor_key:
            return _p410()
        return _p404()

    # ── Zombie enforcement ────────────────────────────────────────────────────
    run_data = _maybe_enforce_zombie(run_data, actor_key)

    # ── Poll rate limiting (per actor+run_id) ─────────────────────────────────
    now_ts = time.time()
    last_poll_str = _store_get(_rk_poll_last(actor_key, run_id))
    if last_poll_str is not None:
        elapsed = now_ts - float(last_poll_str)
        min_interval = limits["poll_min_interval_s"]
        if elapsed < min_interval:
            retry_after_s = math.ceil(min_interval - elapsed)
            return _p429(
                f"Polling too fast. Minimum interval: {min_interval}s for {plan} plan.",
                retry_after_s,
            )

    poll_count = int(_store_get(_rk_poll_count(actor_key, run_id)) or "0")
    if poll_count >= limits["poll_max_count"]:
        return _p429(
            f"Maximum poll count ({limits['poll_max_count']}) reached for this run.",
            retry_after=900,
        )

    # Update poll tracking (only on successful pass)
    _store_set(_rk_poll_last(actor_key, run_id), str(now_ts), ex=TOMBSTONE_TTL_S)
    _store_incr(_rk_poll_count(actor_key, run_id), ex=limits["retention_days"] * 86400)

    # ── Build response ────────────────────────────────────────────────────────
    status = run_data["status"]
    now_iso = datetime.now(timezone.utc).isoformat()

    base_meta = {
        "ai_generated": True,
        "ai_disclosure": AI_DISCLOSURE,
        "plan": run_data.get("plan", plan),
    }

    response_headers = {
        "Cache-Control": "no-store",
        "X-DP-AI-Generated": "true",
        "X-DP-AI-Disclosure": AI_DISCLOSURE,
    }

    if status == "COMPLETED":
        # Build result_inline from stored data
        if "result_inline_json" in run_data:
            result_inline = dict(run_data["result_inline_json"])
        else:
            result_inline = dict(_MOCK_RESULT_BASE)
            result_inline["generated_at"] = run_data.get("created_at", now_iso)
        result_inline["is_ai_generated"] = True
        result_inline["disclaimer"] = AI_DISCLOSURE

        # Generate FRESH presigned URL (never stored, never reused)
        result_download: Optional[dict] = None
        if "result_bucket" in run_data and "result_key" in run_data:
            presigned_info = _generate_presigned_url(
                run_data["result_bucket"], run_data["result_key"]
            )
            if presigned_info:
                result_download = {
                    "presigned_url": presigned_info[0],
                    "sha256": run_data.get("result_sha256"),
                    "expires_at": presigned_info[1],
                }

        content = {
            "run_id": run_id,
            "status": "COMPLETED",
            "created_at": run_data["created_at"],
            "meta": base_meta,
            "result_inline": result_inline,
        }
        if result_download:
            content["result_download"] = result_download

        return JSONResponse(status_code=200, content=content, headers=response_headers)

    # Non-terminal: return current status with poll hints
    content = {
        "run_id": run_id,
        "status": status,
        "created_at": run_data["created_at"],
        "meta": base_meta,
        "poll": {
            "recommended_delay_ms": limits["poll_delay_ms"],
        },
    }
    return JSONResponse(status_code=200, content=content, headers=response_headers)
