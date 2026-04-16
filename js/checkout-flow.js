/**
 * checkout-flow.js — PayPal JS SDK integration + checkout state machine
 * Phase 3
 *
 * State machine mirrors backend CheckoutSession.status:
 *   IDLE → CHECKOUT_SESSION_CREATED → PAYPAL_ORDER_CREATED
 *       → CAPTURE_SUBMITTED → (polling) → PAID_VERIFIED | FAILED | CANCELED
 *
 * DEC-V1-07/08: entitlement activation is webhook-only.
 * This file NEVER activates or assumes entitlement — it only polls onboarding status.
 *
 * DEC-V1-14/15: PayPal-Request-Id is owned by the backend. The frontend
 * never generates or passes a PayPal-Request-Id — that's handled server-side.
 *
 * B-01: PAYPAL_CLIENT_ID must be set at page render time via window.__DPP_CONFIG__.
 */

import { api, ApiError } from './dpp-api.js';

const PLAN_ID = 'beta_private_starter_v1';

/* ─── State ─────────────────────────────────────────────────────────────── */
let _state = 'IDLE';
let _checkoutSessionId = null;
let _pollingTimer = null;
const POLLING_INTERVAL_MS  = 3000;
const POLLING_TIMEOUT_MS   = 300_000; // 5 minutes
let   _pollingStartedAt    = null;

/* ─── DOM refs — populated in init() ─────────────────────────────────────── */
let elPaypalContainer, elStatus, elError, elPendingPanel, elDonePanel;

/* ─── Event bus (simple) ─────────────────────────────────────────────────── */
const _listeners = {};
function emit(event, data) {
  (_listeners[event] ?? []).forEach(fn => fn(data));
}
export function on(event, fn) {
  _listeners[event] = _listeners[event] ?? [];
  _listeners[event].push(fn);
}

/* ─── Status helpers ─────────────────────────────────────────────────────── */
function setStatus(msg, type = 'info') {
  if (elStatus) {
    elStatus.textContent = msg;
    elStatus.className = `alert alert--${type}`;
    elStatus.classList.toggle('hidden', !msg);
  }
}

function setError(msg) {
  if (elError) {
    elError.textContent = msg;
    elError.classList.toggle('hidden', !msg);
  }
  emit('error', { message: msg });
}

/* ─── Checkout session ────────────────────────────────────────────────────── */
async function ensureCheckoutSession() {
  setStatus('Preparing your order…');
  const body = await api.post('/v1/billing/checkout-sessions', { plan_id: PLAN_ID });
  _checkoutSessionId = body.session_id;                                    // backend contract: session_id
  sessionStorage.setItem('dpp_checkout_session_id', _checkoutSessionId);  // persist for return.html fallback
  _state = 'CHECKOUT_SESSION_CREATED';
  emit('session_created', body);
  return body;
}

/* ─── PayPal SDK ─────────────────────────────────────────────────────────── */
function getPayPalClientId() {
  const cfg = window.__DPP_CONFIG__;
  if (!cfg?.PAYPAL_CLIENT_ID) {
    throw new Error('B-01: PAYPAL_CLIENT_ID not configured. Contact support.');
  }
  return cfg.PAYPAL_CLIENT_ID;
}

function loadPayPalSdk() {
  return new Promise((resolve, reject) => {
    if (window.paypal) { resolve(window.paypal); return; }
    const clientId = getPayPalClientId();
    const script = document.createElement('script');
    // currency=USD, intent=capture, disable-funding limits to card+paypal
    script.src = `https://www.paypal.com/sdk/js?client-id=${encodeURIComponent(clientId)}&currency=USD&intent=capture&disable-funding=venmo,paylater`;
    script.onload  = () => resolve(window.paypal);
    script.onerror = () => reject(new Error('Failed to load PayPal SDK'));
    document.head.appendChild(script);
  });
}

/* ─── Render PayPal button ────────────────────────────────────────────────── */
export async function renderPayPalButton(containerSelector) {
  elPaypalContainer = document.querySelector(containerSelector);
  if (!elPaypalContainer) throw new Error(`PayPal container not found: ${containerSelector}`);

  const paypal = await loadPayPalSdk();

  paypal.Buttons({
    style: {
      layout: 'vertical',
      color:  'gold',
      shape:  'rect',
      label:  'pay',
      height: 48,
    },

    // Called when user clicks the PayPal button — create the order server-side
    createOrder: async () => {
      try {
        setError('');
        await ensureCheckoutSession();

        setStatus('Creating PayPal order…');
        const res = await api.post('/v1/billing/paypal/orders', {
          session_id: _checkoutSessionId,  // backend contract: session_id
          plan_id: PLAN_ID,
        });

        _state = 'PAYPAL_ORDER_CREATED';
        emit('order_created', res);
        setStatus('Redirecting to PayPal…');
        return res.paypal_order_id;

      } catch (err) {
        const msg = err instanceof ApiError ? err.detail : err.message;
        setError(msg);
        setStatus('');
        throw err; // PayPal SDK will show its own error UI
      }
    },

    // Called after user approves on PayPal — submit capture non-authoritatively
    onApprove: async (data) => {
      try {
        setStatus('Processing payment…');
        _state = 'CAPTURE_SUBMITTED';

        await api.post('/v1/billing/paypal/capture', {
          session_id:      _checkoutSessionId,  // backend contract: session_id
          paypal_order_id: data.orderID,
        });

        // DEC-V1-07/08: capture endpoint returns 202 — we must poll, not assume success
        setStatus('Payment submitted — confirming…');
        startPolling();

      } catch (err) {
        const msg = err instanceof ApiError ? err.detail : err.message;
        setError(`Capture failed: ${msg}. If payment was taken, contact support.`);
        setStatus('');
      }
    },

    onCancel: () => {
      _state = 'CANCELED';
      emit('canceled', {});
      setStatus('Payment canceled. You can try again.', 'warning');
    },

    onError: (err) => {
      const msg = err?.message ?? String(err);
      setError(`PayPal error: ${msg}`);
      setStatus('');
      emit('paypal_error', { error: msg });
    },

  }).render(containerSelector);
}

/* ─── Polling ────────────────────────────────────────────────────────────── */
function startPolling() {
  _pollingStartedAt = Date.now();
  showPendingPanel();
  scheduleNextPoll();
}

function scheduleNextPoll() {
  _pollingTimer = setTimeout(poll, POLLING_INTERVAL_MS);
}

async function poll() {
  if (Date.now() - _pollingStartedAt > POLLING_TIMEOUT_MS) {
    stopPolling();
    setStatus('Payment is taking longer than expected. Please check your email or contact support.', 'warning');
    emit('timeout', {});
    return;
  }

  try {
    const status = await api.get('/v1/onboarding/status');
    const steps = status.steps;

    if (steps.entitlement_active) {
      stopPolling();
      _state = 'PAID_VERIFIED';
      showDonePanel();
      emit('paid', { status });
      // Redirect to onboarding dashboard
      setTimeout(() => { window.location.href = '/onboarding.html'; }, 1500);

    } else if (steps.payment_complete && !steps.entitlement_active) {
      // payment_complete but not active yet — webhook may still be in-flight
      scheduleNextPoll();

    } else if (status.checkout_session_status === 'FAILED') {
      stopPolling();
      _state = 'FAILED';
      setError('Payment failed. Please try again or contact support.');
      emit('failed', { status });

    } else {
      scheduleNextPoll();
    }

  } catch (err) {
    // Network hiccup — keep polling
    scheduleNextPoll();
  }
}

function stopPolling() {
  if (_pollingTimer) { clearTimeout(_pollingTimer); _pollingTimer = null; }
}

/* ─── Panel toggles ──────────────────────────────────────────────────────── */
function showPendingPanel() {
  elPendingPanel?.classList.remove('hidden');
  elPaypalContainer?.classList.add('hidden');
}

function showDonePanel() {
  elPendingPanel?.classList.add('hidden');
  elDonePanel?.classList.remove('hidden');
}

/* ─── Init ────────────────────────────────────────────────────────────────── */
/**
 * Initialise checkout flow.
 * @param {object} opts
 * @param {string} opts.statusEl      — selector for status alert element
 * @param {string} opts.errorEl       — selector for error element
 * @param {string} opts.pendingPanel  — selector for "payment pending" panel
 * @param {string} opts.donePanel     — selector for "payment done" panel
 */
export function initCheckout(opts = {}) {
  elStatus       = opts.statusEl      ? document.querySelector(opts.statusEl)      : null;
  elError        = opts.errorEl       ? document.querySelector(opts.errorEl)       : null;
  elPendingPanel = opts.pendingPanel  ? document.querySelector(opts.pendingPanel)  : null;
  elDonePanel    = opts.donePanel     ? document.querySelector(opts.donePanel)     : null;
}

export function getState() { return _state; }
