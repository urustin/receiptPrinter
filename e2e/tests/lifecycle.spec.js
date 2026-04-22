/**
 * 전체 라이프사이클 테스트:
 *   1. DB에 직접 4개 삽입 (프린터 하드웨어 우회)
 *   2. 2개를 API로 완료 처리  → completed_at 설정 및 status='done' 확인
 *   3. 2개를 API로 삭제        → 삭제 확인
 *   4. /print 보드 UI가 최종 상태를 반영하는지 확인
 *   5. 남은 항목 정리
 */
const { test, expect } = require('@playwright/test');
const { execSync } = require('child_process');
const { loginAs, apiRequest, TEST_EMAIL } = require('../helpers/auth');

const TAG = `lifecycle-${Date.now()}`;

// ── DB 헬퍼 ──────────────────────────────────────
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

// ── 테스트 ────────────────────────────────────────
test('라이프사이클: 4개 삽입 → 2개 완료 → 2개 삭제 → 검증', async ({ page, request: ctx }) => {
  cleanupTag();

  // 1. DB에 직접 4개 삽입
  const titles = [`${TAG}-A`, `${TAG}-B`, `${TAG}-C`, `${TAG}-D`];
  const ids = titles.map(insertJob);
  const [idA, idB, idC, idD] = ids;

  try {
    // 2. 4개 모두 진행중에 있는지 확인
    const hist1 = await (await apiRequest(ctx, 'GET', '/history')).json();
    const progressIds = hist1.progress.map(j => j.id);
    for (const id of ids) {
      expect(progressIds).toContain(id);
    }

    // 3. A, B 완료 처리
    expect((await apiRequest(ctx, 'PATCH', `/jobs/${idA}/done`)).ok()).toBe(true);
    expect((await apiRequest(ctx, 'PATCH', `/jobs/${idB}/done`)).ok()).toBe(true);

    // 4. A, B가 완료 목록에 있고 completed_at이 설정됐는지 확인
    const hist2 = await (await apiRequest(ctx, 'GET', '/history')).json();
    const doneIds = hist2.done.map(j => j.id);
    expect(doneIds).toContain(idA);
    expect(doneIds).toContain(idB);

    const jobA = hist2.done.find(j => j.id === idA);
    const jobB = hist2.done.find(j => j.id === idB);
    expect(jobA.completed_at).not.toBeNull();
    expect(jobB.completed_at).not.toBeNull();
    expect(jobA.status).toBe('done');
    expect(jobB.status).toBe('done');

    // 5. C, D는 여전히 진행중
    const stillProgress = hist2.progress.map(j => j.id);
    expect(stillProgress).toContain(idC);
    expect(stillProgress).toContain(idD);

    // 6. C, D 삭제
    expect((await apiRequest(ctx, 'DELETE', `/jobs/${idC}`)).ok()).toBe(true);
    expect((await apiRequest(ctx, 'DELETE', `/jobs/${idD}`)).ok()).toBe(true);

    // 7. C, D가 사라졌는지 확인
    const hist3 = await (await apiRequest(ctx, 'GET', '/history')).json();
    const allIds = [...hist3.progress, ...hist3.done].map(j => j.id);
    expect(allIds).not.toContain(idC);
    expect(allIds).not.toContain(idD);

    // 8. /print 보드 UI가 최종 상태를 반영하는지 확인
    await loginAs(page);
    await page.goto('/print');
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
    // 9. A, B(완료 처리된 것) 정리
    cleanupTag();
  }
});
