"""
test_docs_spec_lock.py - Documentation/Spec Drift Prevention

Purpose: Prevent forbidden tokens from re-entering customer-facing docs and specs.
Scope: public/docs, docs/pilot, public/llms.txt, function-calling-specs.json, OpenAPI

Run: pytest apps/api/tests/unit/test_docs_spec_lock.py -v
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Base directory (dpp/)
BASE_DIR = Path(__file__).parent.parent.parent.parent.parent

# Scan paths for customer-facing content
SCAN_PATHS = [
    BASE_DIR / "public" / "docs",
    BASE_DIR / "docs" / "pilot",
    BASE_DIR / "public" / "llms.txt",
    BASE_DIR / "public" / "llms-full.txt",
]

# Forbidden tokens (must NOT appear in customer-facing docs)
FORBIDDEN_TOKENS = [
    "X-API-Key",
    "dw_live_",
    "dw_test_",
    "sk_live_",
    "sk_test_",
    "workspace_id",
    "plan_id",
    "Decision Credits",
]

# Allowlist: Files/contexts where forbidden tokens are OK (internal docs, tests, etc.)
# Format: (token, file_pattern, reason)
ALLOWLIST = [
    # Internal best practices docs can reference legacy patterns
    ("Decision Credits", "best_practices", "Internal reference only"),
    # Test files can use any tokens
    ("workspace_id", "test_", "Test fixture"),
    ("plan_id", "test_", "Test fixture"),
]


def is_allowed(token: str, file_path: Path) -> bool:
    """Check if token usage is explicitly allowed in this file."""
    for allowed_token, pattern, _ in ALLOWLIST:
        if token == allowed_token and pattern in str(file_path):
            return True
    return False


def scan_file(file_path: Path, token: str) -> list[tuple[int, str]]:
    """Scan a file for a token, return (line_number, line_content) list."""
    results = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line_num, line in enumerate(f, 1):
                if token in line:
                    results.append((line_num, line.strip()))
    except Exception:
        pass  # Skip files that can't be read
    return results


def scan_path(search_path: Path, token: str) -> dict[str, list[tuple[int, str]]]:
    """Scan a path (file or directory) for a token."""
    findings = {}

    if not search_path.exists():
        return findings

    if search_path.is_file():
        if not is_allowed(token, search_path):
            results = scan_file(search_path, token)
            if results:
                findings[str(search_path.relative_to(BASE_DIR))] = results
    else:
        # Directory: scan all .md, .txt files
        for ext in ["*.md", "*.txt"]:
            for file_path in search_path.rglob(ext):
                if file_path.is_file() and not is_allowed(token, file_path):
                    results = scan_file(file_path, token)
                    if results:
                        findings[str(file_path.relative_to(BASE_DIR))] = results

    return findings


@pytest.mark.parametrize("token", FORBIDDEN_TOKENS)
def test_forbidden_token_not_in_customer_docs(token):
    """
    Test A: Forbidden Token Scan

    Ensures customer-facing documentation does NOT contain legacy/forbidden tokens.
    Failure indicates drift from SPEC_LOCK_PUBLIC_CONTRACT.md.
    """
    all_findings = {}

    for path in SCAN_PATHS:
        findings = scan_path(path, token)
        if findings:
            all_findings.update(findings)

    if all_findings:
        error_msg = f"\nForbidden token '{token}' found in customer-facing docs:\n"
        for file_path, occurrences in all_findings.items():
            error_msg += f"\n  {file_path}:\n"
            for line_num, line_content in occurrences[:3]:  # Show first 3
                error_msg += f"    Line {line_num}: {line_content[:80]}\n"
            if len(occurrences) > 3:
                error_msg += f"    ... and {len(occurrences) - 3} more\n"

        error_msg += f"\nAction: Remove '{token}' from listed files or add to ALLOWLIST with justification.\n"
        pytest.fail(error_msg)


def test_function_calling_specs_schema_no_forbidden_fields(client: TestClient):
    """
    Test B: Function Calling Specs Schema Drift

    Ensures /docs/function-calling-specs.json does NOT contain workspace_id/plan_id
    and matches RunCreateRequest model structure.
    """
    response = client.get("/docs/function-calling-specs.json")
    assert response.status_code == 200

    spec = response.json()

    # Find create_decision_run tool
    tools = spec.get("tools", [])
    create_run_tool = None
    for tool in tools:
        if tool.get("name") == "create_decision_run":
            create_run_tool = tool
            break

    assert create_run_tool is not None, "create_decision_run tool not found"

    parameters = create_run_tool.get("parameters", {})
    properties = parameters.get("properties", {})
    required = parameters.get("required", [])

    # Forbidden fields must NOT be present
    assert "workspace_id" not in properties, "workspace_id must NOT be in function-calling-specs"
    assert "plan_id" not in properties, "plan_id must NOT be in function-calling-specs"
    assert "workspace_id" not in required, "workspace_id must NOT be required"
    assert "plan_id" not in required, "plan_id must NOT be required"

    # Required fields from RunCreateRequest must be present
    assert "pack_type" in properties, "pack_type must be in parameters"
    assert "inputs" in properties, "inputs must be in parameters"
    assert "reservation" in properties, "reservation must be in parameters"

    assert "pack_type" in required, "pack_type must be required"
    assert "inputs" in required, "inputs must be required"
    assert "reservation" in required, "reservation must be required"

    # Reservation sub-schema check
    reservation_props = properties.get("reservation", {}).get("properties", {})
    assert "max_cost_usd" in reservation_props, "reservation.max_cost_usd must be present"


def test_function_calling_specs_auth_no_environment_prefix(client: TestClient):
    """
    Test B-2: Auth format in function-calling-specs

    Ensures auth section does NOT mention sk_live_/sk_test_ environment prefixes.
    """
    response = client.get("/docs/function-calling-specs.json")
    assert response.status_code == 200

    spec = response.json()
    auth = spec.get("auth", {})

    # Check bearer_format is OpenAPI-compliant (opaque-token)
    bearer_format = auth.get("bearer_format", "")
    assert bearer_format == "opaque-token", \
        f"bearer_format should be 'opaque-token', got: {bearer_format}"

    # Check description contains actual token format (sk_{key_id}_{secret})
    description = auth.get("description", "")
    assert "sk_{key_id}_{secret}" in description, \
        "description must specify sk_{key_id}_{secret} format"

    # Ensure NO environment prefix in description
    assert "sk_live_" not in description, "sk_live_ must NOT be in description"
    assert "sk_test_" not in description, "sk_test_ must NOT be in description"
    assert "sk_{environment}" not in description, "sk_{environment} must NOT be in description"


def test_openapi_auth_scheme_no_x_api_key(client: TestClient):
    """
    Test C: OpenAPI Auth Doc Drift

    Ensures /.well-known/openapi.json does NOT mention X-API-Key
    and uses correct Bearer token format.
    """
    response = client.get("/.well-known/openapi.json")
    assert response.status_code == 200

    openapi = response.json()
    components = openapi.get("components", {})
    security_schemes = components.get("securitySchemes", {})

    assert "BearerAuth" in security_schemes, "BearerAuth scheme must be present"

    bearer_auth = security_schemes["BearerAuth"]
    assert bearer_auth.get("type") == "http", "BearerAuth type must be 'http'"
    assert bearer_auth.get("scheme") == "bearer", "BearerAuth scheme must be 'bearer'"

    description = bearer_auth.get("description", "")

    # Forbidden mentions
    assert "X-API-Key" not in description, "X-API-Key must NOT be in BearerAuth description"
    assert "sk_live_" not in description, "sk_live_ must NOT be in BearerAuth description"
    assert "sk_test_" not in description, "sk_test_ must NOT be in BearerAuth description"

    # Check bearerFormat
    bearer_format = bearer_auth.get("bearerFormat", "")
    assert "sk_{environment}" not in bearer_format, "bearerFormat must NOT contain {environment}"


def test_openapi_no_x_api_key_in_paths(client: TestClient):
    """
    Test C-2: OpenAPI Paths Security

    Ensures no path mentions X-API-Key in descriptions/examples.
    """
    response = client.get("/.well-known/openapi.json")
    assert response.status_code == 200

    openapi = response.json()
    paths = openapi.get("paths", {})

    # Check all paths for X-API-Key mentions
    for path, methods in paths.items():
        for method, spec in methods.items():
            if not isinstance(spec, dict):
                continue

            description = spec.get("description", "")
            assert "X-API-Key" not in description, \
                f"X-API-Key found in {method.upper()} {path} description"

            # Check requestBody examples
            request_body = spec.get("requestBody", {})
            if request_body:
                content = request_body.get("content", {})
                for media_type, media_spec in content.items():
                    example = json.dumps(media_spec.get("example", {}))
                    assert "X-API-Key" not in example, \
                        f"X-API-Key found in {method.upper()} {path} request example"


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def client():
    """FastAPI test client."""
    from dpp_api.main import app
    return TestClient(app)
