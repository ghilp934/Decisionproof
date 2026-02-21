#!/usr/bin/env bash
# =============================================================================
# P6.4: Evidence Sealing Helper
# Called by pilot_cutover_run.sh — do not call directly unless re-running seal.
# =============================================================================
# Usage:
#   seal_evidence_to_worm.sh <EVID_DIR> <TS>
#
# Steps:
#   1. Create evidence.tar.gz from all evidence files
#   2. Compute SHA-256 of the tarball
#   3. Compute Object Lock retain-until date
#   4. Upload to S3 WORM bucket with Object Lock parameters
#   5. Verify via head-object (VersionId + lock mode)
#   6. Write 60_evidence_seal_manifest.json
#
# Required ENV:
#   DPP_WORM_BUCKET              S3 bucket with Object Lock + versioning enabled
#   DPP_WORM_PREFIX              S3 key prefix (without trailing slash)
#   DPP_WORM_OBJECT_LOCK_MODE    GOVERNANCE or COMPLIANCE  (default: GOVERNANCE)
#   DPP_WORM_RETENTION_DAYS      Retention in days         (default: 365)
#   AWS_REGION                   AWS region
#   AWS_PROFILE                  AWS CLI profile           (default: dpp-admin)
#
# Exit codes:
#   0 — success
#   1 — any failure (tar, sha256, upload, verification)
# =============================================================================

set -euo pipefail

EVID_DIR="${1:?'Usage: seal_evidence_to_worm.sh <EVID_DIR> <TS>'}"
TS="${2:?'Usage: seal_evidence_to_worm.sh <EVID_DIR> <TS>'}"

if [[ ! -d "${EVID_DIR}" ]]; then
    echo "ERROR: EVID_DIR does not exist: ${EVID_DIR}" >&2
    exit 1
fi

# ── AWS Profile (MANDATORY per DPP ops rules) ─────────────────────────────────
export AWS_PROFILE="${AWS_PROFILE:-dpp-admin}"

# ── Required env validation ───────────────────────────────────────────────────
: "${DPP_WORM_BUCKET:?'DPP_WORM_BUCKET is required'}"
: "${DPP_WORM_PREFIX:?'DPP_WORM_PREFIX is required'}"
: "${AWS_REGION:?'AWS_REGION is required'}"

DPP_WORM_OBJECT_LOCK_MODE="${DPP_WORM_OBJECT_LOCK_MODE:-GOVERNANCE}"
DPP_WORM_RETENTION_DAYS="${DPP_WORM_RETENTION_DAYS:-365}"

# ── Paths ─────────────────────────────────────────────────────────────────────
TARBALL="${EVID_DIR}/evidence.tar.gz"
SHA256FILE="${TARBALL}.sha256"
MANIFEST="${EVID_DIR}/60_evidence_seal_manifest.json"
UPLOAD_LOG="${EVID_DIR}/70_worm_upload_stdout.txt"

# S3 key — deterministic: prefix/phase6_4_cutover/<TS>/evidence.tar.gz
S3_KEY="${DPP_WORM_PREFIX%/}/phase6_4_cutover/${TS}/evidence.tar.gz"

_log() { echo "[seal $(date -u '+%H:%M:%SZ')] $*"; }

# =============================================================================
# Step 1: Create tarball
# =============================================================================
_log "Step 4a: Creating tarball..."
# Exclude the tarball itself and sha256 to avoid recursive inclusion
tar -czf "${TARBALL}" \
    -C "${EVID_DIR}" \
    --exclude="./evidence.tar.gz" \
    --exclude="./evidence.tar.gz.sha256" \
    .

TARBALL_SIZE="$(du -sh "${TARBALL}" 2>/dev/null | cut -f1)"
_log "  Tarball: ${TARBALL} (${TARBALL_SIZE})"

# =============================================================================
# Step 2: SHA-256
# =============================================================================
_log "Step 4b: Computing SHA-256..."
if command -v sha256sum &>/dev/null; then
    sha256sum "${TARBALL}" > "${SHA256FILE}"
elif command -v shasum &>/dev/null; then
    shasum -a 256 "${TARBALL}" > "${SHA256FILE}"
else
    echo "ERROR: Neither sha256sum nor shasum found" >&2
    exit 1
fi

SHA256_VALUE="$(awk '{print $1}' "${SHA256FILE}")"
_log "  SHA-256: ${SHA256_VALUE}"

# =============================================================================
# Step 3: Compute retain-until date
# =============================================================================
_log "Step 4c: Computing retain-until (${DPP_WORM_RETENTION_DAYS} days from now)..."
# GNU date vs BSD date (macOS)
if date --version &>/dev/null 2>&1; then
    RETAIN_UNTIL="$(date -u -d "+${DPP_WORM_RETENTION_DAYS} days" '+%Y-%m-%dT%H:%M:%SZ')"
else
    RETAIN_UNTIL="$(date -u -v+${DPP_WORM_RETENTION_DAYS}d '+%Y-%m-%dT%H:%M:%SZ')"
fi
_log "  Retain until: ${RETAIN_UNTIL}"

# =============================================================================
# Step 4: S3 put-object with Object Lock
# =============================================================================
_log "Step 5a: Uploading to S3 WORM bucket..."
_log "  Bucket : ${DPP_WORM_BUCKET} (redacted in manifest)"
_log "  Key    : ${S3_KEY}"
_log "  Mode   : ${DPP_WORM_OBJECT_LOCK_MODE}"

{
    echo "=== S3 put-object ==="
    echo "Timestamp  : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "S3 Key     : ${S3_KEY}"
    echo "Lock Mode  : ${DPP_WORM_OBJECT_LOCK_MODE}"
    echo "Retain Until: ${RETAIN_UNTIL}"
    echo ""
} > "${UPLOAD_LOG}"

UPLOAD_JSON="$(aws s3api put-object \
    --region "${AWS_REGION}" \
    --bucket "${DPP_WORM_BUCKET}" \
    --key "${S3_KEY}" \
    --body "${TARBALL}" \
    --object-lock-mode "${DPP_WORM_OBJECT_LOCK_MODE}" \
    --object-lock-retain-until-date "${RETAIN_UNTIL}" \
    --output json \
    2>&1)"

echo "${UPLOAD_JSON}" | tee -a "${UPLOAD_LOG}"

VERSION_ID="$(echo "${UPLOAD_JSON}" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('VersionId','UNKNOWN'))" \
    2>/dev/null || echo "UNKNOWN")"
ETAG="$(echo "${UPLOAD_JSON}" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('ETag','UNKNOWN').strip('\"'))" \
    2>/dev/null || echo "UNKNOWN")"

_log "  Upload complete — VersionId=${VERSION_ID}  ETag=${ETAG}"

# =============================================================================
# Step 5: head-object verification
# =============================================================================
_log "Step 5b: Verifying via head-object..."
{
    echo ""
    echo "=== S3 head-object ==="
    echo "Timestamp: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo ""
} >> "${UPLOAD_LOG}"

HEAD_JSON="$(aws s3api head-object \
    --region "${AWS_REGION}" \
    --bucket "${DPP_WORM_BUCKET}" \
    --key "${S3_KEY}" \
    --output json \
    2>&1)"

echo "${HEAD_JSON}" | tee -a "${UPLOAD_LOG}"

VERIFIED_VERSION="$(echo "${HEAD_JSON}" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('VersionId','UNKNOWN'))" \
    2>/dev/null || echo "UNKNOWN")"
VERIFIED_LOCK="$(echo "${HEAD_JSON}" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('ObjectLockMode','NOT_SET'))" \
    2>/dev/null || echo "NOT_SET")"
VERIFIED_UNTIL="$(echo "${HEAD_JSON}" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('ObjectLockRetainUntilDate',''))" \
    2>/dev/null || echo "")"

if [[ "${VERIFIED_VERSION}" == "UNKNOWN" ]] || [[ -z "${VERIFIED_VERSION}" ]]; then
    echo "" >> "${UPLOAD_LOG}"
    echo "ERROR: head-object verification failed — VersionId not returned" >> "${UPLOAD_LOG}"
    _log "ERROR: head-object verification failed"
    exit 1
fi

_log "  Verified — VersionId=${VERIFIED_VERSION}  LockMode=${VERIFIED_LOCK}"

# =============================================================================
# Step 6: Write seal manifest JSON
# =============================================================================
_log "Step 5c: Writing seal manifest..."
python3 - << PYEOF
import json, os

manifest = {
    "schema_version": "1.0",
    "generated_at": "${TS}",
    "tarball_path": "${TARBALL}",
    "tarball_size_bytes": os.path.getsize("${TARBALL}"),
    "sha256": "${SHA256_VALUE}",
    "s3_key": "${S3_KEY}",
    "s3_bucket": "<BUCKET_REDACTED_SEE_ENV>",
    "lock_mode": "${DPP_WORM_OBJECT_LOCK_MODE}",
    "retain_until": "${RETAIN_UNTIL}",
    "retention_days": int("${DPP_WORM_RETENTION_DAYS}"),
    "version_id": "${VERIFIED_VERSION}",
    "etag": "${ETAG}",
    "verified_lock_mode": "${VERIFIED_LOCK}",
    "verified_retain_until": "${VERIFIED_UNTIL}"
}

with open("${MANIFEST}", "w") as f:
    json.dump(manifest, f, indent=2)

print(json.dumps(manifest, indent=2))
PYEOF

_log "  Manifest written: ${MANIFEST}"
_log "Sealing COMPLETE."
