const { test, expect } = require('@playwright/test');

test('shows login screen when not authenticated', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#login-screen')).toBeVisible();
  await expect(page.locator('.google-btn')).toBeVisible();
  await expect(page.locator('#app')).toBeHidden();
});

test('hides login screen and shows app when authenticated', async ({ page }) => {
  const { loginAs } = require('../helpers/auth');
  await loginAs(page);
  await page.goto('/');
  await expect(page.locator('#login-screen')).toBeHidden();
  await expect(page.locator('#app')).toBeVisible();
});

test('google sign-in button points to /auth/login', async ({ page }) => {
  await page.goto('/');
  const href = await page.locator('.google-btn').getAttribute('href');
  expect(href).toBe('/auth/login');
});

test('auth/login redirects to Google OAuth', async ({ page }) => {
  const res = await page.request.get('/auth/login', { maxRedirects: 0 });
  expect(res.status()).toBe(302);
  expect(res.headers()['location']).toContain('accounts.google.com');
});
