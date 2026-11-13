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
  const rows = lics.filter(l => (!st || l.status === st) && (!q || `${l.name} ${l.surname || ''} ${l.email}`.toLowerCase().includes(q)));
  $('#lrows').replaceChildren(...rows.map(l => {
    const paid = l.surname !== null, reload = () => licenses().then(overview);
    const tier = paid ? el('select', { className: 'inp', style: 'width:130px;padding:2px 6px', onchange: e => act(() => send('PATCH', '/licenses/' + l.id, { tier: e.target.value }).then(reload), 'Tier updated') },
      ...['FREE', 'PRO', 'ENTERPRISE'].map(t => el('option', { value: t, textContent: t, selected: t === l.tier }))) : el('span', { textContent: l.tier });
    const actions = paid ? [btn(l.revoked ? 'Restore' : 'Revoke', () => act(() => send('PATCH', '/licenses/' + l.id, { revoked: !l.revoked }).then(reload), l.revoked ? 'Restored' : 'Revoked')),
      btn('+30d', () => act(() => send('PATCH', '/licenses/' + l.id, { extend_days: 30 }).then(reload), 'Extended by 30 days')),
      btn('+1y', () => act(() => send('PATCH', '/licenses/' + l.id, { extend_days: 365 }).then(reload), 'Extended by 1 year')),
      cfg.email ? btn('Email', () => act(() => send('POST', `/licenses/${l.id}/email`), 'Email sent')) : null] : [el('span', { className: 'text-xs text-slate-500', textContent: 'automatic' })];
    return el('tr', {}, el('td', { textContent: [l.name, l.surname].filter(Boolean).join(' ') }), el('td', { textContent: l.email }), el('td', {}, tier), el('td', {}, pill(l.status)),
      el('td', { textContent: l.status === 'pending' ? `${l.duration_days} days after sign-in` : date(l.expires_at) }),
      el('td', { textContent: l.drive ? '✓' : '—' }), el('td', { textContent: String(l.shares) }), el('td', {}, ...actions));
  }));
  if (!rows.length) $('#lrows').replaceChildren(el('tr', {}, el('td', { colSpan: 8, className: 'text-slate-500', textContent: 'No licenses match.' })));
}

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
    const r = await send('POST', '/licenses', { name: $('#fName').value, surname: $('#fSurname').value, email: $('#fEmail').value, tier: $('#fTier').value,
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
