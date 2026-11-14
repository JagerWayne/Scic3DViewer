import { $, toast } from '/static/js/ui.js';

let token = null;
try { token = localStorage.getItem('scic_token'); } catch { /* private mode */ }
// All user-supplied text goes through textContent (el), never innerHTML.
const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids.flat().filter(x => x != null)); return e; };
const date = ts => (ts ? new Date(ts * 1000).toLocaleDateString() : '—');
const when = ts => (ts ? new Date(ts * 1000).toLocaleString() : '—');
const pill = s => el('span', { className: 'pill p-' + s, textContent: s });
const btn = (txt, fn, cls = 'btn') => el('button', { className: cls, textContent: txt, onclick: fn, style: 'padding:2px 8px;font-size:12px;margin-right:4px' });
let cfg = {};

async function api(path, opt = {}) {
  const r = await fetch('/api/admin' + path, { ...opt, headers: { Authorization: 'Bearer ' + token, ...(opt.body ? { 'Content-Type': 'application/json' } : {}) } });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(typeof j.detail === 'string' ? j.detail : Array.isArray(j.detail) ? 'Please check the fields' : 'Error ' + r.status), { status: r.status });
  return j;
}
const send = (m, path, body) => api(path, { method: m, body: body ? JSON.stringify(body) : undefined });
const act = async (fn, msg) => { try { await fn(); if (msg) toast(msg); } catch (e) { toast(e.message); } };
const gate = html => { $('#gate').replaceChildren(...html); $('#gate').classList.remove('hidden'); $('#app').classList.add('hidden'); };

// ---------- overview ----------
async function overview() {
  const s = await api('/stats'); cfg = s.config;
  const card = (label, value) => el('div', { className: 'card' }, el('div', { className: 'text-xs text-slate-400', textContent: label }),
    el('div', { className: 'text-2xl font-bold', textContent: String(value) }));
  $('#cards').replaceChildren(card('People', s.people), card('Paid, active', s.paid_active), card('Waiting for first sign-in', s.pending),
    card('Expiring in 30 days', s.expiring_30d), card('Shared links', s.shares), card('Total views', s.views), card('Drives connected', s.drives), card('Sign-ins, 24h', s.logins_24h));
  const max = Math.max(1, ...s.daily.map(d => (d.login || 0) + (d.share || 0) + (d.update || 0) + (d.view || 0)));
  $('#chart').replaceChildren(...s.daily.map(d => {
    const seg = (n, c) => el('div', { className: 'seg', style: `height:${((n || 0) / max) * 100}px;background:${c}` });
    return el('div', { title: `${d.date}: ${d.login || 0} sign-ins, ${(d.share || 0) + (d.update || 0)} shares, ${d.view || 0} views` },
      seg(d.view, '#fbbf24'), seg((d.share || 0) + (d.update || 0), '#4ade80'), seg(d.login, '#38bdf8'), el('span', { className: 'text-[10px] text-slate-500', textContent: d.date.slice(5) }));
  }));
  if (!s.daily.length) $('#chart').replaceChildren(el('span', { className: 'text-sm text-slate-500', textContent: 'No activity yet' }));
  const row = (ok, label, hint) => el('div', {}, el('span', { className: 'dot', style: `background:${ok ? '#4ade80' : '#f87171'}` }), label, el('div', { className: 'text-xs text-slate-500', textContent: hint }));
  $('#system').replaceChildren(row(s.config.google, 'Google sign-in & Drive', s.config.google ? 'credentials.json found' : 'Missing credentials.json (see GOOGLE_DRIVE_SETUP.md)'),
    row(s.config.email, 'Email (SMTP)', s.config.email ? 'Configured' : 'Not configured: license emails are off'),
    row(s.config.stripe, 'Stripe payments', s.config.stripe ? 'Configured' : 'Not configured: upgrades are off'),
    el('div', { className: 'text-xs text-slate-400', textContent: 'Site address: ' + s.config.base_url }), el('div', { className: 'text-xs text-slate-400', textContent: 'Admin: ' + s.config.admin }));
  $('#fMailNote').textContent = s.config.email ? '' : '(email is not configured on the server)';
  $('#fMail').disabled = !s.config.email;
}

// ---------- licenses ----------
let lics = [];
async function licenses() { lics = await api('/licenses'); drawLicenses(); }
function drawLicenses() {
  const q = $('#lq').value.trim().toLowerCase(), st = $('#lstatus').value;
  const rows = lics.filter(l => (!st || l.status === st) && (!q || `${l.name || ''} ${l.email}`.toLowerCase().includes(q)));
  $('#lrows').replaceChildren(...rows.map(l => {
    const paid = l.managed, reload = () => licenses().then(overview);
    const actions = [btn('Edit', () => openEdit(l.id), paid ? 'btn btn-p' : 'btn'), !paid ? btn('Give license', () => openEdit(l.id, true), 'btn btn-p') : null,
      paid && l.status !== 'revoked' ? btn(l.paused_at ? 'Resume' : 'Pause', () => act(() => send('PATCH', '/licenses/' + l.id, { paused: !l.paused_at }).then(reload),
        l.paused_at ? 'Resumed: the paused time was added back' : 'Paused: Free tier for now, the clock is frozen')) : null,
      btn('Delete', () => deleteRow(l), 'btn btn-danger')];
    return el('tr', { ondblclick: () => openEdit(l.id) }, el('td', {}, l.name || '', l.note ? el('span', { title: l.note, className: 'ml-1', textContent: '📝' }) : null),
      el('td', { textContent: l.email }), el('td', { textContent: l.tier }), el('td', {}, pill(l.status)),
      el('td', { textContent: l.status === 'pending' ? `${l.duration_days} days after sign-in` : l.status === 'paused' ? `${date(l.expires_at)} (frozen)` : date(l.expires_at) }),
      el('td', { textContent: l.drive ? '✓' : '—' }), el('td', { textContent: String(l.shares) }), el('td', {}, ...actions));
  }));
  if (!rows.length) $('#lrows').replaceChildren(el('tr', {}, el('td', { colSpan: 8, className: 'text-slate-500', textContent: 'No licenses match.' })));
}

// ---------- edit dialog, delete ----------
let editing = null;
const ymd = ts => { const d = new Date(ts * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const refresh = async id => { await licenses(); overview(); const l = lics.find(x => x.id === id); if (l) fillEdit(l); };
async function purge(l) { // everything about one person: needs the email typed, the server refuses the admin account
  const typed = prompt(`This permanently deletes ${l.email} and ALL their data: licenses, links (and the files in their Google Drive), Drive login, sessions and activity log.\n\nType the email to confirm:`);
  if (typed === null) return false;
  if (typed.trim().toLowerCase() !== l.email.toLowerCase()) { toast('The email did not match: nothing was deleted'); return false; }
  await act(() => send('DELETE', `/licenses/${l.id}?purge=true`), 'Person deleted'); await licenses(); overview(); return true;
}
async function deleteRow(l) {
  if (!l.managed) return purge(l);
  if (!confirm(`Delete the license of ${l.name} (${l.email})?\n\nThey keep their account and links and go back to the Free tier.`)) return;
  await act(() => send('DELETE', '/licenses/' + l.id), 'License deleted'); await licenses(); overview();
}
function syncGive() { // a Free account becomes a license as soon as a tier and a length are chosen
  const l = editing; if (!l || l.managed) return;
  const give = $('#eTier').value !== 'FREE';
  for (const id of ['eExpWrap', 'eUnitWrap', 'eGiveNote']) $('#' + id).classList.toggle('hidden', !give);
}
function fillEdit(l) {
  editing = l; const paid = l.managed, activated = !!l.activated_at;
  $('#eTitle').textContent = paid ? 'Edit license' : 'Edit account (Free)'; $('#eStatus').replaceChildren(pill(l.status));
  $('#eName').value = l.name || ''; $('#eEmail').value = l.email; $('#eTier').value = l.tier; $('#eNote').value = l.note || '';
  $('#eStatusSel').value = l.revoked ? 'revoked' : l.paused_at ? 'paused' : 'active';
  for (const id of ['eEmail', 'eStatusSel']) $('#' + id).disabled = !paid;
  $('#eStatusWrap').classList.toggle('hidden', !paid); $('#eEmailNote').classList.toggle('hidden', !paid); $('#eDelLic').classList.toggle('hidden', !paid);
  $('#eExpWrap').classList.toggle('hidden', !paid); $('#eUnitWrap').classList.add('hidden'); $('#eGiveNote').classList.add('hidden');
  $('#eStartWrap').classList.toggle('hidden', !(paid && !activated)); $('#eStartNow').checked = false;
  $('#eExp').classList.toggle('hidden', !activated); $('#eDur').classList.toggle('hidden', activated);
  $('#eExpLabel').textContent = !paid ? 'License length' : activated ? 'Expires on' : 'Duration after the first sign-in (days)';
  if (activated) $('#eExp').value = l.expires_at ? ymd(l.expires_at) : '';
  else { $('#eDur').value = l.duration_days || 1; $('#eUnit').value = paid ? '1' : '365'; }
  const quick = (label, body, msg) => btn(label, () => act(async () => { await send('PATCH', '/licenses/' + l.id, body); await refresh(l.id); }, msg));
  $('#eQuick').replaceChildren(...(paid ? [quick('+30 days', { extend_days: 30 }, 'Extended by 30 days'), quick('+1 year', { extend_days: 365 }, 'Extended by 1 year'),
    l.status !== 'revoked' ? quick(l.paused_at ? 'Resume' : 'Pause', { paused: !l.paused_at }, l.paused_at ? 'Resumed: the paused time was added back' : 'Paused: Free tier for now, the clock is frozen') : null,
    quick(l.revoked ? 'Restore' : 'Revoke', { revoked: !l.revoked }, l.revoked ? 'Restored' : 'Revoked: blocked for good'),
    cfg.email ? btn('Email license', () => act(() => send('POST', `/licenses/${l.id}/email`), 'Email sent')) : null].filter(Boolean) : []));
  syncGive();
}
function openEdit(id, give) {
  const l = lics.find(x => x.id === id); if (!l) return;
  fillEdit(l);
  if (give && !l.managed) { $('#eTier').value = 'PRO'; syncGive(); $('#eDur').focus(); }  // "Give license": Pro for 1 year to start with
  $('#eConfirm').value = ''; $('#eDelPerson').disabled = true; $('#eErr').classList.add('hidden'); $('#editModal').classList.remove('hidden');
}
$('#eTier').addEventListener('change', syncGive);
$('#eClose').addEventListener('click', () => $('#editModal').classList.add('hidden'));
$('#eConfirm').addEventListener('input', () => ($('#eDelPerson').disabled = $('#eConfirm').value.trim().toLowerCase() !== editing.email.toLowerCase()));
$('#eDelPerson').addEventListener('click', async () => { if (await purge(editing)) $('#editModal').classList.add('hidden'); });
$('#eDelLic').addEventListener('click', async () => {
  const l = editing; await deleteRow(l); if (!lics.some(x => x.id === l.id)) $('#editModal').classList.add('hidden');
});
$('#editForm').addEventListener('submit', async e => {
  e.preventDefault(); $('#eErr').classList.add('hidden');
  const l = editing, paid = l.managed, body = {}, put = (k, v, old) => { if (v !== (old ?? '')) body[k] = v; };
  put('name', $('#eName').value.trim(), l.name); put('note', $('#eNote').value.trim(), l.note);
  if (paid) {
    put('email', $('#eEmail').value.trim().toLowerCase(), l.email); put('tier', $('#eTier').value, l.tier);
    const sel = $('#eStatusSel').value;  // Active / Paused / Revoked: each can be switched to any other
    if ((sel === 'revoked') !== !!l.revoked) body.revoked = sel === 'revoked';
    if ((sel === 'paused') !== !!l.paused_at) body.paused = sel === 'paused';
    if (l.activated_at) { if ($('#eExp').value && $('#eExp').value !== ymd(l.expires_at)) body.expires_on = $('#eExp').value; }
    else {
      if (+$('#eDur').value !== l.duration_days) body.duration_days = +$('#eDur').value;
      if ($('#eStartNow').checked) body.start_now = true;
    }
  } else if ($('#eTier').value !== 'FREE') {  // giving a license to a Free account: tier + length, it starts right away
    body.tier = $('#eTier').value; body.duration_days = Math.max(1, Math.round(+$('#eDur').value * +$('#eUnit').value));
  }
  if (!Object.keys(body).length) return toast('Nothing changed');
  try { await send('PATCH', '/licenses/' + l.id, body); await refresh(l.id); toast('Saved'); }
  catch (err) { $('#eErr').textContent = err.message; $('#eErr').classList.remove('hidden'); }
});

// ---------- shared links ----------
async function shares() {
  const rows = await api('/shares'), now = Date.now() / 1000;
  const reload = () => shares().then(overview);
  $('#srows').replaceChildren(...rows.map(s => {
    const status = s.revoked ? 'revoked' : s.expires_at && s.expires_at < now ? 'expired' : 'active';
    return el('tr', {}, el('td', {}, el('a', { href: '/v/' + s.share_id, target: '_blank', rel: 'noopener', className: 'text-sky-400 underline', textContent: s.title || s.share_id })),
      el('td', { textContent: s.email || '—' }), el('td', { textContent: when(s.created_at) }), el('td', { textContent: String(s.views) }),
      el('td', {}, pill(status), s.locked ? el('span', { className: 'text-xs text-slate-400 ml-1', textContent: '🔒 password' }) : null),
      el('td', {}, btn(s.revoked ? 'Restore' : 'Revoke', () => act(() => send('PATCH', '/shares/' + s.share_id, { revoked: !s.revoked }).then(reload))),
        btn('Delete', () => confirm('Delete this link and its file from the owner\'s Drive? This cannot be undone.') && act(() => send('DELETE', '/shares/' + s.share_id).then(reload), 'Deleted'))));
  }));
  if (!rows.length) $('#srows').replaceChildren(el('tr', {}, el('td', { colSpan: 6, className: 'text-slate-500', textContent: 'No shared links yet.' })));
}

// ---------- activity ----------
async function activity() {
  const rows = await api('/activity?limit=300&q=' + encodeURIComponent($('#aq').value));
  $('#arows').replaceChildren(...rows.map(a => el('tr', {}, el('td', { textContent: when(a.ts) }), el('td', { textContent: a.action }), el('td', { textContent: a.email || '—' }), el('td', { textContent: a.ip || '' }), el('td', { className: 'text-slate-400', textContent: a.detail || '' }))));
  if (!rows.length) $('#arows').replaceChildren(el('tr', {}, el('td', { colSpan: 5, className: 'text-slate-500', textContent: 'Nothing recorded.' })));
}

// ---------- wiring ----------
const loaders = { overview, licenses, shares, activity };
function open(tab) {
  document.querySelectorAll('#tabs .tab').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  Object.keys(loaders).forEach(t => $('#t-' + t).classList.toggle('hidden', t !== tab));
  act(loaders[tab]);
}
document.querySelectorAll('#tabs .tab').forEach(b => b.addEventListener('click', () => open(b.dataset.tab)));
$('#lq').addEventListener('input', drawLicenses); $('#lstatus').addEventListener('change', drawLicenses);
let t; $('#aq').addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => act(activity), 300); });
$('#aref').addEventListener('click', () => act(activity));
$('#newLic').addEventListener('click', () => { $('#fErr').classList.add('hidden'); $('#fOk').classList.add('hidden'); $('#licModal').classList.remove('hidden'); });
$('#fClose').addEventListener('click', () => $('#licModal').classList.add('hidden'));
$('#licForm').addEventListener('submit', async e => {
  e.preventDefault(); $('#fErr').classList.add('hidden'); $('#fOk').classList.add('hidden');
  try {
    const r = await send('POST', '/licenses', { name: $('#fName').value, email: $('#fEmail').value, tier: $('#fTier').value,
      duration: $('#fDur').value + $('#fUnit').value, send_email: $('#fMail').checked });
    $('#fOk').textContent = `License created for ${$('#fEmail').value}. ` + (r.emailed === true ? 'Email sent. ' : r.emailed === false ? 'Email failed: ' + r.email_error + '. ' : '') + 'They just sign in with that Google account.';
    $('#fOk').classList.remove('hidden'); $('#licForm').reset(); licenses().then(overview);
  } catch (err) { $('#fErr').textContent = err.message; $('#fErr').classList.remove('hidden'); }
});

(async () => {
  if (!token) return gate([el('p', { textContent: 'Please sign in first.' }), el('a', { className: 'btn btn-p', href: '/studio', textContent: 'Go to the Studio to sign in with Google' })]);
  try {
    const me = await (await fetch('/api/me', { headers: { Authorization: 'Bearer ' + token } })).json(); $('#me').textContent = me.email || '';
    await overview(); $('#app').classList.remove('hidden');
  } catch (e) { gate([el('p', { textContent: e.status === 403 ? 'This Google account is not the site admin.' : 'Your session expired. Please sign in again.' }), el('a', { className: 'btn btn-p', href: '/studio', textContent: 'Back to the Studio' })]); }
})();
