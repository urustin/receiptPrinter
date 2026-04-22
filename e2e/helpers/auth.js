const crypto = require('crypto');

const SECRET_KEY = '0684d5a809d932da47140ee34102c260e95c79a98c371425c5abd77e7e7d338b';
const TEST_EMAIL = 'test@example.com';
const TEST_NAME  = 'Test User';

function b64url(str) {
  return Buffer.from(str).toString('base64url');
}

/**
 * HS256 JWT 생성 (백엔드와 동일한 포맷).
 */
function makeJwt(payload = {}) {
  const header = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body   = b64url(JSON.stringify({ email: TEST_EMAIL, name: TEST_NAME, ...payload }));
  const sig    = crypto
    .createHmac('sha256', SECRET_KEY)
    .update(`${header}.${body}`)
    .digest('base64url');
  return `${header}.${body}.${sig}`;
}

/**
 * JWT를 localStorage에 주입해 로그인 상태로 만든다.
 */
async function loginAs(page, payload = {}) {
  const token = makeJwt(payload);
  await page.addInitScript((t) => {
    localStorage.setItem('auth_token', t);
  }, token);
}

/**
 * 인증이 필요한 API 요청 헬퍼.
 */
async function apiRequest(context, method, path, body) {
  return context.fetch(`http://localhost:60021${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${makeJwt()}`,
      'Content-Type': 'application/json',
    },
    data: body ? JSON.stringify(body) : undefined,
  });
}

module.exports = { loginAs, makeJwt, apiRequest, TEST_EMAIL, TEST_NAME };
