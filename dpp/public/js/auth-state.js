/**
 * auth-state.js — JWT sessionStorage management
 * Phase 3: read/write/clear auth tokens + user metadata.
 *
 * Storage keys:
 *   dpp_access_token   — Supabase JWT access token
 *   dpp_refresh_token  — Supabase refresh token
 *   dpp_user_id        — Supabase user UUID
 *   dpp_email          — User email (for display)
 *
 * All data lives in sessionStorage only (cleared on tab close).
 * Never store tokens in localStorage — XSS persistence risk.
 */

const KEYS = {
  ACCESS_TOKEN:  'dpp_access_token',
  REFRESH_TOKEN: 'dpp_refresh_token',
  USER_ID:       'dpp_user_id',
  EMAIL:         'dpp_email',
};

/**
 * Persist login response from POST /v1/auth/login.
 * @param {{ access_token: string, refresh_token: string, user_id: string, email: string }} session
 */
export function saveSession(session) {
  sessionStorage.setItem(KEYS.ACCESS_TOKEN,  session.access_token);
  sessionStorage.setItem(KEYS.REFRESH_TOKEN, session.refresh_token);
  sessionStorage.setItem(KEYS.USER_ID,       session.user_id);
  sessionStorage.setItem(KEYS.EMAIL,         session.email ?? '');
}

/**
 * @returns {{ accessToken: string|null, refreshToken: string|null, userId: string|null, email: string|null }}
 */
export function getSession() {
  return {
    accessToken:  sessionStorage.getItem(KEYS.ACCESS_TOKEN),
    refreshToken: sessionStorage.getItem(KEYS.REFRESH_TOKEN),
    userId:       sessionStorage.getItem(KEYS.USER_ID),
    email:        sessionStorage.getItem(KEYS.EMAIL),
  };
}

/**
 * @returns {boolean} True if an access token exists in sessionStorage.
 */
export function isLoggedIn() {
  return Boolean(sessionStorage.getItem(KEYS.ACCESS_TOKEN));
}

/**
 * Clear all auth state (logout).
 */
export function clearSession() {
  Object.values(KEYS).forEach(k => sessionStorage.removeItem(k));
}

/**
 * Redirect to /login.html if the user is not authenticated.
 * Call at the top of any page that requires auth.
 * @param {string} [returnPath] — path to redirect back to after login (defaults to current page)
 */
export function requireAuth(returnPath) {
  if (!isLoggedIn()) {
    const next = encodeURIComponent(returnPath ?? window.location.pathname + window.location.search);
    window.location.replace(`/login.html?next=${next}`);
  }
}

/**
 * Redirect to /onboarding.html if the user IS authenticated.
 * Call on login/signup pages to avoid showing them to logged-in users.
 */
export function redirectIfLoggedIn() {
  if (isLoggedIn()) {
    window.location.replace('/onboarding.html');
  }
}
