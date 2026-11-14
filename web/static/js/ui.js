// Small DOM helpers shared by Studio and Viewer.
export const $ = (s, r = document) => r.querySelector(s);
export function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add('hidden'), 3200);
}
export function download(blob, name) {
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: name });
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1e4);
}
const btn = (txt, title, fn, cls = 'ib') => Object.assign(document.createElement('button'), { textContent: txt, title, className: cls, onclick: fn, ariaLabel: title });

// Parts outliner: show/hide, solo, and (creator only) rename/delete.
export function mountOutliner(stage, el, editable) {
  const open = new Set(), hasMesh = o => { let f = false; o.traverse(c => c.isMesh && (f = true)); return f; };
  let queued = false;
  const render = () => {
    queued = false; el.replaceChildren();
    const walk = (o, depth) => {
      const kids = o.children.filter(hasMesh), row = document.createElement('div');
      row.className = 'orow' + (stage.selected === o ? ' sel' : ''); row.style.paddingLeft = depth * 12 + 'px';
      row.append(kids.length ? btn(open.has(o.uuid) ? '▾' : '▸', 'Expand', () => { open.has(o.uuid) ? open.delete(o.uuid) : open.add(o.uuid); render(); }) : Object.assign(document.createElement('span'), { style: 'width:22px' }));
      const nm = Object.assign(document.createElement('span'), { className: 'nm', textContent: o.name || (o.isMesh ? 'Mesh' : 'Group') });
      nm.onclick = () => stage.selectable && stage.select(stage.topPart(o));
      if (editable) nm.ondblclick = () => { const n = prompt('Rename part', o.name); if (n && n.trim()) stage.rename(o, n.trim().slice(0, 60)); };
      row.append(nm, btn(o.visible ? '●' : '○', 'Show / hide', () => stage.toggleVisible(o)), btn('S', 'Solo / isolate', () => stage.solo(o)));
      if (editable) row.append(btn('✕', 'Delete part', () => stage.remove(o)));
      el.append(row); if (open.has(o.uuid)) kids.forEach(k => walk(k, depth + 1));
    };
    stage.assembly.children.filter(hasMesh).forEach(c => walk(c, 0));
  };
  const q = () => { if (!queued) { queued = true; requestAnimationFrame(render); } };
  ['tree', 'change', 'select'].forEach(e => stage.addEventListener(e, q)); render();
}

// Bottom bar: annotation toggle + step / auto-play through annotation stops.
export function mountTour(stage, el, canTour, onLocked) {
  el.className = 'fixed bottom-10 left-1/2 -translate-x-1/2 z-20 glass rounded-2xl px-3 py-2 flex items-center gap-2';
  const label = Object.assign(document.createElement('span'), { className: 'text-xs text-slate-300 min-w-[90px] text-center', textContent: 'No callouts' });
  const gate = fn => () => (canTour ? fn() : onLocked());
  const play = btn('▶ Tour', 'Play tour', gate(() => (stage._tour ? stage.tourStop() : stage.tourPlay())), 'btn'),
    eye = btn('Callouts', 'Toggle annotations', () => { eye.classList.toggle('on'); stage.showAnnos(eye.classList.contains('on')); }, 'btn on');
  el.append(btn('◀', 'Previous stop', gate(() => stage.tourStep(-1)), 'btn'), play, btn('▶', 'Next stop', gate(() => stage.tourStep(1)), 'btn'), label, eye);
  const upd = () => { label.textContent = stage.annotations.length ? (stage.pinned == null ? `${stage.annotations.length} callouts` : `Stop ${stage.pinned + 1}/${stage.annotations.length}`) : 'No callouts'; };
  stage.addEventListener('annos', upd); stage.addEventListener('focus', upd); stage.addEventListener('config', upd);
  stage.addEventListener('tour', e => (play.textContent = e.detail ? '■ Stop' : '▶ Tour'));
}

// Dropdown menus of the top bar: click or hover to switch, arrow keys, Escape and outside click to close.
export function mountMenus() {
  const btns = [...document.querySelectorAll('[data-menu]')];
  const panel = b => document.getElementById('menu-' + b.dataset.menu);
  const items = p => [...p.querySelectorAll('.mitem:not(:disabled):not(.hidden)')];
  const close = () => btns.forEach(b => { b.setAttribute('aria-expanded', 'false'); panel(b).classList.add('hidden'); });
  const isOpen = () => btns.some(b => b.getAttribute('aria-expanded') === 'true');
  const open = (b, focusFirst) => {
    close(); const p = panel(b); p.classList.remove('hidden');
    const r = b.getBoundingClientRect(); p.style.top = r.bottom + 4 + 'px'; p.style.left = Math.max(8, Math.min(r.left, innerWidth - p.offsetWidth - 8)) + 'px';
    b.setAttribute('aria-expanded', 'true'); if (focusFirst) items(p)[0]?.focus();
  };
  btns.forEach(b => {
    b.addEventListener('click', () => { // a click right after hovering over to this menu must not close it again
      if (b.getAttribute('aria-expanded') !== 'true') open(b); else if (performance.now() - (b._hoverAt || 0) > 600) close();
    });
    b.addEventListener('mouseenter', () => { if (isOpen() && b.getAttribute('aria-expanded') !== 'true') { b._hoverAt = performance.now(); open(b); } });
    b.addEventListener('keydown', e => { if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(b, true); } });
    panel(b).addEventListener('click', e => { if (e.target.closest('.mitem')) close(); }); // choosing an item closes the menu
    panel(b).addEventListener('keydown', e => {
      const list = items(panel(b)), i = list.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); list[(i + 1) % list.length]?.focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); list[(i - 1 + list.length) % list.length]?.focus(); }
      else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { const n = btns[(btns.indexOf(b) + (e.key === 'ArrowRight' ? 1 : -1) + btns.length) % btns.length]; open(n, true); }
      else if (e.key === 'Escape') { close(); b.focus(); }
    });
  });
  document.addEventListener('pointerdown', e => { if (!e.target.closest('.menu')) close(); }, true);
  addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  addEventListener('resize', close);
  return close;
}
