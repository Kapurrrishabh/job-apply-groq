// Small progressive enhancements; every page also works without JavaScript.
document.addEventListener('click', e => {
  const all = e.target.closest('[data-select-all]');
  if (all) document.querySelectorAll(`input[type=checkbox][name="${all.dataset.selectAll}"]`).forEach(c => { c.checked = all.checked; });
  const conf = e.target.closest('[data-confirm]');
  if (conf && !window.confirm(conf.dataset.confirm)) e.preventDefault();
});

// AI Execution Console: append real events from /api/events.
(function console_() {
  const box = document.getElementById('console');
  if (!box) return;
  let after = 0;
  const filter = document.getElementById('console-filter');
  const active = document.getElementById('console-active');
  const render = ev => {
    const row = document.createElement('details');
    row.className = `ev ev-${ev.level}`; row.dataset.level = ev.level;
    const sum = document.createElement('summary');
    const parts = [`[${ev.ts}]`, ev.platform ? `${ev.platform}` : '', ev.skill ? `· ${ev.skill}` : '', ev.message, ev.duration_ms != null ? `(${(ev.duration_ms / 1000).toFixed(1)}s)` : ''];
    sum.textContent = parts.filter(Boolean).join(' ');
    row.appendChild(sum);
    const pre = document.createElement('pre');
    pre.textContent = JSON.stringify({operation: ev.op, status: ev.status, run: ev.run, ...ev.detail}, null, 2);
    row.appendChild(pre);
    if (filter && filter.value && filter.value !== ev.level) row.hidden = true;
    box.appendChild(row);
  };
  const poll = async () => {
    try {
      const r = await fetch(`/api/events?after=${after}`, {credentials: 'same-origin'});
      if (!r.ok) return;
      const data = await r.json();
      const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
      data.events.forEach(ev => { render(ev); after = Math.max(after, ev.id); });
      if (stick) box.scrollTop = box.scrollHeight;
      if (active) active.textContent = data.active.length ? data.active.map(a => `${a.kind} #${a.id} (${a.status})`).join(', ') : 'idle';
    } catch (err) { /* offline for a moment; try again next tick */ }
  };
  if (filter) filter.addEventListener('change', () => box.querySelectorAll('.ev').forEach(r => { r.hidden = filter.value && r.dataset.level !== filter.value; }));
  poll(); setInterval(poll, 1500);
})();

// Pages waiting on background work refresh themselves.
(function refresh() {
  const el = document.querySelector('[data-refresh]');
  if (el) setTimeout(() => window.location.reload(), Number(el.dataset.refresh) * 1000);
})();
