const AUTH_KEY = 'auth_token';

function getToken() {
  return localStorage.getItem(AUTH_KEY);
}

function saveToken(token) {
  localStorage.setItem(AUTH_KEY, token);
}

function clearToken() {
  localStorage.removeItem(AUTH_KEY);
}

function logout() {
  clearToken();
  _showLogin();
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
    _showLogin();
    throw new Error('Unauthorized');
  }
  return res;
}

function _showLogin() {
  document.getElementById('login-screen').style.display = 'flex';
  document.getElementById('app').style.display = 'none';
}

function _showApp() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app').style.display = '';
}

function initAuth() {
  const params = new URLSearchParams(location.search);
  const tokenFromUrl = params.get('token');
  if (tokenFromUrl) {
    saveToken(tokenFromUrl);
    history.replaceState({}, '', '/');
  }
  if (getToken()) {
    _showApp();
  } else {
    _showLogin();
  }
}
