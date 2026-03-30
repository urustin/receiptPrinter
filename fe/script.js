initAuth();

// ── Load board ────────────────────────────────────
async function loadBoard() {
  try {
    const res = await authFetch('/history');
    const data = await res.json();
    renderList('progress-list', 'progress-count', data.progress, false);
    renderList('done-list',     'done-count',     data.done,     true);
  } catch {}
}

function renderList(listId, countId, items, isDone) {
  const list = document.getElementById(listId);
  const count = document.getElementById(countId);
  count.textContent = items.length;

  if (!items.length) {
    list.innerHTML = '<li class="empty-state">Nothing here yet.</li>';
    return;
  }

  list.innerHTML = items.map(r => `
    <li class="job-item ${isDone ? 'done-item' : ''}" data-id="${r.id}">
      ${!isDone ? `<span class="drag-handle" title="Drag to reorder">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <circle cx="9" cy="5"  r="1.5"/><circle cx="15" cy="5"  r="1.5"/>
          <circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/>
          <circle cx="9" cy="19" r="1.5"/><circle cx="15" cy="19" r="1.5"/>
        </svg>
      </span>` : ''}
      <span class="job-title">${escHtml(r.title)}</span>
      <span class="job-time">${isDone ? (r.completed_at ?? r.printed_at) : r.printed_at}</span>
      ${!isDone ? `
      <div class="job-actions">
        <button class="btn-done" title="Mark done" onclick="markDone(${r.id})">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
            <polyline points="20 6 9 17 4 12"/>
          </svg>
        </button>
        <button class="btn-delete" title="Delete" onclick="deleteJob(${r.id})">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>` : ''}
    </li>`).join('');

  if (!isDone) initDrag(list);
}

function escHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Print ─────────────────────────────────────────
async function sendPrint() {
  const btn = document.getElementById('print-btn');
  const input = document.getElementById('title');
  const title = input.value.trim();

  if (!title) { showToast('할일을 입력해주세요.', 'error'); return; }

  btn.disabled = true;
  btn.textContent = 'Printing…';

  try {
    const res = await authFetch('/print', {
      method: 'POST',
      body: JSON.stringify({ title }),
    });
    const data = await res.json();
    if (res.ok) {
      showToast('✓ Printed!', 'success');
      input.value = '';
      loadBoard();
    } else {
      showToast('Error: ' + (data.detail ?? data.error), 'error');
    }
  } catch {
    showToast('Connection error.', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg> Print`;
  }
}

// ── Mark done ─────────────────────────────────────
async function markDone(id) {
  try {
    const res = await authFetch(`/jobs/${id}/done`, { method: 'PATCH' });
    if (res.ok) loadBoard();
  } catch {}
}

// ── Delete ────────────────────────────────────────
async function deleteJob(id) {
  try {
    const res = await authFetch(`/jobs/${id}`, { method: 'DELETE' });
    if (res.ok) loadBoard();
  } catch {}
}

// ── Drag to reorder (Pointer Events — mouse + touch) ──
function initDrag(list) {
  let dragging = null;   // the li being dragged
  let ghost    = null;   // floating clone
  let offsetX  = 0, offsetY = 0;

  list.querySelectorAll('.job-item').forEach(item => {
    item.addEventListener('pointerdown', onDown);
  });

  function onDown(e) {
    if (e.target.closest('button')) return;
    e.preventDefault();
    const li = e.currentTarget;
    dragging = li;

    const rect = li.getBoundingClientRect();
    offsetX = e.clientX - rect.left;
    offsetY = e.clientY - rect.top;

    // ghost
    ghost = li.cloneNode(true);
    ghost.classList.add('drag-ghost');
    ghost.style.width  = rect.width  + 'px';
    ghost.style.height = rect.height + 'px';
    ghost.style.left   = rect.left   + 'px';
    ghost.style.top    = rect.top    + window.scrollY + 'px';
    document.body.appendChild(ghost);

    li.classList.add('drag-source');

    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup',   onUp);
  }

  function onMove(e) {
    if (!dragging) return;
    ghost.style.left = (e.clientX - offsetX) + 'px';
    ghost.style.top  = (e.clientY - offsetY + window.scrollY) + 'px';

    // find insertion point
    const items = [...list.querySelectorAll('.job-item:not(.drag-source)')];
    list.querySelectorAll('.drop-above, .drop-below').forEach(el => {
      el.classList.remove('drop-above', 'drop-below');
    });

    let target = null, after = false;
    for (const item of items) {
      const r = item.getBoundingClientRect();
      if (e.clientY < r.top + r.height / 2) {
        target = item; after = false;
        break;
      }
      target = item; after = true;
    }
    if (target) target.classList.add(after ? 'drop-below' : 'drop-above');
  }

  function onUp(e) {
    document.removeEventListener('pointermove', onMove);
    document.removeEventListener('pointerup',   onUp);
    if (!dragging) return;

    ghost.remove();
    dragging.classList.remove('drag-source');
    list.querySelectorAll('.drop-above, .drop-below').forEach(el => {
      const isAbove = el.classList.contains('drop-above');
      el.classList.remove('drop-above', 'drop-below');
      if (isAbove) list.insertBefore(dragging, el);
      else         el.after(dragging);
    });

    ghost    = null;
    dragging = null;

    // persist order
    const ids = [...list.querySelectorAll('.job-item')].map(li => parseInt(li.dataset.id));
    authFetch('/jobs/reorder', {
      method: 'PATCH',
      body: JSON.stringify({ ids }),
    }).catch(() => {});
  }
}

// ── Toast ─────────────────────────────────────────
function showToast(msg, type) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + type;
}

// load on start
loadBoard();
