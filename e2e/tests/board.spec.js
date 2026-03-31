// 프린트 보드 UI 테스트 (/print 페이지)
const { test, expect } = require('@playwright/test');
const { loginAs } = require('../helpers/auth');

test.beforeEach(async ({ page }) => {
  await loginAs(page);
  await page.goto('/print');
});

test('칸반 3개 컬럼이 표시된다', async ({ page }) => {
  const cols = page.locator('.column');
  await expect(cols).toHaveCount(3);
  await expect(page.locator('.col-title').nth(0)).toHaveText('Print');
  await expect(page.locator('.col-title').nth(1)).toHaveText('In Progress');
  await expect(page.locator('.col-title').nth(2)).toHaveText('Done');
});

test('프린트 입력창 placeholder가 "할일 #1"이다', async ({ page }) => {
  const input = page.locator('#title');
  await expect(input).toHaveAttribute('placeholder', '할일 #1');
});

test('로그인 상태에서 로그아웃 버튼이 보인다', async ({ page }) => {
  await expect(page.locator('.signout-btn')).toBeVisible();
});
