const crypto = require('crypto');

const SECRET_KEY = '0684d5a809d932da47140ee34102c260e95c79a98c371425c5abd77e7e7d338b';
const TEST_EMAIL = 'test@example.com';
const TEST_NAME  = 'Test User';

/**
 * Generate a HS256 JWT (same format as the backend issues).
 */
function makeJwt(payload = {}) {
  const header  = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body    = b64url(JSON.stringify({ email: TEST_EMAIL, name: TEST_NAME, ...payload }));
  const sig     = crypto
    .createHmac('sha256', SECRET_KEY)
    .update(`${header}.${body}`)
    .digest('base64url');
  return `${header}.${body}.${sig}`;
}

function b64url(str) {
  return Buffer.from(str).toString('base64url');
}

/**
 * Inject the JWT into localStorage so the page treats the user as logged in.
 */
async function loginAs(page, payload = {}) {
  const token = makeJwt(payload);
  await page.addInitScript((t) => {
    localStorage.setItem('auth_token', t);
  }, token);
}

module.exports = { loginAs, TEST_EMAIL, TEST_NAME };
