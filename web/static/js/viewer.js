// Share page bootstrap: password + link info, then ONLY the code that link needs (3D engine or the small image viewer).
import { $ } from '/static/js/ui.js';

const id = location.pathname.split('/').pop(), api = p => `/api/projects/${encodeURIComponent(id)}/${p}`;
const fail = msg => { $('#err').textContent = msg; $('#err').classList.remove('hidden'); };
let pw = ''; // password of a protected link, sent as a header (never in the URL)
const get = p => fetch(api(p), { headers: pw ? { 'X-Share-Password': pw } : {} });

function askPassword(wrong) {
  return new Promise(res => {
    $('#pwErr').classList.toggle('hidden', !wrong); $('#pwModal').classList.remove('hidden'); $('#pwInput').value = ''; $('#pwInput').focus();
    $('#pwForm').onsubmit = e => { e.preventDefault(); $('#pwModal').classList.add('hidden'); res($('#pwInput').value); };
  });
}

// burger menu: every option sits under the others, so nothing is pushed off a narrow screen
const menu = $('#menu'), menuBtn = $('#menuBtn');
const setMenu = open => { menu.classList.toggle('hidden', !open); menuBtn.setAttribute('aria-expanded', open); };
menuBtn.onclick = () => setMenu(menu.classList.contains('hidden'));
menu.addEventListener('click', e => { if (e.target.closest('button')) setMenu(false); });
document.addEventListener('pointerdown', e => { if (!menu.contains(e.target) && !menuBtn.contains(e.target)) setMenu(false); }, true);
addEventListener('keydown', e => { if (e.key === 'Escape') setMenu(false); });
if (!document.fullscreenEnabled) $('#full').classList.add('hidden');
$('#full').onclick = () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen());

(async () => {
  let r = await get('config');
  for (let wrong = false; r.status === 401; wrong = true) { pw = await askPassword(wrong); r = await get('config'); }
  if (!r.ok) return fail(r.status === 404 ? 'This link does not exist or was removed.' : r.status === 410 ? (await r.json()).detail : 'Could not load this project.');
  const p = await r.json();
  document.title = $('#title').textContent = p.title;
  document.body.classList.toggle('images', p.kind === 'images'); // hides the 3D-only buttons
  await (await import(p.kind === 'images' ? '/static/js/imgview.js' : '/static/js/viewer3d.js')).run(p, get, fail);
})().catch(e => fail('Could not load this project (' + e.message + ').'));
