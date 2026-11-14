import * as THREE from 'three';
import { Stage, BACKDROPS } from '/static/js/stage.js';
import { $, toast, download, mountOutliner, mountTour, mountMenus } from '/static/js/ui.js';

const MB = 1024 * 1024;
const TIER_FOR = { paint_fill: 'PRO', paint_undo: 'ENTERPRISE', assembly: 'PRO', tour: 'PRO', slicer: 'PRO', explode: 'PRO', measure: 'ENTERPRISE', decimate: 'PRO', base: 'PRO', hdri: 'PRO', capture: 'PRO', cad_import: 'ENTERPRISE' };
const store = { get: k => { try { return localStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private mode */ } } };

const stage = window.stage = new Stage($('#stage'));
const fromGoogle = new URLSearchParams(location.hash.slice(1)).get('token'); // set by /auth/google/callback
if (fromGoogle) { store.set('scic_token', fromGoogle); history.replaceState(null, '', '/studio'); }
let me = null, token = fromGoogle || store.get('scic_token');

async function call(path, opt = {}) {
  const r = await fetch(path, { ...opt, headers: { ...(opt.headers || {}), ...(token ? { Authorization: 'Bearer ' + token } : {}) } });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(typeof j.detail === 'string' ? j.detail : 'Request failed (' + r.status + ')'), { status: r.status });
  return j;
}
const can = t => me?.tools.includes(t);
const need = t => can(t) || (toast(`${t.replace('_', ' ')} needs the ${TIER_FOR[t]} tier`), false);
const on = (id, ev, fn) => $('#' + id).addEventListener(ev, fn);
const val = id => $('#' + id).value;
const show = (id, v = true) => $('#' + id).classList.toggle('hidden', !v);

// ---------- account / tier gating ----------
function applyMe() {
  $('#tier').textContent = me.tier; $('#initial').textContent = (me.name || me.email || '?').trim()[0].toUpperCase();
  $('#who').textContent = me.name + (me.expires_at ? ' · until ' + new Date(me.expires_at * 1000).toLocaleDateString() : '');
  stage.maxAnnotations = me.max_annotations; stage.gizmo = can('assembly'); stage.canPaintUndo = can('paint_undo'); updateStatus();
  document.querySelectorAll('[data-need]').forEach(el => {
    const ok = can(el.dataset.need); el.classList.toggle('opacity-50', !ok);
    if (el.tagName === 'BUTTON') { el.title = ok ? el.title : `Requires ${TIER_FOR[el.dataset.need]}`; return; } // click handlers toast via need()
    el.querySelectorAll('input,select,button').forEach(c => (c.disabled = !ok));
    const sum = el.closest('details')?.querySelector('summary');
    if (sum && !ok && !sum.querySelector('.lock')) sum.append(Object.assign(document.createElement('span'), { className: 'lock', textContent: TIER_FOR[el.dataset.need] }));
  });
  show('authModal', false); updateExplode(); driveUi();
  show('adminLink', me.is_admin); show('upgrade', me.billing_available && me.tier !== 'ENTERPRISE' && !me.is_admin);
  if (!$('#tour').children.length) mountTour(stage, $('#tour'), can('tour'), () => toast('Fly-to tour needs the PRO tier'));
}
async function boot() {
  if (token) {
    try {
      me = await call('/api/me'); applyMe();
      if (fromGoogle && !me.drive_connected && me.drive_available) show('driveModal'); // signed in but Drive was not allowed: ask again
      return;
    } catch { token = null; store.set('scic_token'); }
  }
  const q = new URLSearchParams(location.search).get('login');
  if (q) {
    history.replaceState(null, '', '/studio');
    $('#authErr').textContent = q === 'unavailable' ? 'Google sign-in is not set up on this site yet. Site owner: see GOOGLE_DRIVE_SETUP.md (Part A).' : 'Google sign-in did not complete. Please try again.';
    show('authErr');
  }
  show('authModal');
}
on('signout', 'click', () => { token = null; store.set('scic_token'); location.reload(); });

// ---------- files ----------
async function loadFiles(files, replace) {
  if (replace) {
    if (dirty && !confirm('Discard your unsaved changes?')) return;
    editing = null; current = null; stage.assembly.clear(); stage.annotations = []; stage.buildAnnos(); stage.select(null); stage.setBase(null); stage.clearDims();
    stage.history = []; stage.redoStack = []; stage.emit('history'); setDirty(false);
  }
  for (const f of files) {
    if (/\.(step|stp)$/i.test(f.name) && !need('cad_import')) continue;
    if (f.size > me.max_mb * MB) { toast(`${f.name} is over the ${me.max_mb} MB limit of the ${me.tier} tier`); continue; }
    try { await stage.loadFile(f); if (!modelName || replace) modelName = f.name.replace(/\.[^.]+$/, ''); } catch (err) { toast(err.message); }
  }
  syncSliders(); markDirty();
}
on('open', 'click', () => { $('#file').dataset.replace = '1'; $('#file').click(); });
on('add', 'click', () => { if (need('assembly')) { $('#file').dataset.replace = ''; $('#file').click(); } });
on('file', 'change', e => { const f = [...e.target.files]; if (f.length) loadFiles(f, !!e.target.dataset.replace || !stage.assembly.children.length); e.target.value = ''; });
addEventListener('dragover', e => e.preventDefault());
addEventListener('drop', e => { e.preventDefault(); if (me && e.dataTransfer.files.length) loadFiles([...e.dataTransfer.files], !can('assembly')); });

// ---------- toolbar ----------
document.querySelectorAll('#tools [data-mode]').forEach(b => b.addEventListener('click', () => {
  if (b.dataset.need && !need(b.dataset.need)) return;
  const m = b.dataset.mode, mode = ['brush', 'fill'].includes(m) ? 'paint' : ['ruler', 'angle', 'thickness'].includes(m) ? 'measure' : m;
  stage.setMode(mode, m); setActive(b);
}));
document.querySelectorAll('#tools [data-gizmo]').forEach(b => b.addEventListener('click', () => {
  if (!need('assembly')) return;
  stage.setMode('select'); stage.setGizmoMode(b.dataset.gizmo); setActive(b);
  if (!stage.selected) toast('Click a part to attach the gizmo'); else stage.select(stage.selected);
}));
function setActive(b) { document.querySelectorAll('#tools [data-mode],#tools [data-gizmo]').forEach(x => x.classList.toggle('on', x === b)); }
addEventListener('keydown', e => {
  const k = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey;
  if (mod && k === 's') { e.preventDefault(); return saveProject(false); }
  if (/INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
  if (mod && k === 'z') { e.preventDefault(); return e.shiftKey ? stage.redo() : stage.undo(); }
  if (mod && k === 'y') { e.preventDefault(); return stage.redo(); }
  if (mod) return;
  const g = { w: 'translate', e: 'rotate', r: 'scale' }[k];
  if (g && can('assembly')) { stage.setGizmoMode(g); setActive($(`[data-gizmo=${g}]`)); }
  if (k === 's') toggleSnap();
  if (e.key === '?') show('helpModal');
  if (e.key === 'Escape') { stage.setMode('select'); stage.unpin(); setActive($('[data-mode=select]')); }
});
stage.addEventListener('locked', e => toast(e.detail === 'paint_undo' ? 'Undoing paint strokes needs the ENTERPRISE tier' : `${me.tier} tier allows ${me.max_annotations} callouts. Upgrade for unlimited.`));
stage.addEventListener('toast', e => toast(e.detail));
on('panelBtn', 'click', () => $('#panel').classList.toggle('closed'));

// ---------- panel controls ----------
const bd = $('#backdrop'); Object.keys(BACKDROPS).forEach(k => bd.add(new Option(k[0].toUpperCase() + k.slice(1), k)));
on('backdrop', 'change', () => stage.setBackdrop(val('backdrop')));
on('grid', 'change', e => { stage.grid.visible = e.target.checked; $('#vGrid').setAttribute('aria-checked', e.target.checked); });
on('light', 'input', () => stage.setLight(+val('light')));
const mat = () => stage.setMaterial(+val('rough'), +val('metal'));
on('rough', 'input', mat); on('metal', 'input', mat);
on('env', 'change', () => stage.setEnv(val('env')));
on('envI', 'input', () => stage.setEnv(stage.state.env, +val('envI')));
on('hdr', 'change', async e => {
  const f = e.target.files[0]; if (!f) return;
  if (f.size > 20 * MB) return toast('HDR files are limited to 20 MB');
  try { await stage.setHdr(f, +val('envI')); } catch { toast('Could not read that .hdr file'); }
});
on('pcolor', 'input', () => stage.paintColor.set(val('pcolor')));
on('pradius', 'input', () => (stage.brushR = +val('pradius')));
on('pangle', 'input', () => { stage.fillAngle = +val('pangle'); $('#pangleV').textContent = val('pangle') + '°'; });
on('undo', 'click', () => stage.undo());
on('reset', 'click', () => stage.resetColors());

function syncSliders() { // slicer range follows the model's Z extent
  let zmin = Infinity, zmax = -Infinity; const v = new THREE.Vector3();
  stage.meshesAll().forEach(m => { const p = m.geometry.attributes.position; for (let i = 0; i < p.count; i += Math.max(1, (p.count / 2000) | 0)) { v.fromBufferAttribute(p, i).applyMatrix4(m.matrixWorld); zmin = Math.min(zmin, v.z); zmax = Math.max(zmax, v.z); } });
  if (zmin < zmax) Object.assign($('#clipZ'), { min: zmin, max: zmax, value: zmax });
}
const clip = () => stage.setClip($('#clipOn').checked, +val('clipZ'));
on('clipOn', 'change', clip); on('clipZ', 'input', clip);
on('explode', 'input', () => stage.setExplode(+val('explode') / 100));
stage.addEventListener('explode-reset', () => ($('#explode').value = 0));
function updateExplode() {
  const ok = stage.explodeUnits().length > 1;
  if (can('explode')) $('#explode').disabled = !ok;
  $('#explodeNote').textContent = ok || !can('explode') ? '' : 'Needs two or more parts (disabled for a single or merged mesh).';
  if (!ok) $('#explode').value = 0;
}
on('unit', 'change', () => stage.setUnit(val('unit'), +val('uscale') || 1));
on('uscale', 'input', () => stage.setUnit(val('unit'), +val('uscale') || 1));
on('clearDims', 'click', () => stage.clearDims());
on('zup', 'click', () => { if (!stage.meshesAll().length) return toast('Load a model first'); stage.zUpToYUp(); markDirty(); toast('Model turned upright'); });
on('merge', 'click', () => { if (need('assembly')) { stage.mergeAll(); markDirty(); toast('Merged into one mesh (textures are baked to vertex colors)'); } });

on('bapply', 'click', () => stage.setBase({ shape: val('bshape'), radius: +val('bradius'), height: +val('bheight'), finish: val('bfinish') }));
on('bremove', 'click', () => stage.setBase(null));
on('bbake', 'click', () => stage.bakeBase());
on('keep', 'input', () => ($('#keepV').textContent = val('keep') + '%'));
on('decimate', 'click', async () => {
  const b = $('#decimate'); b.disabled = true; b.textContent = 'Working…';
  try { await stage.decimate(+val('keep') / 100); $('#optNote').textContent = 'UVs/textures are dropped; vertex colors are kept.'; } catch (e) { toast('Simplify failed: ' + e.message); }
  b.disabled = !can('decimate'); b.textContent = 'Simplify';
});
on('revert', 'click', () => stage.revertDecimate());
on('png', 'click', async () => download(await stage.capturePNG(), 'scic3d.png'));
on('webm', 'click', async () => {
  const b = $('#webm'); b.disabled = true; b.textContent = 'Recording…';
  try { download(await stage.captureWebM(6), 'scic3d-360.webm'); } catch (e) { toast(e.message); }
  b.disabled = false; b.textContent = '360° WebM';
});

// ---------- menus, tabs, snapping, undo/redo, view presets ----------
mountMenus();
const openPanel = () => { $('#panel').classList.remove('closed'); $('#vPanel').setAttribute('aria-checked', true); };
function showTab(name) {
  document.querySelectorAll('#panel [data-tab]').forEach(t => { const on = t.dataset.tab === name; t.classList.toggle('on', on); t.setAttribute('aria-selected', on); });
  document.querySelectorAll('#panel [data-pane]').forEach(pn => pn.classList.toggle('hidden', pn.dataset.pane !== name));
}
document.querySelectorAll('#panel [data-tab]').forEach(t => t.addEventListener('click', () => showTab(t.dataset.tab)));
on('mOptimize', 'click', () => { if (!need('decimate')) return; openPanel(); showTab('tools'); $('#secOptimize').open = true; });
on('mPedestal', 'click', () => { if (!need('base')) return; openPanel(); showTab('scene'); $('#secPedestal').open = true; });
function toggleSnap() { stage.setSnap(!stage.snap.on, +val('snapDeg')); }
stage.addEventListener('snap', () => { const on = stage.snap.on; $('#snapBtn').setAttribute('aria-pressed', on); $('#mSnap').setAttribute('aria-checked', on); });
on('snapBtn', 'click', toggleSnap); on('mSnap', 'click', toggleSnap);
on('snapDeg', 'change', () => stage.setSnap(stage.snap.on, +val('snapDeg')));
const historyUi = () => { $('#mUndo').disabled = !stage.history.length; $('#mRedo').disabled = !stage.redoStack.length; };
stage.addEventListener('history', historyUi); historyUi();
on('mUndo', 'click', () => stage.undo()); on('mRedo', 'click', () => stage.redo());
on('mDelete', 'click', () => {
  const part = stage.selected; if (!part) return toast('Select a part first'); if (!need('assembly')) return;
  if (confirm(`Delete "${part.name || 'this part'}"?`)) { stage.remove(part); markDirty(); }
});
function look(dir) { const t = stage.controls.target, d = stage.camera.position.distanceTo(t) || 3; stage.camera.position.copy(t).addScaledVector(new THREE.Vector3(...dir).normalize(), d); stage.controls.update(); }
on('vFrame', 'click', () => stage.frame()); on('vFront', 'click', () => look([0, .15, 1])); on('vSide', 'click', () => look([1, .15, 0])); on('vTop', 'click', () => look([0, 1, .001]));
on('vGrid', 'click', () => { const g = $('#grid'); g.checked = !g.checked; g.dispatchEvent(new Event('change')); });
on('vCallouts', 'click', () => { const on = $('#vCallouts').getAttribute('aria-checked') !== 'true'; stage.showAnnos(on); $('#vCallouts').setAttribute('aria-checked', on); });
on('vPanel', 'click', () => { const closed = $('#panel').classList.toggle('closed'); $('#vPanel').setAttribute('aria-checked', !closed); });
on('vFull', 'click', () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen()));
on('vHelp', 'click', () => show('helpModal')); on('helpClose', 'click', () => show('helpModal', false));

// ---------- save and open projects in the user's own Google Drive ----------
let current = null, dirty = false, pendingAsNew = false, openOffset = 0; // current = {id, name} of the Drive project that is open
function renderSaveState() { $('#saveState').textContent = dirty ? 'Unsaved changes' : current ? `Saved · ${current.name}` : 'Not saved'; }
function setDirty(v) { dirty = v; $('#saveDot').hidden = !v; renderSaveState(); }
function markDirty() { if (!dirty && stage.meshesAll().length) setDirty(true); }
['history', 'tree', 'annos'].forEach(ev => stage.addEventListener(ev, markDirty));
$('#panel').addEventListener('input', markDirty); $('#panel').addEventListener('change', markDirty);
addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
function updateStatus() { if (me) $('#quota').textContent = `${me.shares_month}/${me.max_shares_month} links this month · ${me.projects_count}/${me.max_projects} projects`; }
function snapshot() { // small picture for the Open dialog
  const c = Object.assign(document.createElement('canvas'), { width: 220, height: 150 }), x = c.getContext('2d'), src = stage.renderer.domElement;
  x.fillStyle = '#0f172a'; x.fillRect(0, 0, 220, 150);
  const k = Math.min(220 / src.width, 150 / src.height); x.drawImage(src, (220 - src.width * k) / 2, (150 - src.height * k) / 2, src.width * k, src.height * k);
  return c.toDataURL('image/jpeg', .7);
}
function saveProject(asNew) {
  if (!me) return;
  if (!stage.meshesAll().length) return toast('Load a model first');
  if (!me.drive_connected) { show('driveModal'); return toast('Connect your Google Drive to save projects'); }
  if (current && !asNew) return doSave(current.name, false).then(err => err && toast(err));
  pendingAsNew = asNew; $('#saveName').value = current?.name || modelName || 'Untitled'; show('saveErr', false); show('saveModal'); $('#saveName').select();
}
async function doSave(name, asNew) { // returns an error message, or null when saved
  $('#saveState').textContent = 'Saving…';
  try {
    const glb = await stage.exportAs('glb');
    if (glb.size > me.max_mb * MB) throw new Error(`Model is over the ${me.max_mb} MB limit of the ${me.tier} tier`);
    const fd = new FormData();
    fd.append('model', glb, 'model.glb'); fd.append('config', JSON.stringify(stage.getConfig())); fd.append('name', name); fd.append('thumb', snapshot());
    if (current && !asNew) fd.append('project_id', current.id);
    const r = (await runJob('/api/drive-projects', fd, {  // compressing and uploading run in the background of the server: follow them in the status bar
      onSend: (a, b) => { $('#saveState').textContent = `Saving… sending ${Math.round(100 * a / b)}%`; },
      onServer: j => { $('#saveState').textContent = `Saving… ${Math.round(j.percent)}%`; $('#saveState').title = j.log.at(-1)?.[2] || ''; },
    })).result;
    $('#saveState').title = '';
    if (!current || asNew) me.projects_count++;
    current = { id: r.id, name: r.name }; modelName = r.name; setDirty(false); updateStatus(); toast('Saved to your Google Drive');
    return null;
  } catch (e) { renderSaveState(); return e.message; }
}
on('saveForm', 'submit', async e => {
  e.preventDefault(); const b = $('#saveGo'); b.disabled = true; show('saveErr', false);
  const err = await doSave(val('saveName').trim() || 'Untitled', pendingAsNew); b.disabled = false;
  if (err) { $('#saveErr').textContent = err; show('saveErr'); } else show('saveModal', false);
});
on('saveCancel', 'click', () => show('saveModal', false));
on('saveBtn', 'click', () => saveProject(false)); on('mSave', 'click', () => saveProject(false)); on('mSaveAs', 'click', () => saveProject(true));
async function openList(more) {
  const grid = $('#openGrid'); if (!more) { grid.replaceChildren(); openOffset = 0; }
  const r = await call(`/api/drive-projects?limit=24&offset=${openOffset}`);
  openOffset += r.items.length; me.projects_count = r.total; updateStatus();
  $('#openCount').textContent = `${r.total} of ${r.max} saved projects`;
  const btn = (txt, fn) => Object.assign(document.createElement('button'), { className: 'btn', textContent: txt, onclick: fn, style: 'padding:2px 8px;font-size:12px' });
  r.items.forEach(p => {
    const card = Object.assign(document.createElement('div'), { className: 'pcard' }), meta = Object.assign(document.createElement('div'), { className: 'meta' });
    const pic = p.thumb ? Object.assign(document.createElement('img'), { src: p.thumb, alt: '' }) : Object.assign(document.createElement('div'), { className: 'ph' });
    meta.append(Object.assign(document.createElement('b'), { textContent: p.name, className: 'truncate text-sm' }),
      Object.assign(document.createElement('span'), { className: 'text-xs text-slate-400', textContent: `${new Date(p.updated_at * 1000).toLocaleDateString()} · ${(p.size / MB).toFixed(1)} MB` }));
    const row = Object.assign(document.createElement('div'), { className: 'flex gap-1 pt-1' });
    row.append(btn('Open', () => openProject(p.id)),
      btn('Rename', async () => { const n = prompt('Project name', p.name); if (!n || !n.trim()) return; try { await call('/api/drive-projects/' + p.id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: n.trim() }) }); if (current?.id === p.id) { current.name = n.trim(); renderSaveState(); } openList(); } catch (e) { toast(e.message); } }),
      btn('Delete', async () => { if (!confirm(`Delete "${p.name}" from your Google Drive? This cannot be undone.`)) return; try { await call('/api/drive-projects/' + p.id, { method: 'DELETE' }); if (current?.id === p.id) { current = null; renderSaveState(); } openList(); } catch (e) { toast(e.message); } }));
    meta.append(row); card.append(pic, meta); grid.append(card);
  });
  show('openMore', openOffset < r.total); $('#openEmpty').textContent = r.total ? '' : 'No saved projects yet. Use File → Save to Google Drive.';
}
async function openProject(id) {
  if (dirty && !confirm('Discard your unsaved changes?')) return;
  try {
    const [meta, buf] = await Promise.all([call(`/api/drive-projects/${id}`),
      fetch(`/api/drive-projects/${id}/model`, { headers: { Authorization: 'Bearer ' + token } }).then(r => { if (!r.ok) throw new Error('Could not load the model from your Google Drive'); return r.arrayBuffer(); })]);
    editing = null; stage.assembly.clear(); stage.base.clear(); stage.select(null); stage.clearDims(); stage.annotations = []; stage.buildAnnos();
    await stage.loadSaved(buf); stage.applyConfig(meta.config); syncSliders();
    stage.history = []; stage.redoStack = []; stage.emit('history');
    current = { id: meta.id, name: meta.name }; modelName = meta.name; setDirty(false); show('openModal', false); toast('Project opened');
  } catch (e) { toast(e.message); }
}
on('mOpenDrive', 'click', () => { if (!me.drive_connected) { show('driveModal'); return toast('Connect your Google Drive first'); } show('openModal'); openList().catch(e => toast(e.message)); });
on('openClose', 'click', () => show('openModal', false)); on('openMore', 'click', () => openList(true).catch(e => toast(e.message)));

// outliner, tour, callout list, counters
mountOutliner(stage, $('#outliner'), true);
function renderAnnos() {
  const l = $('#annoList'); l.replaceChildren();
  stage.annotations.forEach((a, i) => {
    const row = Object.assign(document.createElement('div'), { className: 'row text-xs' });
    const t = Object.assign(document.createElement('button'), { className: 'btn flex-1 text-left truncate', textContent: `${i + 1}. ${a.text}`, onclick: () => stage.focusAnno(i) });
    row.append(t, Object.assign(document.createElement('button'), { className: 'btn', textContent: '✕', title: 'Delete callout', onclick: () => stage.removeAnno(i) })); l.append(row);
  });
}
stage.addEventListener('annos', renderAnnos);
stage.addEventListener('change', () => { $('#tris').textContent = stage.triCount().toLocaleString() + ' tris'; updateExplode(); });
stage.addEventListener('tree', updateExplode);

// ---------- each user's own Google Drive ----------
function driveUi() {
  $('#drive').textContent = me.drive_connected ? 'Drive ✓' : 'Connect Drive'; $('#drive').classList.toggle('btn-p', !me.drive_connected);
  $('#driveState').textContent = !me.drive_available ? 'Google Drive is not enabled on this site yet. Please ask the site owner.'
    : me.drive_connected ? 'Status: connected.' : 'Status: not connected.';
  $('#driveGo').textContent = me.drive_connected ? 'Disconnect' : 'Connect Google Drive';
  show('driveHelp', !me.drive_connected); show('driveWarn', me.drive_connected);
}
on('drive', 'click', () => show('driveModal'));
on('driveClose', 'click', () => show('driveModal', false));
on('driveGo', 'click', async () => {
  try {
    if (me.drive_connected) {
      if (!confirm('Disconnect Google Drive? Your existing share links will stop loading until you reconnect.')) return;
      await call('/api/drive', { method: 'DELETE' }); me.drive_connected = false; driveUi(); toast('Google Drive disconnected');
    } else location.href = (await call('/api/drive/connect', { method: 'POST' })).url;
  } catch (e) { toast(e.message); }
});
{
  if (new URLSearchParams(location.search).get('paid')) { history.replaceState(null, '', '/studio'); setTimeout(() => toast('Payment received. Your license unlocks within a minute: reload this page.'), 600); }
  const q = new URLSearchParams(location.search).get('drive');
  if (q) { history.replaceState(null, '', '/studio'); setTimeout(() => toast(q === 'connected' ? 'Google Drive connected' : 'Could not connect Google Drive, please try again'), 600); }
}

// ---------- export / share ----------
on('export', 'click', () => {
  if (!stage.meshesAll().length) return toast('Load a model first');
  document.querySelectorAll('#exportModal [data-fmt]').forEach(b => (b.disabled = !me.formats.includes(b.dataset.fmt)));
  $('#exNote').textContent = me.formats.length ? '' : 'Multi-format export needs the PRO tier (GLB) or ENTERPRISE (GLB/OBJ/STL/USDZ).';
  show('exportModal');
});
on('exClose', 'click', () => show('exportModal', false));
document.querySelectorAll('#exportModal [data-fmt]').forEach(b => b.addEventListener('click', async () => {
  try { download(await stage.exportAs(b.dataset.fmt), 'model.' + b.dataset.fmt); } catch (e) { toast('Export failed: ' + e.message); }
}));

let editing = null; // {id, title} while a published project is reopened for editing

// ---- sharing wizard: 6 steps with a moving timeline. Publishes 360 picture links (view-only: visitors only get pictures). ----
// Tier limits are enforced by the server too; this only shapes the form.
const STEPS = ['Share title', 'Download permissions', 'Export quality', 'Expiry', 'Processing', 'Publish'];
const PRESETS = {
  light: { az: 36, el: 3, px: 800, q: 78, elMin: 8, elMax: 55 },
  standard: { az: 72, el: 5, px: 1024, q: 80, elMin: 8, elMax: 58 },
  high: { az: 120, el: 7, px: 1400, q: 82, elMin: 5, elMax: 65 },
};
let modelName = '', step = 1, cancelPack = false; // modelName = name of the loaded file, the default share title
const setFill = frac => { $('#tlFill').style.width = `calc((100% - 26px) * ${frac})`; }; // the blue line slides (CSS transition)
function goStep(n, err) {
  step = n;
  document.querySelectorAll('#shareModal [data-step]').forEach(s => s.classList.toggle('hidden', +s.dataset.step !== n));
  $('#wizTitle').textContent = `Step ${n} of ${STEPS.length} · ${STEPS[n - 1]}`;
  document.querySelectorAll('#tl .tl-dot').forEach((d, i) => { d.classList.toggle('done', i < n - 1); d.classList.toggle('now', i === n - 1); d.textContent = i < n - 1 ? '✓' : i + 1; });
  setFill((n - 1) / (STEPS.length - 1));
  show('sBack', (n > 1 && n < 5) || (n === 5 && !!err)); show('sNext', n < 5); show('iCancel', n === 5 && !err);
  const left = me.max_shares_month - me.shares_month;
  const note = (n === 1 ? `${me.shares_month} of ${me.max_shares_month} new links used this month${left <= 0 ? ' (limit reached)' : ''}. ` : '') +
    (me.tier === 'FREE' ? { 1: 'Passwords need the PRO tier.', 2: 'Model downloads need the PRO tier.', 4: 'Link expiry needs the PRO tier.' }[n] || '' : '');
  $('#sNote').textContent = note || ''; show('sNote', !!note);
  show('sErr', !!err); if (err) $('#sErr').textContent = err;
}
on('share', 'click', () => {
  if (!stage.meshesAll().length) return toast('Load a model first');
  if (!me.drive_connected) { show('driveModal'); return toast('Connect your Google Drive to publish links'); }
  if (!editing && me.shares_month >= me.max_shares_month) return toast(`Monthly limit reached (${me.max_shares_month} new links on the ${me.tier} tier). It resets on the 1st.`);
  for (const id of ['pDl', 'sUsePw', 'sExpiry']) $('#' + id).disabled = !me.share_permissions;
  $('#pDl').checked = $('#sUsePw').checked = false; $('#sPass').value = ''; show('sPassRow', false); $('#sExpiry').value = '0';
  $('#sTitle').value = editing ? editing.title : ''; $('#sTitle').placeholder = modelName || 'Untitled';
  show('sUpdateRow', !!editing); if (editing) $('#sEditing').textContent = `(${editing.title})`;
  setupImages(); goStep(1); show('shareModal');
});
on('sUsePw', 'change', () => { const c = $('#sUsePw').checked; show('sPassRow', c); if (c) $('#sPass').focus(); });
function imgParams() {
  const lim = me.images, c = $('#iPreset').value === 'custom';
  const P = c ? { az: +val('iAz'), el: +val('iEl'), px: +val('iPx'), q: +val('iQ'), elMin: +val('iElMin'), elMax: +val('iElMax') } : PRESETS[$('#iPreset').value];
  const cl = (v, lo, hi) => Math.max(lo, Math.min(hi, Math.round(v) || lo));
  return { az: cl(P.az, 4, lim.max_az), el: cl(P.el, 1, lim.max_el), px: cl(P.px, 256, lim.max_px), q: cl(P.q, 40, 95),
           elMin: cl(P.elMin, -60, 85), elMax: cl(P.elMax, -60, 85) };
}
function imgInfo() {
  const P = imgParams(), mb = P.az * P.el * P.px * P.px * 0.1 * (P.q / 80) / 1e6;  // +10% for the small preview pictures
  $('#iInfo').textContent = `${P.az} × ${P.el} = ${P.az * P.el} pictures of ${P.px}px, about ${mb.toFixed(0)} MB (estimate). ` +
    (me.images.watermark === 'forced' ? 'Free links carry a watermark and a faint cross.' : '');
}
function setupImages() {
  const sel = $('#iPreset'); sel.replaceChildren(...me.images.presets.map(k => new Option(k[0].toUpperCase() + k.slice(1), k)));
  if (me.images.custom) sel.add(new Option('Custom (fine-tune)', 'custom'));
  show('iWmRow', me.images.watermark === 'custom'); show('iCustom', false); imgInfo();
}
on('iPreset', 'change', () => {
  const c = $('#iPreset').value === 'custom'; show('iCustom', c);
  if (c) { const P = PRESETS.standard; for (const [id, k] of [['iAz', 'az'], ['iEl', 'el'], ['iPx', 'px'], ['iQ', 'q'], ['iElMin', 'elMin'], ['iElMax', 'elMax']]) $('#' + id).value = P[k]; }
  imgInfo();
});
for (const id of ['iAz', 'iEl', 'iPx', 'iQ', 'iElMin', 'iElMax']) on(id, 'input', imgInfo);
on('iCancel', 'click', () => { cancelPack = true; currentXhr?.abort(); });
async function buildPack(P, onFrame) { // 'S3IM' + u32 index length + JSON index + the pictures, in one file
  const t = await stage.renderTurntable({ az: P.az, el: P.el, elMin: P.elMin, elMax: Math.max(P.elMin, P.elMax), px: P.px, quality: P.q / 100,
    cancelled: () => cancelPack, onProgress: onFrame });
  let off = 0; const place = list => list.map(b => { const f = [off, b.size]; off += b.size; return f; });
  const thumbs = place(t.thumbs), frames = place(t.blobs); // previews first: the viewer can start as soon as they have arrived
  const ix = new TextEncoder().encode(JSON.stringify({ v: 1, mime: t.mime, w: t.w, h: t.h, az: t.az, el: t.el, elevs: t.elevs, frames, thumbs, anno: stage.annotations.length ? t.anno : null }));
  const head = new Uint8Array(8); head.set([83, 51, 73, 77]); new DataView(head.buffer).setUint32(4, ix.length, true);
  const pack = new Blob([head, ix, ...t.thumbs, ...t.blobs]);
  if (pack.size > me.images.max_mb * MB) throw new Error(`The pictures are ${(pack.size / MB).toFixed(0)} MB, over the ${me.images.max_mb} MB limit of the ${me.tier} tier. Choose a lower quality.`);
  return pack;
}
on('sClose', 'click', () => { cancelPack = true; show('shareModal', false); });
on('sBack', 'click', () => goStep(step === 5 ? 4 : step - 1));
on('sNext', 'click', () => {
  if (step === 1 && $('#sUsePw').checked && val('sPass').length < 4) return goStep(1, 'The password needs at least 4 characters.');
  show('sErr', false);
  if (step === 4) { goStep(5); return publish(); }
  goStep(step + 1);
});
on('sCopy', 'click', () => navigator.clipboard?.writeText(val('sUrl')).then(() => toast('Link copied')));
if (navigator.share) { // phones: the system share sheet (WhatsApp, Mail, Messages...)
  $('#sNative').classList.remove('hidden');
  on('sNative', 'click', () => navigator.share({ title: val('sTitle') || modelName || 'Scic3DViewer', url: val('sUrl') }).catch(() => {}));
}
on('sCopyEmbed', 'click', () => navigator.clipboard?.writeText(val('sEmbed')).then(() => toast('Embed code copied')));
async function showQr(url) { // the QR library loads from the CDN only when a link is created
  const img = $('#sQr');
  try { const qr = (await import('qrcode-generator')).default(0, 'M'); qr.addData(url); qr.make(); img.src = qr.createDataURL(4); img.classList.remove('hidden'); } catch { img.classList.add('hidden'); }
}
// ---- the Processing step: a readable log, and the helpers that talk to the background jobs of the server ----
const fmtT = n => (n >= 60 ? `${Math.floor(n / 60)} min ${String(Math.round(n % 60)).padStart(2, '0')} s` : `${Math.round(n)} s`);
const clock = n => `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
const etaOf = (done, total, t0) => { const el = (performance.now() - t0) / 1000; return `${fmtT(el)} so far` + (done > 0 && el > 1 ? `, about ${fmtT(el * (total - done) / done)} left` : ''); };
let logT0 = 0, logLines = [], serverLines = [], currentXhr = null;
function renderLog() {
  const box = $('#pvLog'), stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 12;
  box.value = [...logLines, ...serverLines].map(l => l.text).join('\n'); if (stick) box.scrollTop = box.scrollHeight;
}
function say(text, key = '') { // a line with the same key replaces the previous one, so progress does not flood the log
  const line = { key, text: `[${clock((performance.now() - logT0) / 1000)}] ${text}` };
  if (key && logLines.at(-1)?.key === key) logLines[logLines.length - 1] = line; else logLines.push(line);
  renderLog();
}
function setBar(pct, headline) { $('#iBar').value = pct; $('#iPct').textContent = Math.round(pct) + '%'; setFill((4 + .96 * pct / 100) / (STEPS.length - 1)); if (headline) $('#pvStatus').textContent = headline; }
const friendly = st => (st === 413 ? 'The upload is too big for the connection to the server (about 100 MB per upload). Choose a lower quality.'
  : st === 524 || st === 504 || st === 502 ? 'The server took too long to answer. Please try again.' : `Request failed (${st})`);
function xhrJson(url, body, onProgress) { // XMLHttpRequest, because fetch cannot report how much of an upload has been sent
  return new Promise((resolve, reject) => {
    const x = currentXhr = new XMLHttpRequest(); x.open('POST', url); x.setRequestHeader('Authorization', 'Bearer ' + token);
    x.upload.onprogress = e => e.lengthComputable && onProgress?.(e.loaded, e.total);
    x.onload = () => {
      let j = {}; try { j = JSON.parse(x.responseText); } catch { /* not JSON: a proxy error page */ }
      if (x.status >= 200 && x.status < 300) resolve(j); else reject(new Error(typeof j.detail === 'string' ? j.detail : friendly(x.status)));
    };
    x.onerror = () => reject(new Error('The connection to the server was lost. Check your internet and try again.'));
    x.onabort = () => reject(new Error('Cancelled'));
    x.send(body);
  });
}
async function pollJob(jid, onUpdate) { // the server works in the background; ask it how far it is, about twice a second
  let failures = 0;
  for (;;) {
    await new Promise(r => setTimeout(r, 700));
    let j; try { j = await call(`/api/jobs/${jid}`); failures = 0; } catch (e) { if (++failures >= 6) throw new Error('Lost contact with the server while it was working. Check My shared links to see whether the link was created.'); continue; }
    onUpdate?.(j);
    if (j.done) { if (j.error) throw new Error(j.error); return j.result; }
    if (j.idle > 180) throw new Error('The server stopped working on this. Please try again.');
  }
}
async function runJob(url, fd, { onSend, onSent, onServer } = {}) { // POST (progress while sending), then follow the job until it is done
  const r = await xhrJson(url, fd, onSend); onSent?.(r);
  return { ...r, result: await pollJob(r.job, onServer) };
}
async function publish() { // step 5: draw the pictures (with a live preview), send them, let the server finish; step 6 shows the link
  const P = imgParams(), total = P.az * P.el; cancelPack = false; let prev = '';
  logT0 = performance.now(); logLines = []; serverLines = []; renderLog(); show('iCancel');
  $('#pvImg').removeAttribute('src'); $('#pvBox').style.background = BACKDROPS[stage.state.backdrop] || BACKDROPS.midnight; setBar(0, 'Starting…');
  const mark = me.images.watermark === 'forced' ? 'a watermark and a faint cross are added' : $('#iWm').value.trim() ? 'your watermark text is added' : 'no watermark';
  say(`Starting: ${total} pictures (${P.az} around × ${P.el} heights) at ${P.px} px; ${mark}.`);
  try {
    const fd = new FormData();
    if ($('#pDl').checked) { // the model is only stored in Drive when visitors may download it
      say('Preparing the 3D model for download…', 'model');
      const glb = await stage.exportAs('glb');
      if (glb.size > me.max_mb * MB) throw new Error(`Model is over the ${me.max_mb} MB limit of the ${me.tier} tier`);
      fd.append('model', glb, 'model.glb'); fd.append('allow_download', 'true'); say(`Model ready: ${(glb.size / MB).toFixed(1)} MB.`, 'model');
    }
    fd.append('title', val('sTitle').trim() || modelName || 'Untitled'); fd.append('config', JSON.stringify(stage.getConfig()));
    fd.append('expires_in_days', val('sExpiry')); fd.append('password', $('#sUsePw').checked ? val('sPass') : '');
    if (editing && $('#sUpdate').checked) fd.append('update_id', editing.id);
    fd.append('kind', 'images'); fd.append('watermark', val('iWm').trim());
    const t0 = performance.now();
    const pack = await buildPack(P, (f, blob) => {
      const i = Math.round(f * total); setBar(45 * f, `Drawing picture ${i} of ${total}…`);
      say(`Drawing pictures: ${i}/${total} (${Math.round(f * 100)}%) · ${etaOf(i, total, t0)}`, 'draw');
      const url = URL.createObjectURL(blob); $('#pvImg').src = url; if (prev) URL.revokeObjectURL(prev); prev = url;
    });
    say(`Drew all ${total} pictures and their previews in ${fmtT((performance.now() - t0) / 1000)}.`, 'draw');
    say(`Packed everything into one file: ${(pack.size / MB).toFixed(1)} MB.`, 'pack'); setBar(48, 'Packed. Sending…');
    fd.append('pack', pack, 'pack.s3im');
    const t1 = performance.now(); let base = 0;
    const r = await runJob('/api/projects/share', fd, {
      onSend: (a, b) => { setBar(50 + 8 * a / b, 'Sending to the server…'); say(`Sending to the server: ${(a / MB).toFixed(1)} / ${(b / MB).toFixed(1)} MB (${Math.round(100 * a / b)}%) · ${etaOf(a, b, t1)}`, 'send'); },
      onSent: () => { base = (performance.now() - logT0) / 1000; show('iCancel', false); say(`Sent in ${fmtT((performance.now() - t1) / 1000)}. The server now finishes the job in the background (watermark, upload to your Google Drive).`, 'sent'); },
      onServer: j => { // the lines the server wrote, with their times
        serverLines = j.log.map(([t, , text]) => ({ text: `[${clock(base + t)}] ${text}` })); renderLog();
        setBar(58 + 42 * j.percent / 100, j.log.at(-1)?.[2] || 'Working on the server…');
      },
    });
    const url = location.origin + r.result.url; $('#sUrl').value = url;
    $('#sEmbed').value = `<iframe src="${url}" width="640" height="480" style="border:0" allow="fullscreen" allowfullscreen></iframe>`;
    if (!editing || !$('#sUpdate').checked) me.shares_month++;
    updateStatus(); setBar(100, 'Done');
    goStep(6); showQr(url);
  } catch (e) { const m = e.message === 'Cancelled' ? 'Cancelled. Go back to change the settings.' : e.message; say('Failed: ' + m); goStep(5, m); }
  if (prev) URL.revokeObjectURL(prev);
}

// ---------- my projects ----------
async function editProject(id) {
  try {
    const [meta, buf] = await Promise.all([call(`/api/projects/${id}/edit`),
      fetch(`/api/projects/${id}/edit-model`, { headers: { Authorization: 'Bearer ' + token } }).then(r => { if (!r.ok) throw new Error('Could not load the model from your Google Drive'); return r.arrayBuffer(); })]);
    stage.assembly.clear(); stage.base.clear(); stage.select(null); stage.clearDims();
    await stage.loadSaved(buf); stage.applyConfig(meta.config); syncSliders();
    editing = { id, title: meta.title }; current = null; stage.history = []; stage.redoStack = []; stage.emit('history'); setDirty(false); show('projectsModal', false);
    toast('Opened for editing. Share link, then Publish, updates the same link.');
  } catch (e) { toast(e.message); }
}
async function projects() {
  const rows = await call('/api/projects'), now = Date.now() / 1000, body = $('#projRows');
  const b = (txt, fn) => Object.assign(document.createElement('button'), { className: 'btn', textContent: txt, onclick: fn, style: 'padding:2px 8px;font-size:12px;margin-right:4px' });
  const json = { 'Content-Type': 'application/json' };
  body.replaceChildren(...rows.map(p => {
    const dead = p.revoked ? 'revoked' : p.expires_at && p.expires_at < now ? 'expired' : '';
    const tr = document.createElement('tr'), td = () => tr.appendChild(document.createElement('td'));
    td().textContent = p.title || p.share_id;
    td().textContent = new Date(p.created_at * 1000).toLocaleDateString() + ' · ' + p.views + ' views' + (p.kind === 'images' ? ' · 360° pictures' : '') + (p.locked ? ' · password' : '') +
      (p.expires_at && !dead ? ' · expires ' + new Date(p.expires_at * 1000).toLocaleDateString() : '') + (dead ? ' · ' + dead.toUpperCase() : '');
    const cell = td(); cell.style.textAlign = 'right'; cell.style.whiteSpace = 'nowrap';
    const run = (fn, msg) => async () => { try { await fn(); if (msg) toast(msg); projects(); } catch (e) { toast(e.message); } };
    cell.append(
      b('Copy link', () => navigator.clipboard?.writeText(location.origin + '/v/' + p.share_id).then(() => toast('Link copied'))),
      b('Open', () => window.open('/v/' + p.share_id, '_blank', 'noopener')),
      Object.assign(b('Edit', () => editProject(p.share_id)), p.has_model ? {} : { disabled: true, title: 'Published without the model download, so it cannot be reopened' }),
      b(p.revoked ? 'Restore' : 'Revoke', run(() => call('/api/projects/' + p.share_id, { method: 'PATCH', headers: json, body: JSON.stringify({ revoked: !p.revoked }) }))),
      b('Expiry/password', run(async () => {
        const d = prompt('Expire after how many days? (0 = never)', '0'); if (d === null) return;
        const pw = prompt('Password for this link (leave empty to remove it, Cancel to keep it as is)');
        const patch = { expires_in_days: Math.max(0, parseInt(d, 10) || 0) }; if (pw !== null) patch.password = pw;
        await call('/api/projects/' + p.share_id, { method: 'PATCH', headers: json, body: JSON.stringify(patch) });
      }, 'Link updated')),
      b('Delete', run(async () => {
        if (!confirm('Delete this link and its file from your Google Drive? This cannot be undone.')) return;
        await call('/api/projects/' + p.share_id, { method: 'DELETE' }); if (editing?.id === p.share_id) editing = null;
      })));
    return tr;
  }));
  $('#projEmpty').textContent = rows.length ? '' : 'You have not published anything yet. Use "Share link" to create your first link.';
}
on('projects', 'click', () => { show('projectsModal'); projects().catch(e => toast(e.message)); });
on('projClose', 'click', () => show('projectsModal', false));

// ---------- upgrade (Stripe) ----------
on('upgrade', 'click', async () => {
  show('upgradeModal');
  try {
    const plans = await call('/api/billing/plans'), name = d => ({ '1m': '1 month', '3m': '3 months', '6m': '6 months', '1y': '1 year' }[d] || d);
    $('#planList').replaceChildren(...plans.map(p => Object.assign(document.createElement('button'), { className: 'btn btn-p', textContent: `${p.tier} · ${name(p.duration)}`,
      onclick: async () => { try { location.href = (await call('/api/billing/checkout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan: p.id }) })).url; } catch (e) { toast(e.message); } } })));
  } catch (e) { toast(e.message); }
});
on('upClose', 'click', () => show('upgradeModal', false));

// ---------- lighting ----------
on('lpreset', 'change', () => stage.setLightPreset(val('lpreset')));
on('shadows', 'change', e => stage.setShadows(e.target.checked));

// ---------- phones: dialogs become bottom sheets and the other windows get out of the way ----------
const modals = [...document.querySelectorAll('[id$="Modal"]')];
modals.forEach(m => {
  m.firstElementChild?.classList.add('modal-card'); m.setAttribute('role', 'dialog'); m.setAttribute('aria-modal', 'true');
  const h = m.querySelector('h2'); if (h) { h.id ||= m.id + 'Title'; m.setAttribute('aria-labelledby', h.id); }
});
const focusOpened = new MutationObserver(recs => recs.forEach(r => {
  const m = r.target, isOpen = !m.classList.contains('hidden');
  if (isOpen && !m._open) (m.querySelector('input:not([type=checkbox]):not([type=radio]),select,textarea') || m.querySelector('button,a'))?.focus();
  m._open = isOpen;
}));
modals.forEach(m => focusOpened.observe(m, { attributes: true, attributeFilter: ['class'] }));
addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  modals.filter(m => m.id !== 'authModal' && !m.classList.contains('hidden')).forEach(m => (m.id === 'shareModal' ? $('#sClose').click() : m.classList.add('hidden')));
});
new MutationObserver(() => document.body.classList.toggle('modal-open', modals.some(m => !m.classList.contains('hidden'))))
  .observe(document.body, { subtree: true, attributes: true, attributeFilter: ['class'] });
if (matchMedia('(max-width: 768px)').matches) $('#panel').classList.add('closed'); // start with the canvas, not the settings sheet

boot();
