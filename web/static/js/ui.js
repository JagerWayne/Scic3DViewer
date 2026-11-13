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
  el.className = 'fixed bottom-4 left-1/2 -translate-x-1/2 z-20 glass rounded-2xl px-3 py-2 flex items-center gap-2';
  const label = Object.assign(document.createElement('span'), { className: 'text-xs text-slate-300 min-w-[90px] text-center', textContent: 'No callouts' });
  const gate = fn => () => (canTour ? fn() : onLocked());
  const play = btn('▶ Tour', 'Play tour', gate(() => (stage._tour ? stage.tourStop() : stage.tourPlay())), 'btn'),
    eye = btn('Callouts', 'Toggle annotations', () => { eye.classList.toggle('on'); stage.showAnnos(eye.classList.contains('on')); }, 'btn on');
  el.append(btn('◀', 'Previous stop', gate(() => stage.tourStep(-1)), 'btn'), play, btn('▶', 'Next stop', gate(() => stage.tourStep(1)), 'btn'), label, eye);
  const upd = () => { label.textContent = stage.annotations.length ? (stage.pinned == null ? `${stage.annotations.length} callouts` : `Stop ${stage.pinned + 1}/${stage.annotations.length}`) : 'No callouts'; };
  stage.addEventListener('annos', upd); stage.addEventListener('focus', upd); stage.addEventListener('config', upd);
  stage.addEventListener('tour', e => (play.textContent = e.detail ? '■ Stop' : '▶ Tour'));
}
