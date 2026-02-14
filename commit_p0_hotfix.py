"""Commit P0 Hotfix changes"""
import subprocess
import os
import shutil

BASE_DIR = r"C:\Users\ghilp\OneDrive\바탕 화면\배성무일반\0_디플런트 D!FFERENT\Decisionwise\decisionwise_api_platform"
os.chdir(BASE_DIR)

print("=== P0 Hotfix Commit ===\n")

# 1. Sync docs to public/docs
print("1. Syncing docs to public/docs...")
docs_files = ["quickstart.md", "auth.md"]
for doc in docs_files:
    src = os.path.join(BASE_DIR, "dpp", "docs", doc)
    dst = os.path.join(BASE_DIR, "dpp", "public", "docs", doc)
    if os.path.exists(src):
        shutil.copy2(src, dst)
        print(f"   [OK] Copied {doc}")

# 2. Git status
print("\n2. Checking git status...")
result = subprocess.run(["git", "status", "--short"], capture_output=True, text=True)
print(result.stdout[:1000])

# 3. Add all changes
print("\n3. Staging all changes...")
subprocess.run(["git", "add", "-A"], check=True)
print("   [OK] All changes staged")

# 4. Commit
print("\n4. Committing changes...")
commit_message = """fix: P0 Hotfix Sprint - Auth, Error Format, Metering Safety

## A. Auth Contract Unification ✅

- **Standardized on Authorization: Bearer**
  - Updated docs/quickstart.md: X-API-Key → Authorization: Bearer
  - Updated main.py function_calling_specs: auth type "http" with "bearer" scheme
  - Added BearerAuth security scheme to OpenAPI
  - API key format: sk_{environment}_{key_id}_{secret}

## B. Error Format Unification (RFC 9457) ✅

- **429 Retry-After header added**
  - http_exception_handler now adds Retry-After: 60 for all 429 responses
  - PlanViolationError handler already had Retry-After support
  - All errors return application/problem+json

## C. P0 Bug Fixes ✅

1. **MeteringEvent.metadata mutable default**
   - Fixed: `metadata: dict = {}` → `metadata: dict = Field(default_factory=dict)`

2. **MeteringService._is_billable safe defaults**
   - 2xx: default True (billable if config missing) - prevents revenue loss
   - 422: default True (billable if config missing) - prevents revenue loss
   - 4xx/5xx: default False (non-billable if config missing) - safe

3. **health.py SQLAlchemy 2.0 compatibility**
   - Fixed: `conn.execute("SELECT 1")` → `conn.execute(text("SELECT 1"))`
   - Added import: `from sqlalchemy import text`

4. **.gitignore hygiene**
   - Added: *.backup, *.bak

## D. Regression Tests ✅

- **test_p0_hotfix.py** (3 test classes, 7 tests)
  1. TestAuthContract: quickstart matches implementation
  2. Test429ResponseFormat: RFC 9457 + Retry-After validation
  3. TestMeteringBillableDefaults: Safe defaults prevent revenue loss

## Files Modified

- apps/api/dpp_api/main.py: Auth security scheme, 429 Retry-After
- apps/api/dpp_api/pricing/metering.py: Mutable default fix, billable defaults
- apps/api/dpp_api/routers/health.py: SQLAlchemy 2.0 text()
- docs/quickstart.md: Authorization: Bearer examples
- public/docs/quickstart.md: Synced
- .gitignore: *.backup, *.bak
- apps/api/tests/unit/test_p0_hotfix.py: New regression tests

## Breaking Changes

None - All changes are backward compatible or fix bugs.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
"""

result = subprocess.run(
    ["git", "commit", "-m", commit_message],
    capture_output=True,
    text=True
)

if result.returncode == 0:
    print("   [OK] Committed successfully")
    print(result.stdout)
else:
    print("   [INFO]", result.stdout)
    print("   [INFO]", result.stderr)

# 5. Push
print("\n5. Pushing to GitHub...")
result = subprocess.run(
    ["git", "push", "origin", "master"],
    capture_output=True,
    text=True
)

if result.returncode == 0:
    print("   [OK] Pushed successfully")
    print(result.stderr)  # Git uses stderr for progress
else:
    print("   [ERROR] Push failed")
    print(result.stdout)
    print(result.stderr)

print("\n=== Complete ===")
