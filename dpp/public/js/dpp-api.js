/**
 * dpp-api.js — Decisionproof API client
 * Phase 3: thin fetch wrapper for all API calls.
 *
 * Usage:
 *   import { api } from './dpp-api.js';
 *   const session = await api.post('/v1/billing/checkout-sessions', { plan_id: '...' });
 *
 * All requests automatically include the Bearer token from sessionStorage.
 * On 401, clears auth state and redirects to /login.html.
 */

const API_BASE = 'https://api.decisionproof.io.kr';

/**
 * Normalised API error — includes RFC 9457 fields when the server returns them.
 * @property {number}  status   HTTP status code
 * @property {string}  type     Problem type URI (if RFC 9457)
 * @property {string}  title    Short machine-readable title
 * @property {string}  detail   Human-readable detail message
 * @property {object}  raw      Full parsed JSON body
 */
export class ApiError extends Error {
  constructor(status, body) {
    const detail = body?.detail ?? body?.message ?? `HTTP ${status}`;
    super(detail);
    this.status = status;
    this.type   = body?.type   ?? null;
    this.title  = body?.title  ?? null;
    this.detail = detail;
    this.raw    = body ?? {};
    this.name   = 'ApiError';
  }
}

/**
 * Core fetch wrapper.
 * @param {string} path    — API path, e.g. '/v1/billing/checkout-sessions'
 * @param {RequestInit} options — fetch options (method, body, etc.)
 * @returns {Promise<object>} — parsed JSON response body
 * @throws {ApiError} on non-2xx responses
 */
async function request(path, options = {}) {
  const token = sessionStorage.getItem('dpp_access_token');

  const headers = {
    'Content-Type': 'application/json',
    'Accept':       'application/json',
    ...(options.headers ?? {}),
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  // Handle 401 — clear auth and redirect to login
  if (response.status === 401) {
    clearAuth();
    window.location.href = '/login.html?reason=session_expired';
    // Throw so callers can bail out (redirect is async)
    throw new ApiError(401, { detail: 'Session expired. Please log in again.' });
  }

  let body = null;
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('json') || contentType.includes('problem+json')) {
    try { body = await response.json(); } catch (_) { body = null; }
  }

  if (!response.ok) {
    throw new ApiError(response.status, body);
  }

  return body;
}

function clearAuth() {
  sessionStorage.removeItem('dpp_access_token');
  sessionStorage.removeItem('dpp_refresh_token');
  sessionStorage.removeItem('dpp_user_id');
  sessionStorage.removeItem('dpp_email');
}

/**
 * Convenience methods
 */
export const api = {
  get:    (path, opts = {}) => request(path, { ...opts, method: 'GET' }),
  post:   (path, body, opts = {}) => request(path, { ...opts, method: 'POST',  body: JSON.stringify(body) }),
  put:    (path, body, opts = {}) => request(path, { ...opts, method: 'PUT',   body: JSON.stringify(body) }),
  patch:  (path, body, opts = {}) => request(path, { ...opts, method: 'PATCH', body: JSON.stringify(body) }),
  delete: (path, opts = {})       => request(path, { ...opts, method: 'DELETE' }),
};

export { clearAuth };
