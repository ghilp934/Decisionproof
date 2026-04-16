# Client Integration Guide: Session JWT for Token Management

**Version**: P0-3.1
**Date**: 2026-02-18
**Audience**: Client Application Developers (Web, Mobile, CLI)

---

## 📋 Overview

All **token management endpoints** (`/v1/tokens/*`) now require **session authentication** using Supabase JWT (access token).

**Key Changes**:
- ✅ **Use**: Session JWT (from Supabase auth) for `/v1/tokens/*`
- ❌ **Don't Use**: API tokens for token management (will return 403)
- 🔐 **Separation**: Session auth (token management) vs. API token auth (API usage)

---

## 🔑 Authentication Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. User Login (Supabase Auth)                              │
│    POST /v1/auth/login                                      │
│    → Returns: { access_token, refresh_token }              │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Token Management (Session Auth Required)                │
│    Authorization: Bearer <access_token>                     │
│    - POST /v1/tokens (create token)                         │
│    - GET /v1/tokens (list tokens)                           │
│    - POST /v1/tokens/{id}/revoke                            │
│    - POST /v1/tokens/{id}/rotate                            │
│    - POST /v1/tokens/revoke-all                             │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. API Usage (API Token Auth)                              │
│    Authorization: Bearer <api_token>                        │
│    - POST /v1/runs (execute decision pack)                  │
│    - GET /v1/runs/{id} (poll result)                        │
│    - Other API endpoints                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 🌐 Web Application (Browser)

### Setup: Supabase Client

```typescript
// lib/supabase.ts
import { createBrowserClient } from '@supabase/ssr'

export const supabase = createBrowserClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
)
```

### Login

```typescript
// app/login/page.tsx
import { supabase } from '@/lib/supabase'

async function handleLogin(email: string, password: string) {
  const { data, error } = await supabase.auth.signInWithPassword({
    email,
    password,
  })

  if (error) {
    console.error('Login failed:', error.message)
    return
  }

  // Session JWT stored in cookies automatically
  console.log('Logged in:', data.user.email)
  console.log('Access token:', data.session.access_token)
}
```

### Token Management Requests

```typescript
// lib/tokenManagement.ts
import { supabase } from '@/lib/supabase'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL

async function getSessionToken(): Promise<string | null> {
  const { data: { session } } = await supabase.auth.getSession()

  if (!session) {
    console.error('No active session')
    return null
  }

  return session.access_token  // Supabase JWT
}

export async function createAPIToken(name: string, expiresInDays?: number) {
  const sessionToken = await getSessionToken()
  if (!sessionToken) throw new Error('Not authenticated')

  const response = await fetch(`${API_BASE_URL}/v1/tokens`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${sessionToken}`,  // Session JWT here
    },
    body: JSON.stringify({
      name,
      expires_in_days: expiresInDays,
      scopes: [],
    }),
  })

  if (!response.ok) {
    if (response.status === 401) {
      // Session expired, prompt re-login
      console.error('Session expired. Please log in again.')
      await supabase.auth.signOut()
      window.location.href = '/login'
      return
    }

    throw new Error(`Failed to create token: ${response.statusText}`)
  }

  const data = await response.json()

  // ⚠️ IMPORTANT: Raw token returned ONCE - save it now!
  console.log('✅ Token created:', data.token)
  return data
}

export async function listAPITokens() {
  const sessionToken = await getSessionToken()
  if (!sessionToken) throw new Error('Not authenticated')

  const response = await fetch(`${API_BASE_URL}/v1/tokens`, {
    method: 'GET',
    headers: {
      'Authorization': `Bearer ${sessionToken}`,  // Session JWT
    },
  })

  if (!response.ok) {
    throw new Error(`Failed to list tokens: ${response.statusText}`)
  }

  return response.json()
}

export async function revokeAPIToken(tokenId: string) {
  const sessionToken = await getSessionToken()
  if (!sessionToken) throw new Error('Not authenticated')

  const response = await fetch(`${API_BASE_URL}/v1/tokens/${tokenId}/revoke`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${sessionToken}`,  // Session JWT
    },
  })

  if (!response.ok) {
    throw new Error(`Failed to revoke token: ${response.statusText}`)
  }

  return response.json()
}
```

---

## 📱 Mobile Application (React Native)

### Setup

```typescript
// lib/supabase.ts
import 'react-native-url-polyfill/auto'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { createClient } from '@supabase/supabase-js'

export const supabase = createClient(
  process.env.EXPO_PUBLIC_SUPABASE_URL!,
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY!,
  {
    auth: {
      storage: AsyncStorage,
      autoRefreshToken: true,
      persistSession: true,
      detectSessionInUrl: false,
    },
  }
)
```

### Token Management (Same as Web)

```typescript
// Use same pattern as web example above
import { supabase } from './lib/supabase'

async function createToken() {
  const { data: { session } } = await supabase.auth.getSession()

  if (!session) {
    Alert.alert('Error', 'Please log in first')
    return
  }

  const response = await fetch('https://api.decisionproof.ai/v1/tokens', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${session.access_token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ name: 'Mobile App Token' }),
  })

  const data = await response.json()

  // Save API token securely (e.g., SecureStore)
  await SecureStore.setItemAsync('api_token', data.token)
}
```

---

## 🖥️ CLI / Server-Side

### Node.js Example

```javascript
// cli/tokenManager.js
const { createClient } = require('@supabase/supabase-js')
const fetch = require('node-fetch')

const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_ANON_KEY
)

async function login(email, password) {
  const { data, error } = await supabase.auth.signInWithPassword({
    email,
    password,
  })

  if (error) throw error

  // Store session for subsequent requests
  return data.session
}

async function createToken(session, name) {
  const response = await fetch('https://api.decisionproof.ai/v1/tokens', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${session.access_token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ name }),
  })

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`)
  }

  return response.json()
}

// Usage
;(async () => {
  const session = await login('user@example.com', 'password')
  const token = await createToken(session, 'CLI Token')

  console.log('API Token (save this!):', token.token)
})()
```

### Python Example

```python
# cli/token_manager.py
import os
from supabase import create_client, Client
import requests

supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_ANON_KEY")
)

def login(email: str, password: str):
    response = supabase.auth.sign_in_with_password({
        "email": email,
        "password": password
    })
    return response.session

def create_token(session, name: str):
    response = requests.post(
        "https://api.decisionproof.ai/v1/tokens",
        headers={
            "Authorization": f"Bearer {session.access_token}",
            "Content-Type": "application/json",
        },
        json={"name": name}
    )
    response.raise_for_status()
    return response.json()

# Usage
if __name__ == "__main__":
    session = login("user@example.com", "password")
    token = create_token(session, "Python CLI Token")

    print(f"API Token (save this!): {token['token']}")
```

---

## 🔒 Security Best Practices

### 1. Never Log Tokens

```typescript
// ❌ BAD
console.log('Session token:', sessionToken)
console.log('API token:', apiToken)

// ✅ GOOD
console.log('Session token:', sessionToken ? '[REDACTED]' : 'null')
console.log('API token created:', apiToken ? 'yes' : 'no')
```

### 2. Handle Session Expiration

```typescript
async function makeAuthenticatedRequest(url: string, options: RequestInit) {
  const { data: { session } } = await supabase.auth.getSession()

  if (!session) {
    // Session expired or not logged in
    window.location.href = '/login'
    throw new Error('Session expired')
  }

  // Check if token is about to expire (< 5 minutes)
  const expiresAt = new Date(session.expires_at! * 1000)
  const now = new Date()
  const fiveMinutes = 5 * 60 * 1000

  if (expiresAt.getTime() - now.getTime() < fiveMinutes) {
    // Refresh session proactively
    const { data, error } = await supabase.auth.refreshSession()
    if (error) throw error
    session = data.session!
  }

  return fetch(url, {
    ...options,
    headers: {
      ...options.headers,
      'Authorization': `Bearer ${session.access_token}`,
    },
  })
}
```

### 3. Secure Storage

**Browser**:
- ✅ Supabase handles session storage in httpOnly cookies (recommended)
- ❌ Don't store JWT in localStorage (XSS risk)

**Mobile**:
- ✅ Use SecureStore / Keychain for API tokens
- ❌ Don't store in AsyncStorage (unencrypted)

**CLI**:
- ✅ Use OS keychain (e.g., macOS Keychain, Windows Credential Manager)
- ⚠️ If storing in file, encrypt it and set 0600 permissions

---

## ⚠️ Common Errors

### Error 401: Unauthorized

**Cause**: Missing or invalid session JWT

**Fix**:
```typescript
// Check if user is logged in
const { data: { session } } = await supabase.auth.getSession()
if (!session) {
  // Redirect to login
  window.location.href = '/login'
}
```

### Error 403: Forbidden

**Cause**: Using API token instead of session JWT for token management

**Fix**:
```typescript
// ❌ WRONG - Using API token
headers: {
  'Authorization': 'Bearer dp_live_abc123...'  // API token (won't work!)
}

// ✅ CORRECT - Using session JWT
const { data: { session } } = await supabase.auth.getSession()
headers: {
  'Authorization': `Bearer ${session.access_token}`  // Session JWT
}
```

### Error 403: No Active Tenant

**Cause**: User has no tenant mapping (data migration issue)

**Fix**: Contact support - this should not happen after P0-3.1 migration

---

## 📚 API Reference

### POST /v1/auth/login

**Request**:
```json
{
  "email": "user@example.com",
  "password": "password"
}
```

**Response**:
```json
{
  "user_id": "a1b2c3d4-...",
  "email": "user@example.com",
  "access_token": "eyJhbGci...",  // ← Use this for token management
  "refresh_token": "v1.MR5S..."
}
```

### POST /v1/tokens

**Headers**:
```
Authorization: Bearer <session_access_token>
Content-Type: application/json
```

**Request**:
```json
{
  "name": "My API Token",
  "expires_in_days": 90,
  "scopes": []
}
```

**Response** (⚠️ Raw token shown ONCE):
```json
{
  "token": "dp_live_abc123...",  // ← Save this! Never shown again
  "token_id": "tok_xyz...",
  "prefix": "dp_live",
  "last4": "xyz9",
  "name": "My API Token",
  "status": "active",
  "created_at": "2026-02-18T12:00:00Z",
  "expires_at": "2026-05-19T12:00:00Z"
}
```

### GET /v1/tokens

**Response** (no raw tokens):
```json
{
  "tokens": [
    {
      "token_id": "tok_xyz...",
      "name": "My API Token",
      "prefix": "dp_live",
      "last4": "xyz9",  // ← Last 4 chars only
      "status": "active",
      "created_at": "2026-02-18T12:00:00Z",
      "expires_at": "2026-05-19T12:00:00Z",
      "last_used_at": "2026-02-18T13:00:00Z"
    }
  ]
}
```

---

## 🧪 Testing Checklist

- [ ] Login with email/password returns access_token
- [ ] Create token with session JWT succeeds (returns raw token)
- [ ] Create token with API token fails (403)
- [ ] List tokens with session JWT succeeds
- [ ] Revoke token with session JWT succeeds
- [ ] Rotate token with session JWT succeeds (returns new raw token)
- [ ] Session expiration triggers re-login
- [ ] No tokens logged in console/logs

---

## 📞 Support

If you encounter issues:
1. Check error response (RFC 9457 Problem Detail format)
2. Verify session JWT is not expired (check `exp` claim)
3. Contact support: ghilplip934@gmail.com
