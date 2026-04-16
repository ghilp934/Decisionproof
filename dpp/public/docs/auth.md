# Authentication

## Bearer Token Authentication

Decisionproof uses Bearer token authentication. Include your API token in the `Authorization` header:

```http
Authorization: Bearer sk_abc123_xyz789def456...
```

### Token Format

- **Format**: `sk_{key_id}_{secret}`
- **Example**: `sk_abc123_xyz789def456ghi789...`
- **Length**: Variable (minimum 32 characters)

**Note**: Token format does NOT include environment prefixes. All tokens follow the same format pattern.

### Example Request

```bash
curl -X POST https://api.decisionproof.ai/v1/runs \
  -H "Authorization: Bearer sk_abc123_xyz789def456..." \
  -H "Idempotency-Key: unique-request-id-12345" \
  -H "Content-Type: application/json" \
  -d '{
    "pack_type": "decision",
    "inputs": {"question": "Should we proceed?"},
    "reservation": {"max_cost_usd": "0.0500"}
  }'
```

## Error Responses

### 401 Unauthorized

API token is missing or invalid.

```json
{
  "type": "https://docs.decisionproof.ai/errors/auth-missing",
  "title": "Unauthorized",
  "status": 401,
  "detail": "Missing or invalid Authorization header",
  "instance": "/v1/runs",
  "reason_code": "AUTH_MISSING",
  "trace_id": "abc123-def456-789"
}
```

**Billable**: No (authentication errors are non-billable)

**Client Action**:
1. Verify `Authorization` header is present
2. Verify token format: `Bearer sk_{key_id}_{secret}`
3. Check token is not expired
4. Contact support if issue persists

### 403 Forbidden

API token is valid but lacks permissions for the requested resource (e.g., tenant mismatch).

```json
{
  "type": "https://docs.decisionproof.ai/errors/forbidden",
  "title": "Forbidden",
  "status": 403,
  "detail": "Token does not have permission to access this resource",
  "instance": "/v1/runs/run_abc123",
  "reason_code": "TENANT_MISMATCH",
  "trace_id": "abc123-def456-789"
}
```

**Billable**: No (authorization errors are non-billable)

**Client Action**:
1. Verify you are using the correct token for this resource
2. Check that the resource (e.g., `run_id`) belongs to your tenant
3. Contact support if you believe this is an error

## Security Best Practices

- **Never commit tokens to version control**: Use environment variables or secrets management
- **Rotate tokens regularly**: Set up a rotation schedule (recommended: every 90 days)
- **Use different tokens for different environments**: Separate tokens for development, staging, and production
- **Monitor token usage**: Review access logs for suspicious activity
- **Revoke compromised tokens immediately**: Contact support for emergency token revocation

## Token Management

### Environment Variables (Recommended)

```bash
# .env file (DO NOT commit to git)
DECISIONPROOF_API_TOKEN=sk_abc123_xyz789def456...
```

```python
# Python example
import os
import requests

token = os.environ["DECISIONPROOF_API_TOKEN"]
headers = {"Authorization": f"Bearer {token}"}

response = requests.post(
    "https://api.decisionproof.ai/v1/runs",
    headers=headers,
    json={"pack_type": "decision", ...}
)
```

### Secrets Management (Production)

For production deployments, use a secrets manager:
- **AWS Secrets Manager**: `aws secretsmanager get-secret-value`
- **HashiCorp Vault**: `vault kv get`
- **Kubernetes Secrets**: Mount as environment variable
- **Azure Key Vault**: `az keyvault secret show`

## Troubleshooting

### "Invalid or missing API key"

**Symptom**: 401 error with `AUTH_MISSING` or `AUTH_INVALID`

**Solutions**:
1. Check header name: Must be `Authorization` with `Bearer` scheme
2. Check header value: Must start with `Bearer ` (note the space)
3. Check token format: `sk_{key_id}_{secret}` (no environment prefix)
4. Verify token is not expired (contact support to check status)

### "Token does not have permission"

**Symptom**: 403 error with `TENANT_MISMATCH`

**Solutions**:
1. Verify the resource (`run_id`, `tenant_id`) belongs to your account
2. Check you are not using a token from a different tenant
3. Ensure the token has not been revoked

## Related Documentation

- [Quickstart Guide](quickstart.md) - Complete API usage examples
- [Error Responses](problem-types.md) - All error codes and handling
- [Rate Limiting](rate-limits.md) - Request limits and headers
