const { test, expect } = require('@playwright/test');
const { loginAs } = require('../helpers/auth');

test.beforeEach(async ({ page }) => {
  await loginAs(page);
  await page.goto('/');
});

test('shows 3 kanban columns', async ({ page }) => {
  const cols = page.locator('.column');
  await expect(cols).toHaveCount(3);
  await expect(page.locator('.col-title').nth(0)).toHaveText('Print');
  await expect(page.locator('.col-title').nth(1)).toHaveText('In Progress');
  await expect(page.locator('.col-title').nth(2)).toHaveText('Done');
});

test('print input placeholder is 할일 #1', async ({ page }) => {
  const input = page.locator('#title');
  await expect(input).toHaveAttribute('placeholder', '할일 #1');
});

test('header shows clock', async ({ page }) => {
  const clock = page.locator('#clock');
  await expect(clock).not.toHaveText('--:--:--');
});

test('sign out button is visible when logged in', async ({ page }) => {
  await expect(page.locator('.signout-btn')).toBeVisible();
});
