"""
RC-2 Smoke Test Script
Tests 429 + Retry-After in real HTTP stack using TestClient
"""

import json
import sys
from pathlib import Path

# Add apps/api to path
sys.path.insert(0, str(Path(__file__).parent / "apps" / "api"))

from fastapi.testclient import TestClient
from dpp_api.main import app

def test_429_smoke():
    """Test /test/rate-limit-429 endpoint"""
    print("[*] RC-2 Smoke Test - Starting...")
    print("")

    client = TestClient(app)

    print("[TEST] Testing GET /test/rate-limit-429...")
    response = client.get("/test/rate-limit-429")

    print(f"[RESP] Response Status: {response.status_code}")
    print("")

    # Check Status Code
    if response.status_code == 429:
        print("[OK] Status Code: 429 OK")
    else:
        print(f"[FAIL] Status Code: FAIL (expected 429, got {response.status_code})")
        return False

    # Check Retry-After Header
    retry_after = response.headers.get("retry-after", response.headers.get("Retry-After"))
    if retry_after:
        print(f"[OK] Retry-After Header: {retry_after} seconds")
    else:
        print("[FAIL] Retry-After Header: MISSING")
        print(f"   Available headers: {list(response.headers.keys())}")
        return False

    # Check Content-Type
    content_type = response.headers.get("content-type")
    if content_type and "application/problem+json" in content_type:
        print(f"[OK] Content-Type: {content_type}")
    else:
        print(f"[FAIL] Content-Type: FAIL (expected application/problem+json, got {content_type})")
        return False

    # Check Problem Details JSON
    try:
        data = response.json()
        print("")
        print("[JSON] Problem Details JSON:")
        print(json.dumps(data, indent=2))
        print("")

        # Check required fields
        required_fields = ["type", "title", "status", "detail", "instance"]
        missing_fields = [field for field in required_fields if field not in data]

        if not missing_fields:
            print("[OK] Required Fields: All present")
        else:
            print(f"[FAIL] Required Fields: Missing {missing_fields}")
            return False

        # Check instance format
        instance = data.get("instance", "")
        if instance.startswith("urn:decisionproof:trace:"):
            print(f"[OK] Instance Format: Opaque URN ({instance[:50]}...)")
        else:
            print(f"[FAIL] Instance Format: FAIL (expected urn:decisionproof:trace:..., got {instance})")
            return False

        # Check no path leak
        if "/" not in instance:
            print("[OK] Instance Security: No path leak")
        else:
            print(f"[FAIL] Instance Security: Path leak detected ({instance})")
            return False

    except Exception as e:
        print(f"[FAIL] JSON Parsing: FAIL ({e})")
        return False

    print("")
    print("[PASS] RC-2 Smoke Test: PASSED")
    print("")
    print("[SUMMARY] Summary:")
    print("   - Status Code: 429 [OK]")
    print(f"   - Retry-After: {retry_after}s [OK]")
    print("   - Content-Type: application/problem+json [OK]")
    print("   - Problem Details: Valid [OK]")
    print("   - Instance: Opaque URN [OK]")
    print("   - Security: No path/PK leaks [OK]")

    return True

if __name__ == "__main__":
    success = test_429_smoke()
    sys.exit(0 if success else 1)
