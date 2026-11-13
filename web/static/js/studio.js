import * as THREE from 'three';
import { Stage, BACKDROPS } from '/static/js/stage.js';
import { $, toast, download, mountOutliner, mountTour } from '/static/js/ui.js';

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
  $('#tier').textContent = me.tier;
  $('#who').textContent = me.name + (me.expires_at ? ' · until ' + new Date(me.expires_at * 1000).toLocaleDateString() : '');
  stage.maxAnnotations = me.max_annotations; stage.gizmo = can('assembly');
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
  if (token) { try { me = await call('/api/me'); applyMe(); return; } catch { token = null; store.set('scic_token'); } }
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
  if (replace) { editing = null; stage.assembly.clear(); stage.annotations = []; stage.buildAnnos(); stage.select(null); stage.setBase(null); stage.clearDims(); }
  for (const f of files) {
    if (/\.(step|stp)$/i.test(f.name) && !need('cad_import')) continue;
    if (f.size > me.max_mb * MB) { toast(`${f.name} is over the ${me.max_mb} MB limit of the ${me.tier} tier`); continue; }
    try { await stage.loadFile(f); } catch (err) { toast(err.message); }
  }
  syncSliders();
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
function setActive(b) { document.querySelectorAll('#tools .btn').forEach(x => x.classList.toggle('on', x === b)); }
addEventListener('keydown', e => {
  if (/INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
  const g = { w: 'translate', e: 'rotate', r: 'scale' }[e.key.toLowerCase()];
  if (g && can('assembly')) { stage.setGizmoMode(g); setActive($(`[data-gizmo=${g}]`)); }
  if (e.key === 'Escape') { stage.setMode('select'); stage.unpin(); setActive($('[data-mode=select]')); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && can('paint_undo')) { e.preventDefault(); stage.undo(); }
});
stage.addEventListener('locked', () => toast(`${me.tier} tier allows ${me.max_annotations} callouts. Upgrade for unlimited.`));
stage.addEventListener('toast', e => toast(e.detail));
on('panelBtn', 'click', () => $('#panel').classList.toggle('closed'));

// ---------- panel controls ----------
const bd = $('#backdrop'); Object.keys(BACKDROPS).forEach(k => bd.add(new Option(k[0].toUpperCase() + k.slice(1), k)));
on('backdrop', 'change', () => stage.setBackdrop(val('backdrop')));
on('grid', 'change', e => (stage.grid.visible = e.target.checked));
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
on('undo', 'click', () => need('paint_undo') && stage.undo());
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
on('merge', 'click', () => { if (need('assembly')) { stage.mergeAll(); toast('Merged into one mesh (textures are baked to vertex colors)'); } });

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

on('share', 'click', () => {
  if (!stage.meshesAll().length) return toast('Load a model first');
  if (!me.drive_connected) { show('driveModal'); return toast('Connect your Google Drive to publish links'); }
  const perm = { pDl: me.share_permissions, pMeasure: can('measure'), pExplode: can('explode') };
  for (const [id, ok] of Object.entries(perm)) { $('#' + id).disabled = !ok; $('#' + id).checked = false; }
  for (const id of ['sExpiry', 'sPass']) $('#' + id).disabled = !me.share_permissions;
  $('#sPass').value = ''; $('#sExpiry').value = '0';
  show('sUpdateRow', !!editing); if (editing) { $('#sTitle').value = editing.title; $('#sEditing').textContent = `(${editing.title})`; }
  $('#sNote').textContent = me.tier === 'FREE' ? 'Free tier publishes view-only links, without expiry or password.' : ''; show('sNote', me.tier === 'FREE');
  show('sResult', false); show('sErr', false); show('shareModal');
});
on('sClose', 'click', () => show('shareModal', false));
on('sCopy', 'click', () => navigator.clipboard?.writeText(val('sUrl')).then(() => toast('Link copied')));
on('sCopyEmbed', 'click', () => navigator.clipboard?.writeText(val('sEmbed')).then(() => toast('Embed code copied')));
async function showQr(url) { // the QR library loads from the CDN only when a link is created
  const img = $('#sQr');
  try { const qr = (await import('qrcode-generator')).default(0, 'M'); qr.addData(url); qr.make(); img.src = qr.createDataURL(4); img.classList.remove('hidden'); } catch { img.classList.add('hidden'); }
}
on('sGo', 'click', async () => {
  const b = $('#sGo'); b.disabled = true; show('sErr', false);
  try {
    const glb = await stage.exportAs('glb');
    if (glb.size > me.max_mb * MB) throw new Error(`Model is over the ${me.max_mb} MB limit of the ${me.tier} tier`);
    const fd = new FormData();
    fd.append('model', glb, 'model.glb'); fd.append('title', val('sTitle').trim() || 'Untitled'); fd.append('config', JSON.stringify(stage.getConfig()));
    if ($('#pDl').checked) fd.append('allow_download', 'true');
    if ($('#pMeasure').checked) fd.append('allow_measure', 'true');
    if ($('#pExplode').checked) fd.append('allow_explode', 'true');
    fd.append('expires_in_days', val('sExpiry')); fd.append('password', val('sPass'));
    if (editing && $('#sUpdate').checked) fd.append('update_id', editing.id);
    if (stage.state.env === 'custom' && stage.hdrFile) fd.append('hdr', stage.hdrFile);
    const r = await call('/api/projects/share', { method: 'POST', body: fd });
    const url = location.origin + r.url; $('#sUrl').value = url;
    $('#sEmbed').value = `<iframe src="${url}" width="640" height="480" style="border:0" allow="fullscreen" allowfullscreen></iframe>`;
    show('sResult'); showQr(url);
  } catch (e) { $('#sErr').textContent = e.message; show('sErr'); }
  b.disabled = false;
});

// ---------- my projects ----------
async function editProject(id) {
  try {
    const [meta, buf] = await Promise.all([call(`/api/projects/${id}/edit`),
      fetch(`/api/projects/${id}/edit-model`, { headers: { Authorization: 'Bearer ' + token } }).then(r => { if (!r.ok) throw new Error('Could not load the model from your Google Drive'); return r.arrayBuffer(); })]);
    stage.assembly.clear(); stage.base.clear(); stage.select(null); stage.clearDims();
    await stage.loadSaved(buf); stage.applyConfig(meta.config); syncSliders();
    editing = { id, title: meta.title }; show('projectsModal', false);
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
    td().textContent = new Date(p.created_at * 1000).toLocaleDateString() + ' · ' + p.views + ' views' + (p.locked ? ' · password' : '') +
      (p.expires_at && !dead ? ' · expires ' + new Date(p.expires_at * 1000).toLocaleDateString() : '') + (dead ? ' · ' + dead.toUpperCase() : '');
    const cell = td(); cell.style.textAlign = 'right'; cell.style.whiteSpace = 'nowrap';
    const run = (fn, msg) => async () => { try { await fn(); if (msg) toast(msg); projects(); } catch (e) { toast(e.message); } };
    cell.append(
      b('Copy link', () => navigator.clipboard?.writeText(location.origin + '/v/' + p.share_id).then(() => toast('Link copied'))),
      b('Open', () => window.open('/v/' + p.share_id, '_blank', 'noopener')),
      b('Edit', () => editProject(p.share_id)),
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

boot();
