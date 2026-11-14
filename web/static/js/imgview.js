// 360-degree image viewer for view-only links. No Three.js, no WebGL and no 3D data: the visitor only receives pictures,
// and the picture closest to the dragged angle is shown. Pack layout: 'S3IM' + u32 index length + JSON index + pictures
// (the small 320 px previews first, then the full pictures).
import { $, toast, download } from '/static/js/ui.js';
import { BACKDROPS } from '/static/js/backdrops.js';

const CACHE = 64; // decoded pictures kept in memory for packs without previews
const calm = matchMedia('(prefers-reduced-motion: reduce)').matches; // no inertia, easing or auto-spin for people who asked for less motion

export async function run(p, get, fail) {
  const cfg = p.config || {}, wm = p.watermark, annos = cfg.annotations || [];
  const box = $('#stage'); box.style.background = BACKDROPS[cfg.backdrop] || BACKDROPS.midnight;
  const note = Object.assign(document.createElement('div'), { className: 'absolute inset-0 flex items-center justify-center text-sm text-slate-300 pointer-events-none', textContent: 'Loading…', role: 'status' });
  box.append(note);

  const r = await get('images');
  if (!r.ok) return fail(r.status === 502 ? 'These pictures are temporarily unavailable.' : 'Could not load the pictures.');
  // Start as soon as the index and all the small previews have arrived; the full pictures keep loading in the background.
  const rd = r.body.getReader(), parts = []; let got = 0, ix = null, base = 0, early = Infinity, done = false;
  const readMore = async () => { const x = await rd.read(); if (x.value) { parts.push(x.value); got += x.value.length; } return x.done; };
  while (!done) {
    done = await readMore(); note.textContent = `Loading… ${(got / 1e6).toFixed(1)} MB`;
    if (!ix && got >= 8) {
      const b = new Blob(parts), n = new DataView(await b.slice(0, 8).arrayBuffer()).getUint32(4, true);
      if (got >= 8 + n) {
        ix = JSON.parse(await b.slice(8, 8 + n).text()); base = 8 + n;
        const th = ix.thumbs, fr = ix.frames;
        if (Array.isArray(th) && th.length === ix.az * ix.el && Math.max(...th.map(([o, l]) => o + l)) <= Math.min(...fr.map(([o]) => o))) early = base + Math.max(...th.map(([o, l]) => o + l));
      }
    }
    if (ix && got >= early) break;
  }
  if (!ix) return fail('Could not read the pictures.');
  const { az, el, w, h } = ix;
  let pack = new Blob(parts), complete = done;
  note.remove();

  const cv = Object.assign(document.createElement('canvas'), { tabIndex: 0 }); cv.style.cssText = 'cursor:grab;touch-action:none;width:100%;height:100%'; // a canvas does not stretch with inset:0 alone
  cv.setAttribute('role', 'application'); cv.setAttribute('aria-label', `${p.title}: 360 degree view. Drag or use the arrow keys to rotate, plus and minus to zoom.`);
  const labels = Object.assign(document.createElement('div'), { className: 'labels' });
  box.append(cv, labels);
  const ctx = cv.getContext('2d');
  const chips = annos.map((a, i) => { const e = Object.assign(document.createElement('button'), { className: 'lbl callout', type: 'button', textContent: `${i + 1}. ${a.text}`, title: 'Turn to this callout' }); e.onclick = () => { stopTour(); showAnno(i); }; labels.append(e); return e; });
  const badge = Object.assign(document.createElement('div'), { className: 'fixed left-3 bottom-3 z-10 text-xs text-slate-300 glass rounded-lg px-2 py-1', role: 'status' });
  if (!complete) { badge.textContent = 'Loading full quality…'; document.body.append(badge); (async () => { while (!(await readMore())) badge.textContent = `Loading full quality… ${(got / 1e6).toFixed(1)} MB`; pack = new Blob(parts); complete = true; fired = false; dirty = true; badge.remove(); })(); }

  const SEEN = .25; // a tag counts as visible when its surface faces the camera at least this much (old packs store 0 or 1)
  let calloutsOn = true, tourBar = null, tourI = -1, tourTimer = null;
  const stopTour = () => { clearInterval(tourTimer); tourTimer = null; if (tourBar) tourBar.querySelector('[data-play]').textContent = '▶ Tour'; };
  if (annos.length) { // viewers can hide the tags, and step or play through them
    const tb = Object.assign(document.createElement('button'), { className: 'btn on', textContent: 'Callouts', title: 'Show or hide the callout tags' });
    tb.setAttribute('aria-pressed', 'true');
    tb.onclick = () => { calloutsOn = !calloutsOn; tb.classList.toggle('on', calloutsOn); tb.setAttribute('aria-pressed', calloutsOn); tourBar.classList.toggle('hidden', !calloutsOn); if (!calloutsOn) stopTour(); dirty = true; };
    $('#rotate').before(tb);
    tourBar = Object.assign(document.createElement('div'), { className: 'fixed bottom-4 left-1/2 -translate-x-1/2 z-20 glass rounded-2xl px-2 py-2 flex items-center gap-1', role: 'group' });
    tourBar.setAttribute('aria-label', 'Callout tour');
    const label = Object.assign(document.createElement('span'), { className: 'text-xs text-slate-300 px-2 truncate text-center', style: 'max-width:38vw;min-width:60px' }); label.setAttribute('aria-live', 'polite'); label.textContent = `${annos.length} callouts`;
    const mk = (txt, aria, fn, extra = {}) => Object.assign(document.createElement('button'), { className: 'btn', type: 'button', textContent: txt, onclick: fn, ariaLabel: aria, ...extra });
    const go = d => { tourI = tourI < 0 ? (d > 0 ? 0 : annos.length - 1) : (tourI + d + annos.length) % annos.length; label.textContent = `${tourI + 1}/${annos.length}: ${annos[tourI].text}`; showAnno(tourI); };
    const play = mk('▶ Tour', 'Play or stop the callout tour', () => { if (tourTimer) return stopTour(); go(1); play.textContent = '■ Stop'; tourTimer = setInterval(() => go(1), 3800); });
    play.dataset.play = '1';
    tourBar.append(mk('◀', 'Previous callout', () => { stopTour(); go(-1); }), play, mk('▶', 'Next callout', () => { stopTour(); go(1); }), label);
    document.body.append(tourBar);
  }
  // start on the angle the creator was looking from
  const cam = cfg.camera; let A = 0, E = (el - 1) / 2;
  if (cam) {
    const dx = cam.pos[0] - cam.target[0], dy = cam.pos[1] - cam.target[1], dz = cam.pos[2] - cam.target[2];
    A = (Math.atan2(dx, dz) / (Math.PI * 2) * az + az) % az;
    const deg = Math.asin(dy / (Math.hypot(dx, dy, dz) || 1)) * 180 / Math.PI, ev = ix.elevs || [];
    E = ev.length > 1 ? Math.max(0, Math.min(el - 1, ev.reduce((b, v, k) => (Math.abs(v - deg) < Math.abs(ev[b] - deg) ? k : b), 0))) : 0;
  }
  const frameAt = () => ((Math.round(A) % az) + az) % az + Math.max(0, Math.min(el - 1, Math.round(E))) * az;

  // Two qualities: a small preview (320 px) is drawn while the view moves; once it has rested on one picture for FULL_AFTER ms
  // the full picture is decoded and drawn. Packs made before previews existed simply use the full pictures.
  const FULL_AFTER = 250, hasThumbs = Array.isArray(ix.thumbs) && ix.thumbs.length === az * el;
  const lru = cap => ({ map: new Map(), pending: new Set(), cap }), tc = lru(96), fc = lru(hasThumbs ? 12 : CACHE);
  let last = null, lastQ = '', dirty = true, curI = -1, lastChange = 0, fired = false, fullShown = false;
  const decode = (c, list, i) => { // decoded pictures live in a small least-recently-used cache (a whole pack decoded would need gigabytes)
    const hit = c.map.get(i); if (hit) { c.map.delete(i); c.map.set(i, hit); return hit; }
    if (!c.pending.has(i) && i >= 0 && i < az * el) {
      c.pending.add(i); const [o, len] = list[i];
      createImageBitmap(pack.slice(base + o, base + o + len, ix.mime)).then(b => {
        c.map.set(i, b); c.pending.delete(i); dirty = true;
        for (const [k, v] of c.map) { if (c.map.size <= c.cap) break; if (v !== last) { v.close?.(); c.map.delete(k); } } // never close the picture on screen
      }).catch(() => c.pending.delete(i));
    }
    return null;
  };

  let zoom = 1, px = 0, py = 0; // zoom about the centre (wheel or pinch), pan while zoomed
  const DPR = () => Math.min(devicePixelRatio || 1, 2); // phones report 3: drawing at 3x only costs speed
  const fit = () => Math.min(cv.width / w, cv.height / h) * zoom;
  let markCv = null; // links made before the watermark was baked into the pictures get it painted here, once per canvas size
  const buildMark = () => {
    const W = cv.width, H = cv.height, m = Object.assign(document.createElement('canvas'), { width: W, height: H }), x = m.getContext('2d'), fs = Math.max(13, Math.round(Math.min(W, H) / 24));
    x.font = `700 ${fs}px system-ui,sans-serif`; x.textBaseline = 'middle';
    if (wm.cross) { x.strokeStyle = 'rgba(255,255,255,.2)'; x.lineWidth = Math.max(1, fs / 12); x.beginPath(); x.moveTo(0, 0); x.lineTo(W, H); x.moveTo(W, 0); x.lineTo(0, H); x.stroke(); }
    if (wm.tile) {
      x.fillStyle = 'rgba(255,255,255,.2)'; x.shadowColor = 'rgba(0,0,0,.35)'; x.shadowBlur = 3; x.textAlign = 'center';
      x.translate(W / 2, H / 2); x.rotate(-Math.PI / 7); const step = fs * 4, span = Math.hypot(W, H), tw = x.measureText(wm.text).width + fs * 3;
      for (let y = -span; y < span; y += step) for (let xx = -span; xx < span; xx += tw) x.fillText(wm.text, xx, y);
    } else if (wm.text) {
      x.fillStyle = 'rgba(255,255,255,.8)'; x.shadowColor = 'rgba(0,0,0,.6)'; x.shadowBlur = 4; x.textAlign = 'right'; x.fillText(wm.text, W - fs, H - fs);
    }
    return m;
  };
  function draw(now) {
    const i = frameAt(); cv.dataset.frame = i;
    if (i !== curI) { curI = i; lastChange = now; fired = false; }   // a new picture: back to previews until it rests
    const settled = now - lastChange >= FULL_AFTER;
    let b = null, q = 'low';
    if ((settled || !hasThumbs) && complete) { b = decode(fc, ix.frames, i); if (b) q = 'full'; }
    if (!b && hasThumbs) b = decode(tc, ix.thumbs, i);
    const near = []; for (const d of [1, -1, 2, -2]) near.push((((i % az) + d + az) % az) + Math.floor(i / az) * az); // neighbours around the ring
    if (i >= az) near.push(i - az); if (i < az * (el - 1)) near.push(i + az);
    near.forEach(n => (hasThumbs ? decode(tc, ix.thumbs, n) : complete && decode(fc, ix.frames, n)));
    if (b) { last = b; lastQ = q; }
    fullShown = lastQ === 'full' && (settled || !hasThumbs); cv.dataset.quality = lastQ;
    ctx.clearRect(0, 0, cv.width, cv.height);
    const s = fit(), ox = (cv.width - w * s) / 2 + px, oy = (cv.height - h * s) / 2 + py;
    if (last) { try { ctx.drawImage(last, ox, oy, w * s, h * s); } catch { last = null; dirty = true; } } // a bad picture must not blank the view
    if (wm && !wm.baked) ctx.drawImage(markCv ||= buildMark(), 0, 0);
    const k = cv.clientWidth / cv.width, fa = ix.anno?.[i] || [];
    chips.forEach((c, j) => {
      const a = fa[j]; c.hidden = !calloutsOn || !a || a[2] < SEEN || !last;
      if (a) c.style.transform = `translate(${(ox + a[0] * w * s) * k}px,${(oy + a[1] * h * s) * k}px) translate(-50%,-130%)`;
    });
  }

  // input: drag (mouse or finger) rotates, wheel or pinch zooms, arrow keys step
  const ptrs = new Map(); let vA = 0, vE = 0, auto = false, pinch = 0, anim = null;
  const clampZoom = () => { zoom = Math.max(1, Math.min(3, zoom)); if (zoom === 1) px = py = 0; dirty = true; };
  cv.addEventListener('pointerdown', e => { stopTour(); cv.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, [e.clientX, e.clientY]); vA = vE = 0; anim = null; cv.style.cursor = 'grabbing'; pinch = 0; });
  cv.addEventListener('pointermove', e => {
    const o = ptrs.get(e.pointerId); if (!o) return;
    const dx = e.clientX - o[0], dy = e.clientY - o[1]; ptrs.set(e.pointerId, [e.clientX, e.clientY]);
    if (ptrs.size === 2) {
      const [a, c] = [...ptrs.values()], d = Math.hypot(a[0] - c[0], a[1] - c[1]); if (pinch) { zoom *= d / pinch; clampZoom(); } pinch = d;
      if (zoom > 1.01) { px += dx * DPR() / 2; py += dy * DPR() / 2; dirty = true; } // two fingers moving together pan
      return;
    }
    if (zoom > 1.01 && (e.shiftKey || e.buttons & 6)) { px += dx * DPR(); py += dy * DPR(); dirty = true; return; } // pan: Shift, right or middle button
    vA = -dx * az * .75 / cv.clientWidth; vE = dy * (el - 1) * 1.2 / cv.clientHeight; A += vA; E = Math.max(0, Math.min(el - 1, E + vE)); dirty = true;
  });
  const up = e => { ptrs.delete(e.pointerId); pinch = 0; cv.style.cursor = 'grab'; if (calm) vA = vE = 0; };
  cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
  cv.addEventListener('contextmenu', e => e.preventDefault());
  cv.addEventListener('wheel', e => { e.preventDefault(); zoom *= e.deltaY < 0 ? 1.1 : 1 / 1.1; clampZoom(); }, { passive: false });
  cv.addEventListener('keydown', e => {
    const m = { ArrowLeft: [1, 0], ArrowRight: [-1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
    if (m) { e.preventDefault(); stopTour(); A += m[0]; E = Math.max(0, Math.min(el - 1, E + m[1])); dirty = true; return; }
    if (e.key === '+' || e.key === '=') { zoom *= 1.25; clampZoom(); } else if (e.key === '-') { zoom /= 1.25; clampZoom(); } else if (e.key === '0') { zoom = 1; clampZoom(); }
  });
  $('#rotate').setAttribute('aria-pressed', 'false');
  $('#rotate').onclick = e => { auto = !auto; e.currentTarget.classList.toggle('on', auto); e.currentTarget.setAttribute('aria-pressed', auto); };

  // callout click: turn to the picture where that callout faces the camera, closest to the centre
  function showAnno(i) {
    let best = -1, bs = -1;
    // the picture where the tag faces the camera squarely and sits near the middle (never one where it is on the far side)
    (ix.anno || []).forEach((fa, f) => { const a = fa[i]; if (a && a[2] >= SEEN) { const s = a[2] + (1 - Math.abs(a[0] - .5) - Math.abs(a[1] - .5) * .5) * .6; if (s > bs) { bs = s; best = f; } } });
    if (best < 0) return toast('This point is not visible from the pictured angles');
    turnTo(best % az, Math.floor(best / az));
  }
  function turnTo(a, e) { // smooth turn to picture column `a` and height `e`, by the shortest way round; also resets zoom
    let dA = a - (((A % az) + az) % az); if (dA > az / 2) dA -= az; if (dA < -az / 2) dA += az;
    zoom = 1; px = py = 0; auto = false; $('#rotate').classList.remove('on'); $('#rotate').setAttribute('aria-pressed', 'false');
    if (calm) { A += dA; E = e; } else anim = { t0: performance.now(), A0: A, E0: E, dA, dE: e - E };
    dirty = true;
  }
  { // camera presets: Front looks along -Z (glTF front), Side from +X; the height closest to level
    const lvl = (ix.elevs || []).reduce((b, v, k, all) => (Math.abs(v) < Math.abs(all[b]) ? k : b), 0);
    for (const [name, col] of [['Front', 0], ['Side', Math.round(az / 4)]]) {
      const b = Object.assign(document.createElement('button'), { className: 'btn', textContent: name, title: name + ' view' });
      b.onclick = () => { stopTour(); turnTo(col, lvl); }; $('#rotate').before(b);
    }
  }

  const fitCanvas = () => { cv.width = Math.round(cv.clientWidth * DPR()) || 1; cv.height = Math.round(cv.clientHeight * DPR()) || 1; markCv = null; dirty = true; };
  new ResizeObserver(fitCanvas).observe(box); fitCanvas();
  let t = performance.now();
  (function loop(now) {
    const dt = Math.min(.05, (now - t) / 1000); t = now;
    if (anim) { const k = Math.min(1, (now - anim.t0) / 700), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2; A = anim.A0 + anim.dA * e; E = anim.E0 + anim.dE * e; if (k >= 1) anim = null; dirty = true; }
    else if (!ptrs.size && (Math.abs(vA) > .002 || Math.abs(vE) > .002)) { A += vA; E = Math.max(0, Math.min(el - 1, E + vE)); vA *= .93; vE *= .8; dirty = true; } // inertia after release
    if (auto && !ptrs.size && !calm) { A += az * dt / 12; dirty = true; }
    if (!fired && curI >= 0 && !fullShown && now - lastChange >= FULL_AFTER) { fired = true; dirty = true; } // rested on one picture: load the full one
    if (dirty) { dirty = false; draw(now); }
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
