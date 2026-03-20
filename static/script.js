function tick() {
  document.getElementById('clock').textContent =
    new Date().toLocaleString('ko-KR', { hour12: false });
}
tick(); setInterval(tick, 1000);

async function sendPrint() {
  const btn = document.getElementById('btn');
  const toast = document.getElementById('toast');
  const title = document.getElementById('title').value.trim();

  if (!title) {
    showToast('Please enter a title.', 'error'); return;
  }

  btn.disabled = true;
  btn.textContent = 'Printing…';
  toast.className = 'toast';

  try {
    const res = await fetch('/print', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ title })
    });
    const data = await res.json();
    if (res.ok) {
      showToast('✓ Printed successfully!', 'success');
      document.getElementById('title').value = '';
    } else {
      showToast('Error: ' + data.error, 'error');
    }
  } catch(e) {
    showToast('Connection error.', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Print';
  }
}

function showToast(msg, type) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + type;
}

async function toggleHistory() {
  const panel = document.getElementById('history-panel');
  const btn = document.getElementById('history-btn');
  if (panel.style.display !== 'none') {
    panel.style.display = 'none';
    btn.textContent = 'History';
    return;
  }

  btn.textContent = 'Loading…';
  try {
    const res = await fetch('/history');
    const data = await res.json();
    const list = document.getElementById('history-list');
    list.innerHTML = data.length
      ? data.map(r => `<li><span class="h-title">${r.title}</span><span class="h-time">${r.printed_at}</span></li>`).join('')
      : '<li style="color:#aeaeb2">No history yet.</li>';
    panel.style.display = 'block';
    btn.textContent = 'Hide History';
  } catch(e) {
    btn.textContent = 'History';
    showToast('Failed to load history.', 'error');
  }
}
