// 360-degree image viewer for view-only links. No Three.js, no WebGL and no 3D data: the visitor only receives pictures,
// and the picture closest to the dragged angle is shown. Pack layout: 'S3IM' + u32 index length + JSON index + frames.
import { $, toast, download } from '/static/js/ui.js';
import { BACKDROPS } from '/static/js/backdrops.js';

const CACHE = 64; // decoded frames kept in memory (a full pack decoded at once would need gigabytes)

export async function run(p, get, fail) {
  const cfg = p.config || {}, wm = p.watermark, annos = cfg.annotations || [];
  const box = $('#stage'); box.style.background = BACKDROPS[cfg.backdrop] || BACKDROPS.midnight;
  const note = Object.assign(document.createElement('div'), { className: 'absolute inset-0 flex items-center justify-center text-sm text-slate-300 pointer-events-none', textContent: 'Loading…' });
  box.append(note);

  const r = await get('images');
  if (!r.ok) return fail(r.status === 502 ? 'These pictures are temporarily unavailable.' : 'Could not load the pictures.');
  const parts = []; let got = 0; const rd = r.body.getReader();
  for (;;) { const { done, value } = await rd.read(); if (done) break; parts.push(value); got += value.length; note.textContent = `Loading… ${(got / 1e6).toFixed(1)} MB`; }
  const pack = new Blob(parts);
  const n = new DataView(await pack.slice(0, 8).arrayBuffer()).getUint32(4, true);
  const ix = JSON.parse(await pack.slice(8, 8 + n).text()), base = 8 + n, { az, el, w, h } = ix;
  note.remove();

  const cv = Object.assign(document.createElement('canvas'), { tabIndex: 0 }); cv.style.cssText = 'cursor:grab;touch-action:none;width:100%;height:100%'; // a canvas does not stretch with inset:0 alone
  cv.setAttribute('aria-label', '360 degree view, drag or use the arrow keys to rotate');
  const labels = Object.assign(document.createElement('div'), { className: 'labels' });
  box.append(cv, labels);
  const ctx = cv.getContext('2d');
  const chips = annos.map((a, i) => { const e = Object.assign(document.createElement('div'), { className: 'lbl callout', textContent: `${i + 1}. ${a.text}` }); e.onclick = () => showAnno(i); labels.append(e); return e; });

  // start on the angle the creator was looking from
  const cam = cfg.camera; let A = 0, E = (el - 1) / 2;
  if (cam) {
    const dx = cam.pos[0] - cam.target[0], dy = cam.pos[1] - cam.target[1], dz = cam.pos[2] - cam.target[2];
    A = (Math.atan2(dx, dz) / (Math.PI * 2) * az + az) % az;
    const deg = Math.asin(dy / (Math.hypot(dx, dy, dz) || 1)) * 180 / Math.PI, ev = ix.elevs || [];
    E = ev.length > 1 ? Math.max(0, Math.min(el - 1, ev.reduce((b, v, k) => (Math.abs(v - deg) < Math.abs(ev[b] - deg) ? k : b), 0))) : 0;
  }
  const frameAt = () => ((Math.round(A) % az) + az) % az + Math.max(0, Math.min(el - 1, Math.round(E))) * az;

  // decoded frames, least recently used dropped first
  const cache = new Map(), pending = new Set(); let last = null, dirty = true;
  const want = i => {
    if (cache.has(i)) { const b = cache.get(i); cache.delete(i); cache.set(i, b); return b; }
    if (!pending.has(i) && i >= 0 && i < az * el) {
      pending.add(i); const [o, len] = ix.frames[i];
      createImageBitmap(pack.slice(base + o, base + o + len, ix.mime)).then(b => {
        cache.set(i, b); pending.delete(i); dirty = true;
        while (cache.size > CACHE) { const k = cache.keys().next().value; cache.get(k).close?.(); cache.delete(k); }
      }).catch(() => pending.delete(i));
    }
    return null;
  };

  let zoom = 1, px = 0, py = 0; // zoom about the centre (wheel or pinch), pan while zoomed
  const fit = () => Math.min(cv.width / w, cv.height / h) * zoom;
  const drawMark = () => {
    if (!wm) return;
    const W = cv.width, H = cv.height, fs = Math.max(13, Math.round(Math.min(W, H) / 24));
    ctx.save(); ctx.font = `700 ${fs}px system-ui,sans-serif`; ctx.textBaseline = 'middle';
    if (wm.cross) { ctx.strokeStyle = 'rgba(255,255,255,.2)'; ctx.lineWidth = Math.max(1, fs / 12); ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(W, H); ctx.moveTo(W, 0); ctx.lineTo(0, H); ctx.stroke(); }
    if (wm.tile) {
      ctx.fillStyle = 'rgba(255,255,255,.2)'; ctx.shadowColor = 'rgba(0,0,0,.35)'; ctx.shadowBlur = 3; ctx.textAlign = 'center';
      ctx.translate(W / 2, H / 2); ctx.rotate(-Math.PI / 7); const step = fs * 4, span = Math.hypot(W, H);
      for (let y = -span; y < span; y += step) for (let x = -span; x < span; x += ctx.measureText(wm.text).width + fs * 3) ctx.fillText(wm.text, x, y);
    } else if (wm.text) {
      ctx.fillStyle = 'rgba(255,255,255,.8)'; ctx.shadowColor = 'rgba(0,0,0,.6)'; ctx.shadowBlur = 4; ctx.textAlign = 'right'; ctx.fillText(wm.text, W - fs, H - fs);
    }
    ctx.restore();
  };
  function draw() {
    const i = frameAt(), b = want(i) || last;
    if (b) last = b;
    for (const d of [1, -1, 2, -2]) want((((i % az) + d + az) % az) + Math.floor(i / az) * az); // neighbours around the ring
    if (frameAt() >= az) want(i - az); if (frameAt() < az * (el - 1)) want(i + az);
    ctx.clearRect(0, 0, cv.width, cv.height);
    const s = fit(), ox = (cv.width - w * s) / 2 + px, oy = (cv.height - h * s) / 2 + py;
    if (last) ctx.drawImage(last, ox, oy, w * s, h * s);
    drawMark();
    const k = cv.clientWidth / cv.width, fa = ix.anno?.[i] || [];
    chips.forEach((c, j) => {
      const a = fa[j]; c.hidden = !a || !a[2] || !b;
      if (a) c.style.transform = `translate(${(ox + a[0] * w * s) * k}px,${(oy + a[1] * h * s) * k}px) translate(-50%,-130%)`;
    });
  }

  // input: drag (mouse or finger) rotates, wheel or pinch zooms, arrow keys step
  const ptrs = new Map(); let vA = 0, vE = 0, auto = false, pinch = 0, anim = null;
  const clampZoom = () => { zoom = Math.max(1, Math.min(3, zoom)); if (zoom === 1) px = py = 0; dirty = true; };
  cv.addEventListener('pointerdown', e => { cv.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, [e.clientX, e.clientY]); vA = vE = 0; anim = null; cv.style.cursor = 'grabbing'; pinch = 0; });
  cv.addEventListener('pointermove', e => {
    const o = ptrs.get(e.pointerId); if (!o) return;
    const dx = e.clientX - o[0], dy = e.clientY - o[1]; ptrs.set(e.pointerId, [e.clientX, e.clientY]);
    if (ptrs.size === 2) {
      const [a, c] = [...ptrs.values()], d = Math.hypot(a[0] - c[0], a[1] - c[1]); if (pinch) { zoom *= d / pinch; clampZoom(); } pinch = d; return;
    }
    if (zoom > 1.01) { px += dx * devicePixelRatio; py += dy * devicePixelRatio; dirty = true; return; }
    vA = -dx * az * .75 / cv.clientWidth; vE = dy * (el - 1) * 1.2 / cv.clientHeight; A += vA; E = Math.max(0, Math.min(el - 1, E + vE)); dirty = true;
  });
  const up = e => { ptrs.delete(e.pointerId); pinch = 0; cv.style.cursor = 'grab'; };
  cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
  cv.addEventListener('wheel', e => { e.preventDefault(); zoom *= e.deltaY < 0 ? 1.1 : 1 / 1.1; clampZoom(); }, { passive: false });
  cv.addEventListener('keydown', e => {
    const m = { ArrowLeft: [1, 0], ArrowRight: [-1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key]; if (!m) return;
    e.preventDefault(); A += m[0]; E = Math.max(0, Math.min(el - 1, E + m[1])); dirty = true;
  });
  $('#rotate').onclick = e => { auto = !auto; e.currentTarget.classList.toggle('on', auto); };

  // callout click: turn to the picture where that callout faces the camera, closest to the centre
  function showAnno(i) {
    let best = -1, bs = -1;
    (ix.anno || []).forEach((fa, f) => { const a = fa[i]; if (a && a[2]) { const s = 1 - Math.abs(a[0] - .5) - Math.abs(a[1] - .5) * .5; if (s > bs) { bs = s; best = f; } } });
    if (best < 0) return toast('This point is not visible from the pictured angles');
    let dA = (best % az) - (((A % az) + az) % az); if (dA > az / 2) dA -= az; if (dA < -az / 2) dA += az;
    anim = { t0: performance.now(), A0: A, E0: E, dA, dE: Math.floor(best / az) - E };
  }

  const fitCanvas = () => { cv.width = Math.round(cv.clientWidth * devicePixelRatio) || 1; cv.height = Math.round(cv.clientHeight * devicePixelRatio) || 1; dirty = true; };
  new ResizeObserver(fitCanvas).observe(box); fitCanvas();
  let t = performance.now();
  (function loop(now) {
    const dt = Math.min(.05, (now - t) / 1000); t = now;
    if (anim) { const k = Math.min(1, (now - anim.t0) / 700), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2; A = anim.A0 + anim.dA * e; E = anim.E0 + anim.dE * e; if (k >= 1) anim = null; dirty = true; }
    else if (!ptrs.size && (Math.abs(vA) > .002 || Math.abs(vE) > .002)) { A += vA; E = Math.max(0, Math.min(el - 1, E + vE)); vA *= .93; vE *= .8; dirty = true; } // inertia after release
    if (auto && !ptrs.size) { A += az * dt / 12; dirty = true; }
    if (dirty) { dirty = false; draw(); }
    requestAnimationFrame(loop);
  })(t);

  if (p.allow_download && p.formats.length) {
    const dl = $('#dl'), name = p.title.replace(/[^\w\- ]+/g, '').trim() || 'model';
    for (const f of p.formats) {
      const b = Object.assign(document.createElement('button'), { className: 'btn', textContent: '.' + f.toUpperCase() });
      b.onclick = async () => { try { const m = await get('model'); if (!m.ok) throw 0; download(await m.blob(), `${name}.${f}`); } catch { toast('Download failed'); } };
      dl.append(b);
    }
    dl.classList.remove('hidden');
  }
}
