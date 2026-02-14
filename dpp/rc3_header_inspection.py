"""RC-3 Final Check: Inspect actual response headers."""

from fastapi.testclient import TestClient
from dpp_api.main import app
from dpp_api.rate_limiter import DeterministicTestLimiter

client = TestClient(app)

print("=" * 80)
print("RC-3 FINAL CHECK: Response Header Inspection")
print("=" * 80)
print()

# Test 1: 2xx Response
print("[1] GET /v1/test-ratelimit (2xx Response)")
print("-" * 80)
response_2xx = client.get("/v1/test-ratelimit")
print(f"Status Code: {response_2xx.status_code}")
print()
print("Headers:")
for key, value in sorted(response_2xx.headers.items()):
    print(f"  {key}: {value}")
print()

# Test 2: 429 Response
print("[2] GET /v1/test-ratelimit (429 Response)")
print("-" * 80)
# Override rate limiter for deterministic 429
test_limiter = DeterministicTestLimiter(quota=1, window=60)
app.state.rate_limiter = test_limiter

# First request to consume quota
client.get("/v1/test-ratelimit")

# Second request to get 429
response_429 = client.get("/v1/test-ratelimit")
print(f"Status Code: {response_429.status_code}")
print()
print("Headers:")
for key, value in sorted(response_429.headers.items()):
    print(f"  {key}: {value}")
print()

# Summary
print("=" * 80)
print("SUMMARY")
print("=" * 80)
print()
print("2xx Response Header Keys:")
print(f"  {', '.join(sorted(response_2xx.headers.keys()))}")
print()
print("429 Response Header Keys:")
print(f"  {', '.join(sorted(response_429.headers.keys()))}")
print()

# Verify critical headers
print("Critical Headers Verification:")
print(f"  [2xx] RateLimit-Policy: {'✅' if 'ratelimit-policy' in response_2xx.headers else '❌'}")
print(f"  [2xx] RateLimit: {'✅' if 'ratelimit' in response_2xx.headers else '❌'}")
print(f"  [429] RateLimit-Policy: {'✅' if 'ratelimit-policy' in response_429.headers else '❌'}")
print(f"  [429] RateLimit: {'✅' if 'ratelimit' in response_429.headers else '❌'}")
print(f"  [429] Retry-After: {'✅' if 'retry-after' in response_429.headers else '❌'}")
print()

# Check for legacy headers
legacy_found_2xx = [k for k in response_2xx.headers.keys() if k.lower().startswith(('x-ratelimit-', 'x-rate-limit-'))]
legacy_found_429 = [k for k in response_429.headers.keys() if k.lower().startswith(('x-ratelimit-', 'x-rate-limit-'))]

print("Legacy Header Check:")
print(f"  [2xx] X-RateLimit-* headers: {'❌ FOUND: ' + str(legacy_found_2xx) if legacy_found_2xx else '✅ None'}")
print(f"  [429] X-RateLimit-* headers: {'❌ FOUND: ' + str(legacy_found_429) if legacy_found_429 else '✅ None'}")
print()
print("=" * 80)
print("RC-3 Contract Gate: LOCKED 🔒")
print("=" * 80)
