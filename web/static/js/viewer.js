import { Stage } from '/static/js/stage.js';
import { $, toast, download, mountOutliner, mountTour } from '/static/js/ui.js';

const id = location.pathname.split('/').pop(), api = p => `/api/projects/${encodeURIComponent(id)}/${p}`;
const stage = window.stage = new Stage($('#stage'));
stage.selectable = false;
const fail = msg => { $('#err').textContent = msg; $('#err').classList.remove('hidden'); };
let pw = ''; // password of a protected link, sent as a header (never in the URL)
const get = p => fetch(api(p), { headers: pw ? { 'X-Share-Password': pw } : {} });

function askPassword(wrong) {
  return new Promise(res => {
    $('#pwErr').classList.toggle('hidden', !wrong); $('#pwModal').classList.remove('hidden'); $('#pwInput').value = ''; $('#pwInput').focus();
    $('#pwForm').onsubmit = e => { e.preventDefault(); $('#pwModal').classList.add('hidden'); res($('#pwInput').value); };
  });
}

(async () => {
  let r = await get('config');
  for (let wrong = false; r.status === 401; wrong = true) { pw = await askPassword(wrong); r = await get('config'); }
  if (!r.ok) return fail(r.status === 404 ? 'This link does not exist or was removed.' : r.status === 410 ? (await r.json()).detail : 'Could not load this project.');
  const p = await r.json(), cfg = p.config || {};
  document.title = $('#title').textContent = p.title;
  const m = await get('model');
  if (!m.ok) return fail(m.status === 502 ? 'This model is temporarily unavailable.' : 'Could not load the model.');
  await stage.loadSaved(await m.arrayBuffer());
  stage.applyConfig(cfg);
  stage.fitIfClipped();
  if (cfg.env?.preset === 'custom') {
    get('hdr').then(h => h.ok ? h.blob() : Promise.reject()).then(b => stage.setHdr(URL.createObjectURL(b), cfg.env.intensity)).catch(() => stage.setEnv('softbox', cfg.env.intensity));
  }

  mountOutliner(stage, $('#outliner'), false);
  mountTour(stage, $('#tour'), true, () => {});

  if (p.allow_explode) {
    $('#secExplode').classList.remove('hidden');
    const ex = $('#explode');
    if (stage.explodeUnits().length < 2) { ex.disabled = true; } else {
      ex.value = Math.round((cfg.explode || 0) * 100); stage.setExplode(ex.value / 100);
      ex.addEventListener('input', () => stage.setExplode(ex.value / 100));
    }
  }
  if (p.allow_measure) {
    $('#tools').classList.remove('hidden'); $('#secMeasure').classList.remove('hidden');
    document.querySelectorAll('#tools [data-mode]').forEach(b => b.addEventListener('click', () => {
      const t = b.dataset.mode; stage.setMode(t === 'select' ? 'select' : 'measure', t);
      document.querySelectorAll('#tools .btn').forEach(x => x.classList.toggle('on', x === b));
    }));
    const unit = () => stage.setUnit($('#unit').value, +$('#uscale').value || 1);
    $('#unit').value = cfg.units?.unit || 'units'; $('#uscale').value = cfg.units?.scale || 1;
    $('#unit').onchange = $('#uscale').oninput = unit; $('#clearDims').onclick = () => stage.clearDims();
  }
  if (p.allow_download && p.formats.length) {
    const box = $('#dl'), name = p.title.replace(/[^\w\- ]+/g, '').trim() || 'model';
    for (const f of p.formats) {
      const b = Object.assign(document.createElement('button'), { className: 'btn', textContent: '.' + f.toUpperCase() });
      b.onclick = async () => {
        try { download(f === 'glb' ? await (await get('model')).blob() : await stage.exportAs(f), `${name}.${f}`); } catch (e) { toast('Download failed'); }
      };
      box.append(b);
    }
    box.classList.remove('hidden');
  }
})().catch(e => fail('Could not load this project (' + e.message + ').'));

stage.addEventListener('toast', e => toast(e.detail));
// the parts panel starts closed (it is in the HTML) so the model is not covered, especially on a phone
const setPanel = open => { $('#panel').classList.toggle('closed', !open); $('#panelBtn').classList.toggle('on', open); };
$('#panelBtn').onclick = () => setPanel($('#panel').classList.contains('closed'));
$('#panelClose').onclick = () => setPanel(false);
$('#rotate').onclick = e => { stage.controls.autoRotate = !stage.controls.autoRotate; stage.controls.autoRotateSpeed = 2; e.currentTarget.classList.toggle('on', stage.controls.autoRotate); };
if (!document.fullscreenEnabled) $('#full').classList.add('hidden');
$('#full').onclick = () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen());
