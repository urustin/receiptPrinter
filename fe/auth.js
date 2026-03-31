const AUTH_KEY     = 'auth_token';
const REDIRECT_KEY = 'auth_redirect';

function getToken()   { return localStorage.getItem(AUTH_KEY); }
function saveToken(t) { localStorage.setItem(AUTH_KEY, t); }
function clearToken() { localStorage.removeItem(AUTH_KEY); }

function logout() {
  clearToken();
  location.replace('/');
}

function authHeaders() {
  return {
    'Authorization': `Bearer ${getToken()}`,
    'Content-Type': 'application/json',
  };
}

async function authFetch(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: { ...authHeaders(), ...(options.headers || {}) },
  });
  if (res.status === 401) {
    clearToken();
    _requireAuth();
    throw new Error('Unauthorized');
  }
  return res;
}

// ── Internal helpers ──────────────────────────────────────────────────────────

function _requireAuth() {
  if (location.pathname === '/' || location.pathname === '') {
    _showLoginModal();
  } else {
    localStorage.setItem(REDIRECT_KEY, location.pathname);
    location.replace('/');
  }
}

function _showLoginModal() {
  const modal = document.getElementById('login-modal');
  const app   = document.getElementById('app');
  if (modal) modal.style.display = 'flex';
  if (app)   app.style.display   = 'none';
}

function _showApp() {
  const modal = document.getElementById('login-modal');
  const app   = document.getElementById('app');
  if (modal) modal.style.display = 'none';
  if (app)   app.style.removeProperty('display');
}

// ── 메인 페이지 (/) 전용: 모달 방식 ─────────────────────────────────────────

function initAuth() {
  const params = new URLSearchParams(location.search);
  const token  = params.get('token');
  if (token) {
    saveToken(token);
    const redirect = localStorage.getItem(REDIRECT_KEY);
    if (redirect) {
      localStorage.removeItem(REDIRECT_KEY);
      location.replace(redirect);
      return;
    }
    history.replaceState({}, '', '/');
  }
  if (getToken()) {
    _showApp();
  } else {
    _showLoginModal();
  }
}

// ── 하위 페이지 전용: 미인증 시 메인으로 redirect ───────────────────────────

function initSubPageAuth(path) {
  if (!getToken()) {
    localStorage.setItem(REDIRECT_KEY, path || location.pathname);
    location.replace('/');
    return false;
  }
  _showApp();
  return true;
}
