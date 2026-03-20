/**
 * Full lifecycle test:
 *   1. Insert 4 items directly into DB (bypass printer hardware)
 *   2. Mark 2 as done via API  → verify completed_at is set, status is 'done'
 *   3. Delete 2 via API        → verify they are gone
 *   4. Verify board UI reflects final state
 *   5. Clean up remaining items
 */
const { test, expect } = require('@playwright/test');
const { execSync } = require('child_process');
const crypto = require('crypto');
const { loginAs, TEST_EMAIL } = require('../helpers/auth');

const SECRET_KEY = '0684d5a809d932da47140ee34102c260e95c79a98c371425c5abd77e7e7d338b';
const TAG = `lifecycle-${Date.now()}`;

// ── JWT / API helpers ─────────────────────────────
function makeJwt() {
  const h = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const b = b64url(JSON.stringify({ email: TEST_EMAIL, name: 'Test User' }));
  const s = crypto.createHmac('sha256', SECRET_KEY).update(`${h}.${b}`).digest('base64url');
  return `${h}.${b}.${s}`;
}
function b64url(str) { return Buffer.from(str).toString('base64url'); }

async function api(ctx, method, path) {
  return ctx.fetch(`http://localhost:60021${path}`, {
    method,
    headers: { Authorization: `Bearer ${makeJwt()}` },
  });
}

// ── DB helpers ────────────────────────────────────
function dbExec(sql) {
  return execSync(
    `docker exec printer-db-1 psql -U printer -d printer -t -A -c "${sql}"`
  ).toString().trim();
}

function insertJob(title) {
  const row = dbExec(
    `INSERT INTO print_jobs (title, printed_by, status) VALUES ('${title}', '${TEST_EMAIL}', 'progress') RETURNING id;`
  );
  return parseInt(row);
}

function cleanupTag() {
  dbExec(`DELETE FROM print_jobs WHERE title LIKE '${TAG}%' AND printed_by='${TEST_EMAIL}';`);
}

// ── Test ──────────────────────────────────────────
test('lifecycle: insert 4 → done 2 → delete 2 → verify', async ({ page, request: ctx }) => {
  // 0. ensure clean state
  cleanupTag();

  // 1. Insert 4 items directly into DB
  const titles = [`${TAG}-A`, `${TAG}-B`, `${TAG}-C`, `${TAG}-D`];
  const ids = titles.map(insertJob);
  const [idA, idB, idC, idD] = ids;

  try {
    // 2. Verify all 4 appear in progress via API
    const hist1 = await (await api(ctx, 'GET', '/history')).json();
    const progressIds = hist1.progress.map(j => j.id);
    for (const id of ids) {
      expect(progressIds).toContain(id);
    }

    // 3. Mark A and B as done
    expect((await api(ctx, 'PATCH', `/jobs/${idA}/done`)).ok()).toBe(true);
    expect((await api(ctx, 'PATCH', `/jobs/${idB}/done`)).ok()).toBe(true);

    // 4. Verify A and B are in done with completed_at set
    const hist2 = await (await api(ctx, 'GET', '/history')).json();
    const doneIds = hist2.done.map(j => j.id);
    expect(doneIds).toContain(idA);
    expect(doneIds).toContain(idB);

    const jobA = hist2.done.find(j => j.id === idA);
    const jobB = hist2.done.find(j => j.id === idB);
    expect(jobA.completed_at).not.toBeNull();
    expect(jobB.completed_at).not.toBeNull();
    expect(jobA.status).toBe('done');
    expect(jobB.status).toBe('done');

    // 5. C and D still in progress
    const stillProgress = hist2.progress.map(j => j.id);
    expect(stillProgress).toContain(idC);
    expect(stillProgress).toContain(idD);

    // 6. Delete C and D
    expect((await api(ctx, 'DELETE', `/jobs/${idC}`)).ok()).toBe(true);
    expect((await api(ctx, 'DELETE', `/jobs/${idD}`)).ok()).toBe(true);

    // 7. Verify C and D are gone
    const hist3 = await (await api(ctx, 'GET', '/history')).json();
    const allIds = [...hist3.progress, ...hist3.done].map(j => j.id);
    expect(allIds).not.toContain(idC);
    expect(allIds).not.toContain(idD);

    // 8. Verify UI board reflects final state (2 done items from this test)
    await loginAs(page);
    await page.goto('/');
    await page.waitForTimeout(600);

    const doneList = page.locator('#done-list .job-item');
    const doneTitles = await doneList.allTextContents();
    const tagDoneTitles = doneTitles.filter(t => t.includes(TAG));
    expect(tagDoneTitles.length).toBe(2);

    const progressList = page.locator('#progress-list .job-item');
    const progressTitles = await progressList.allTextContents();
    const tagProgressTitles = progressTitles.filter(t => t.includes(TAG));
    expect(tagProgressTitles.length).toBe(0);

  } finally {
    // 9. Clean up — delete A and B that were marked done
    cleanupTag();
  }
});
