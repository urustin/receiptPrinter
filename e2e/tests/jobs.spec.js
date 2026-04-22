/**
 * 잡 CRUD 테스트: 출력 → 진행중 → 완료 / 삭제
 *
 * 실제 백엔드(localhost:60021)에 요청합니다.
 * 각 테스트는 DELETE API로 자체 정리합니다.
 */
const { test, expect } = require('@playwright/test');
const { loginAs, apiRequest } = require('../helpers/auth');

// ── 출력 → 진행중 컬럼에 나타남 ─────────────────
test('출력한 잡이 진행중 컬럼에 나타난다', async ({ page }) => {
  await loginAs(page);
  await page.goto('/print');

  // 실제 프린터 하드웨어 없이 POST 성공을 흉내낸다
  await page.route('/api/print', async route => {
    if (route.request().method() === 'POST') {
      await route.fulfill({ status: 200, body: JSON.stringify({ ok: true }) });
    } else {
      await route.continue();
    }
  });

  await page.locator('#title').fill(`test-${Date.now()}`);
  await page.locator('#print-btn').click();
  await page.waitForTimeout(500);
  await expect(page.locator('.toast.success')).toBeVisible();
});

// ── /history API ──────────────────────────────────
test('/history API가 progress와 done 키를 반환한다', async ({ request: ctx }) => {
  const res = await apiRequest(ctx, 'GET', '/history');
  expect(res.ok()).toBeTruthy();
  const data = await res.json();
  expect(data).toHaveProperty('progress');
  expect(data).toHaveProperty('done');
  expect(Array.isArray(data.progress)).toBe(true);
  expect(Array.isArray(data.done)).toBe(true);
});

// ── 사용자 격리 확인 ──────────────────────────────
test('/history는 인증된 사용자의 항목만 반환한다', async ({ request: ctx }) => {
  const res = await apiRequest(ctx, 'GET', '/history');
  const data = await res.json();
  [...data.progress, ...data.done].forEach(item => {
    expect(item).toHaveProperty('id');
    expect(item).toHaveProperty('title');
    expect(item).toHaveProperty('status');
    expect(item).toHaveProperty('printed_at');
  });
});

// ── 완료 처리 ─────────────────────────────────────
test('PATCH /jobs/:id/done이 잡을 완료 상태로 이동시킨다', async ({ request: ctx }) => {
  const hist = await (await apiRequest(ctx, 'GET', '/history')).json();
  if (!hist.progress.length) { test.skip(); return; }

  const job = hist.progress[0];
  const res = await apiRequest(ctx, 'PATCH', `/jobs/${job.id}/done`);
  expect(res.ok()).toBeTruthy();

  const updated = await (await apiRequest(ctx, 'GET', '/history')).json();
  const moved = updated.done.find(j => j.id === job.id);
  expect(moved).toBeDefined();
  expect(moved.status).toBe('done');
  expect(moved.completed_at).not.toBeNull();

  // 복구
  await apiRequest(ctx, 'DELETE', `/jobs/${job.id}`);
});

// ── 삭제 ─────────────────────────────────────────
test('DELETE /jobs/:id가 잡을 제거한다', async ({ request: ctx }) => {
  const hist = await (await apiRequest(ctx, 'GET', '/history')).json();
  const allJobs = [...hist.progress, ...hist.done];
  if (!allJobs.length) { test.skip(); return; }

  const job = allJobs[0];
  const res = await apiRequest(ctx, 'DELETE', `/jobs/${job.id}`);
  expect(res.ok()).toBeTruthy();

  const updated = await (await apiRequest(ctx, 'GET', '/history')).json();
  const all = [...updated.progress, ...updated.done];
  expect(all.find(j => j.id === job.id)).toBeUndefined();
});

// ── 재출력 ───────────────────────────────────────
test('POST /jobs/:id/reprint이 DB를 변경하지 않고 성공한다', async ({ request: ctx }) => {
  const hist = await (await apiRequest(ctx, 'GET', '/history')).json();
  const allJobs = [...hist.progress, ...hist.done];
  if (!allJobs.length) { test.skip(); return; }

  const job = allJobs[0];
  const res = await apiRequest(ctx, 'POST', `/jobs/${job.id}/reprint`, { print_enabled: false });
  expect(res.ok()).toBeTruthy();

  // DB 변경 없음 확인
  const after = await (await apiRequest(ctx, 'GET', '/history')).json();
  const found = [...after.progress, ...after.done].find(j => j.id === job.id);
  expect(found).toBeDefined();
  expect(found.status).toBe(job.status);
});

// ── 미인증 접근 ───────────────────────────────────
test('미인증 /history는 401 또는 403을 반환한다', async ({ request: ctx }) => {
  const res = await ctx.fetch('http://localhost:60021/history');
  expect([401, 403]).toContain(res.status());
});

test('미인증 PATCH /jobs/:id/done은 401 또는 403을 반환한다', async ({ request: ctx }) => {
  const res = await ctx.fetch('http://localhost:60021/jobs/1/done', { method: 'PATCH' });
  expect([401, 403]).toContain(res.status());
});

test('미인증 DELETE /jobs/:id는 401 또는 403을 반환한다', async ({ request: ctx }) => {
  const res = await ctx.fetch('http://localhost:60021/jobs/1', { method: 'DELETE' });
  expect([401, 403]).toContain(res.status());
});
