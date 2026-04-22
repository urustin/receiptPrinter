const { test, expect } = require('@playwright/test');
const { loginAs } = require('../helpers/auth');

// 메인 허브 페이지(/)의 인증 흐름 테스트

test('미인증 상태에서 로그인 모달이 보인다', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#login-modal')).toBeVisible();
  await expect(page.locator('.google-btn')).toBeVisible();
  await expect(page.locator('#app')).toBeHidden();
});

test('인증 상태에서 앱이 표시되고 로그인 모달은 숨겨진다', async ({ page }) => {
  await loginAs(page);
  await page.goto('/');
  await expect(page.locator('#login-modal')).toBeHidden();
  await expect(page.locator('#app')).toBeVisible();
});

test('Google 로그인 버튼이 /auth/login을 가리킨다', async ({ page }) => {
  await page.goto('/');
  const href = await page.locator('.google-btn').getAttribute('href');
  expect(href).toBe('/auth/login');
});

test('/auth/login이 Google OAuth로 302 리다이렉트한다', async ({ page }) => {
  const res = await page.request.get('/auth/login', { maxRedirects: 0 });
  expect(res.status()).toBe(302);
  expect(res.headers()['location']).toContain('accounts.google.com');
});
