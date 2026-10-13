/**
 * app.js — FM Invoice Readiness Mailer frontend utilities
 * Every fetch() handles errors; no silent failures.
 */

'use strict';

// ── API wrapper ──────────────────────────────────────────────────────────────
const API = {
  async fetch(url, options = {}) {
    const response = await fetch(url, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    if (!response.ok) {
      let errorMsg = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        errorMsg = body?.error?.message || body?.error || errorMsg;
      } catch (_) {}
      throw new Error(errorMsg);
    }
    const data = await response.json();
    if (data.success === false) {
      throw new Error(data.error?.message || 'Server returned an error');
    }
    return data;
  },

  async get(url) {
    return this.fetch(url, { method: 'GET' });
  },

  async post(url, body) {
    return this.fetch(url, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  async postForm(url, formData) {
    const response = await fetch(url, { method: 'POST', body: formData });
    if (!response.ok) {
      let errorMsg = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        errorMsg = body?.error?.message || body?.error || errorMsg;
      } catch (_) {}
      throw new Error(errorMsg);
    }
    const data = await response.json();
    if (data.success === false) {
      throw new Error(data.error?.message || 'Server returned an error');
    }
    return data;
  },
};

// ── Polling helper ───────────────────────────────────────────────────────────
class Poller {
  constructor(url, onUpdate, onComplete, onError, interval = 2000) {
    this._url = url;
    this._onUpdate = onUpdate;
    this._onComplete = onComplete;
    this._onError = onError;
    this._interval = interval;
    this._timer = null;
    this._running = false;
  }

  start() {
    if (this._running) return;
    this._running = true;
    this._poll();
  }

  stop() {
    this._running = false;
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
  }

  async _poll() {
    if (!this._running) return;
    try {
      const data = await API.get(this._url);
      const payload = data.data || data;
      this._onUpdate(payload);
      if (payload.complete) {
        this.stop();
        this._onComplete(payload);
        return;
      }
    } catch (err) {
      this._onError(err);
      this.stop();
      return;
    }
    if (this._running) {
      this._timer = setTimeout(() => this._poll(), this._interval);
    }
  }
}

// ── UI helpers ───────────────────────────────────────────────────────────────
function showError(msg, containerId = 'error-banner') {
  const el = document.getElementById(containerId);
  if (el) {
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; }, 8000);
  } else {
    console.error('[WM]', msg);
  }
}

function showSuccess(msg, containerId = 'success-banner') {
  const el = document.getElementById(containerId);
  if (el) {
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; }, 4000);
  }
}

function setLoading(buttonEl, loading, originalText) {
  if (!buttonEl) return;
  buttonEl.disabled = loading;
  buttonEl.textContent = loading ? 'Loading…' : (originalText || buttonEl.textContent);
}

// ── Export ───────────────────────────────────────────────────────────────────
window.WM = { API, Poller, showError, showSuccess, setLoading };
