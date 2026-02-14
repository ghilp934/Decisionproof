# Delegated Authentication

## Overview

Decisionwise supports delegated authentication for scenarios where agents or applications act on behalf of users without direct credential sharing.

## OAuth 2.0 (RFC 6749)

OAuth 2.0 is the recommended baseline approach for delegated authentication.

### Authorization Code Flow

**Use case**: Web applications with user interaction

1. User clicks "Connect Decisionwise"
2. Redirect to Decisionwise authorization endpoint
3. User approves access
4. Redirect back with authorization code
5. Exchange code for access token
6. Use access token in API requests

**Example Authorization URL:**
```
https://auth.decisionwise.ai/oauth/authorize?
  client_id=your_client_id&
  redirect_uri=https://yourapp.com/callback&
  response_type=code&
  scope=runs:read runs:write
```

### Client Credentials Flow

**Use case**: Server-to-server integration (no user context)

1. Exchange client credentials for access token
2. Use access token in API requests

**Example Token Request:**
```bash
curl -X POST https://auth.decisionwise.ai/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=your_client_id" \
  -d "client_secret=your_client_secret" \
  -d "scope=runs:read runs:write"
```

## Device Authorization Grant (RFC 8628)

**Use case**: Headless agents, CLI tools, IoT devices without browser access

### Flow

1. **Device requests user code:**
```bash
POST https://auth.decisionwise.ai/oauth/device/code
{
  "client_id": "your_client_id",
  "scope": "runs:read runs:write"
}
```

2. **Response contains user code and verification URL:**
```json
{
  "device_code": "GmRhmhcxhwEzkoEqiMEg_DnyEysNkuNhszIySk9eS",
  "user_code": "WDJB-MJHT",
  "verification_uri": "https://decisionwise.ai/activate",
  "verification_uri_complete": "https://decisionwise.ai/activate?user_code=WDJB-MJHT",
  "expires_in": 900,
  "interval": 5
}
```

3. **User visits verification URL and enters code**

4. **Device polls for token:**
```bash
POST https://auth.decisionwise.ai/oauth/token
{
  "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
  "device_code": "GmRhmhcxhwEzkoEqiMEg_DnyEysNkuNhszIySk9eS",
  "client_id": "your_client_id"
}
```

5. **Token response (after user approval):**
```json
{
  "access_token": "eyJhbGc...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "def502...",
  "scope": "runs:read runs:write"
}
```

### Agent Implementation Example

```python
import time
import requests

def device_auth_flow(client_id):
    # 1. Request device code
    resp = requests.post(
        "https://auth.decisionwise.ai/oauth/device/code",
        json={"client_id": client_id, "scope": "runs:read runs:write"}
    )
    device_data = resp.json()

    # 2. Show user code to user
    print(f"Visit: {device_data['verification_uri']}")
    print(f"Enter code: {device_data['user_code']}")

    # 3. Poll for token
    device_code = device_data["device_code"]
    interval = device_data["interval"]
    expires_in = device_data["expires_in"]

    start_time = time.time()
    while time.time() - start_time < expires_in:
        time.sleep(interval)

        token_resp = requests.post(
            "https://auth.decisionwise.ai/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": client_id
            }
        )

        if token_resp.status_code == 200:
            return token_resp.json()["access_token"]
        elif token_resp.status_code == 400:
            error = token_resp.json().get("error")
            if error == "authorization_pending":
                continue  # Keep polling
            elif error == "slow_down":
                interval += 5  # Increase interval
            else:
                raise Exception(f"Auth failed: {error}")

    raise Exception("Device code expired")
```

## Supported Flows (Current vs Roadmap)

| Flow | Status | Use Case |
|------|--------|----------|
| **API Key** | ✅ Available Now | Direct integration, testing |
| **OAuth 2.0 Authorization Code** | 🚧 Q2 2026 | Web applications |
| **OAuth 2.0 Client Credentials** | 🚧 Q2 2026 | Server-to-server |
| **Device Authorization Grant (RFC 8628)** | 🚧 Q3 2026 | Headless agents, CLI tools |
| **PKCE Extension (RFC 7636)** | 🚧 Q2 2026 | Mobile apps, SPAs |

## Using Access Tokens

Once you have an access token, use it in API requests:

```bash
curl -X POST https://api.decisionwise.ai/v1/runs \
  -H "Authorization: Bearer eyJhbGc..." \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "ws_abc123",
    "run_id": "run_unique_001",
    "plan_id": "plan_xyz789",
    "input": {"question": "What is 2+2?"}
  }'
```

**Note**: API Key authentication (`X-API-Key` header) remains supported for backward compatibility.

## Token Management

### Refresh Tokens

```bash
POST https://auth.decisionwise.ai/oauth/token
{
  "grant_type": "refresh_token",
  "refresh_token": "def502...",
  "client_id": "your_client_id"
}
```

### Token Revocation

```bash
POST https://auth.decisionwise.ai/oauth/revoke
{
  "token": "eyJhbGc...",
  "client_id": "your_client_id"
}
```

## Security Best Practices

1. **Never share API keys or client secrets** in code or version control
2. **Use short-lived access tokens** (default: 1 hour)
3. **Store refresh tokens securely** (encrypted, key management system)
4. **Rotate credentials regularly** (30-90 days recommended)
5. **Use PKCE** for mobile/SPA applications (prevents authorization code interception)
6. **Scope to minimum permissions** (principle of least privilege)

## Further Reading

- [RFC 6749: OAuth 2.0 Framework](https://www.rfc-editor.org/rfc/rfc6749.html)
- [RFC 8628: Device Authorization Grant](https://www.rfc-editor.org/rfc/rfc8628.html)
- [RFC 7636: PKCE](https://www.rfc-editor.org/rfc/rfc7636.html)
- [Decisionwise API Authentication](/docs/auth.md)
