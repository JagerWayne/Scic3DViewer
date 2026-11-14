// Shared 3D engine for Studio and Viewer. Pure client code: no network calls in here.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { RGBELoader } from 'three/addons/loaders/RGBELoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { OBJExporter } from 'three/addons/exporters/OBJExporter.js';
import { STLExporter } from 'three/addons/exporters/STLExporter.js';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';
import { ThreeMFLoader } from 'three/addons/loaders/3MFLoader.js';
import { USDZExporter } from 'three/addons/exporters/USDZExporter.js';
import { SimplifyModifier } from 'three/addons/modifiers/SimplifyModifier.js';
import * as BGU from 'three/addons/utils/BufferGeometryUtils.js';

const V3 = THREE.Vector3;
import { BACKDROPS, BACKDROP_FLAT } from '/static/js/backdrops.js';
export { BACKDROPS };
// [hemisphere intensity, then (intensity, position) for key / fill / rim directional lights]
const LIGHT_PRESETS = {
  studio: { hemi: .6, dirs: [[2.2, [3, 5, 4]], [.8, [-4, 2, -2]], [1.2, [0, 3, -5]]] },
  soft: { hemi: 1, dirs: [[1.4, [2, 6, 3]], [1, [-3, 3, 2]], [.6, [0, 3, -5]]] },
  dramatic: { hemi: .15, dirs: [[3.4, [5, 3, 1]], [.15, [-4, 2, -2]], [2, [-4, 2, -4]]] },
  flat: { hemi: 1.4, dirs: [[.8, [0, 4, 5]], [.8, [0, 4, -5]], [.3, [0, 3, -5]]] },
};
const FINISHES = {
  slate: () => new THREE.MeshStandardMaterial({ color: 0x3b4350, roughness: .85 }),
  steel: () => new THREE.MeshStandardMaterial({ color: 0xb8c0cc, roughness: .25, metalness: 1 }),
  glass: () => new THREE.MeshPhysicalMaterial({ color: 0xcfe8ff, roughness: .05, transmission: 1, thickness: .5, ior: 1.5 }),
  obsidian: () => new THREE.MeshStandardMaterial({ color: 0x0b0b10, roughness: .1, metalness: .2 }),
};

export class Stage extends EventTarget {
  constructor(container) {
    super();
    this.container = container;
    const r = this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    r.setPixelRatio(Math.min(devicePixelRatio, 2));
    r.localClippingEnabled = true;
    r.shadowMap.enabled = true; r.shadowMap.type = THREE.PCFSoftShadowMap;
    r.setClearColor(0x000000, 0);
    container.appendChild(r.domElement);
    this.labelsEl = Object.assign(document.createElement('div'), { className: 'labels' });
    container.appendChild(this.labelsEl);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(45, 1, .01, 1000);
    this.camera.position.set(3, 2, 3);
    this.controls = new OrbitControls(this.camera, r.domElement);
    this.controls.enableDamping = true;
    this.controls.autoRotate = false; // studio starts still
    this.pmrem = new THREE.PMREMGenerator(r);
    this.envCache = {};

    this.assembly = new THREE.Group(); this.assembly.name = 'assembly';
    this.base = new THREE.Group(); this.base.name = 'pedestal';
    this.overlay = new THREE.Group();   // tool visuals (never exported)
    this.annoGroup = new THREE.Group();
    this.scene.add(this.assembly, this.base, this.overlay, this.annoGroup);

    const dir = (c, i, x, y, z) => { const l = new THREE.DirectionalLight(c, i); l.position.set(x, y, z); return l; };
    this.lights = [new THREE.HemisphereLight(0xffffff, 0x445566, .6), dir(0xffffff, 2.2, 3, 5, 4), dir(0xbfd4ff, .8, -4, 2, -2), dir(0xffffff, 1.2, 0, 3, -5)];
    this.lights.forEach(l => { l.userData.base = l.intensity; this.scene.add(l); });
    const key = this.lights[1]; key.castShadow = true; key.shadow.mapSize.set(2048, 2048); key.shadow.bias = -0.0004; key.shadow.radius = 4;
    this.scene.add(key.target);
    this.floor = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), new THREE.ShadowMaterial({ opacity: .35 }));
    this.floor.rotation.x = -Math.PI / 2; this.floor.receiveShadow = true; this.scene.add(this.floor);
    this._lc = new V3(); this._ls = 1; this.preset = 'studio';
    this.spotLight = new THREE.PointLight(0xffffff, 0, 0, 2); this.scene.add(this.spotLight);
    this.dim = 1; this.dimTarget = 1; this.spotI = 0; this.spotITarget = 0;

    this.grid = new THREE.GridHelper(10, 20, 0x64748b, 0x334155); this.scene.add(this.grid);
    this.selBox = new THREE.BoxHelper(new THREE.Object3D(), 0x38bdf8); this.selBox.visible = false; this.scene.add(this.selBox);
    this.tc = new TransformControls(this.camera, r.domElement); this.tc.visible = false; this.scene.add(this.tc);
    this.tc.addEventListener('dragging-changed', e => {
      this.controls.enabled = !e.value; this._noSelectUntil = performance.now() + 250;
      const o = this.tc.object;
      if (e.value) { this.setExplode(0); this.emit('explode-reset'); this._xf0 = o && { o, p: o.position.clone(), q: o.quaternion.clone(), s: o.scale.clone() }; }
      else {
        this._clearP0(); const x = this._xf0; this._xf0 = null;
        if (x && (!x.p.equals(x.o.position) || !x.q.equals(x.o.quaternion) || !x.s.equals(x.o.scale))) { // one undo step per drag
          const a = { p: x.o.position.clone(), q: x.o.quaternion.clone(), s: x.o.scale.clone() };
          const set = v => { x.o.position.copy(v.p); x.o.quaternion.copy(v.q); x.o.scale.copy(v.s); this._clearP0(); };
          this.record({ undo: () => set(x), redo: () => set(a) });
        }
      }
    });
    this.tc.addEventListener('change', () => this.emit('change'));

    this.state = { rough: .5, metal: 0, touched: false, env: 'softbox', envI: 1, clipOn: false, clipC: 0, explode: 0, light: 1, backdrop: 'midnight' };
    this.clipPlane = new THREE.Plane(new V3(0, 0, -1), 0);
    this.mode = 'select'; this.tool = 'brush'; this.mtype = 'ruler';
    this.brushR = .12; this.paintColor = new THREE.Color('#ef4444'); this.fillAngle = 30;
    this.history = []; this.redoStack = []; this.snap = { on: false, deg: 15 }; this.canPaintUndo = true; this.selectable = true; this.gizmo = false; this.maxAnnotations = null;
    this.ask = m => window.prompt(m, '');
    this.ray = new THREE.Raycaster(); this._ndc = new THREE.Vector2();
    this.labels = []; this.annotations = []; this.annoLabels = []; this.dims = [];
    this.unit = 'units'; this.unitScale = 1; this.pinned = null; this.fly = null; this.mpts = [];
    this.ring = new THREE.Mesh(new THREE.RingGeometry(.93, 1, 48), new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide, depthTest: false, transparent: true, opacity: .9 }));
    this.ring.visible = false; this.ring.renderOrder = 20; this.overlay.add(this.ring);
    this.hl = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({ color: 0xfacc15, transparent: true, opacity: .55, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }));
    this.hl.matrixAutoUpdate = false; this.hl.visible = false; this.overlay.add(this.hl);

    r.domElement.style.touchAction = 'none';
    container.addEventListener('pointerdown', e => this._down(e), true);
    window.addEventListener('pointermove', e => this._move(e));
    window.addEventListener('pointerup', e => this._up(e));
    new ResizeObserver(() => this._resize()).observe(container);
    this._resize();
    this.setEnv('softbox', 1);
    this.setBackdrop('midnight');
    r.setAnimationLoop(() => this._loop());
  }

  emit(n, d) { this.dispatchEvent(new CustomEvent(n, { detail: d })); }
  _resize() {
    this.w = this.container.clientWidth || 1; this.h = this.container.clientHeight || 1;
    this.renderer.setSize(this.w, this.h); this.camera.aspect = this.w / this.h; this.camera.updateProjectionMatrix();
  }

  // ---------- loading ----------
  async loadFile(file) {
    const ext = file.name.split('.').pop().toLowerCase(), buf = await file.arrayBuffer();
    let obj;
    if (ext === 'glb' || ext === 'gltf') obj = (await this._gltf().parseAsync(buf, '')).scene;
    else if (ext === 'obj') obj = new OBJLoader().parse(new TextDecoder().decode(buf));
    else if (ext === 'fbx') obj = new FBXLoader().parse(buf, '');
    else if (ext === '3mf') obj = new ThreeMFLoader().parse(buf);
    else if (ext === 'step' || ext === 'stp') obj = await this._loadStep(buf);
    else if (ext === 'stl') obj = new THREE.Mesh(new STLLoader().parse(buf), new THREE.MeshStandardMaterial({ color: 0xb0b8c4 }));
    else throw new Error('Unsupported format: .' + ext);
    if (['stl', 'step', 'stp', '3mf'].includes(ext)) obj.rotation.x = -Math.PI / 2; // CAD formats have Z up, the stage has Y up
    return this.addModel(obj, file.name);
  }
  async _loadStep(buf) { // CAD import: STEP is converted in the browser by OpenCascade (WASM, loaded from the CDN on first use)
    const base = 'https://cdn.jsdelivr.net/npm/occt-import-js@0.0.23/dist/';
    if (!window.occtimportjs) await new Promise((ok, no) => {
      const s = Object.assign(document.createElement('script'), { src: base + 'occt-import-js.js', onload: ok, onerror: () => no(new Error('Could not load the STEP converter (internet needed)')) });
      document.head.appendChild(s);
    });
    const res = (await window.occtimportjs({ locateFile: f => base + f })).ReadStepFile(new Uint8Array(buf), null);
    if (!res.success) throw new Error('Could not read this STEP file');
    const group = new THREE.Group();
    for (const m of res.meshes) {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(m.attributes.position.array, 3));
      if (m.attributes.normal) g.setAttribute('normal', new THREE.Float32BufferAttribute(m.attributes.normal.array, 3));
      g.setIndex(m.index.array.length > 65535 ? new THREE.Uint32BufferAttribute(m.index.array, 1) : new THREE.Uint16BufferAttribute(m.index.array, 1));
      const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: m.color ? new THREE.Color(...m.color) : 0xb0b8c4, metalness: .1, roughness: .6 }));
      mesh.name = m.name || 'Part'; group.add(mesh);
    }
    return group;
  }
  _gltf() {
    const l = new GLTFLoader(); const d = new DRACOLoader();
    d.setDecoderPath('https://www.gstatic.com/draco/versioned/decoders/1.5.6/'); l.setDRACOLoader(d); return l;
  }
  // A GLB we exported ourselves: already normalised, so just unpack our two root groups.
  async loadSaved(buf) {
    const root = (await this._gltf().parseAsync(buf, '')).scene;
    const a = root.getObjectByName('assembly'), p = root.getObjectByName('pedestal');
    [...(a?.children || [])].forEach(c => this.assembly.add(c));
    [...(p?.children || [])].forEach(c => this.base.add(c));
    this._prep(this.assembly); this._prep(this.base); this._applyAll(); this.frame(); this.emit('change');
  }
  _prep(root) {
    root.traverse(o => {
      if (!o.isMesh) return;
      if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
      const conv = m => m.isMeshStandardMaterial ? m : new THREE.MeshStandardMaterial({
        color: m.color || 0xcccccc, map: m.map || null, roughness: .6, side: m.side, vertexColors: !!o.geometry.attributes.color });
      o.material = Array.isArray(o.material) ? o.material.map(conv) : conv(o.material);
      o.castShadow = o.receiveShadow = true;
    });
  }
  addModel(obj, name) {
    this._prep(obj);
    const part = new THREE.Group(); part.name = name.replace(/\.[^.]+$/, ''); part.add(obj);
    const box = new THREE.Box3().setFromObject(obj), sz = box.getSize(new V3());
    obj.scale.multiplyScalar(2 / (Math.max(sz.x, sz.y, sz.z) || 1)); obj.updateMatrixWorld(true);
    box.setFromObject(obj); const c = box.getCenter(new V3());
    obj.position.x -= c.x; obj.position.z -= c.z; obj.position.y -= box.min.y; // centred, resting on y=0
    this.assembly.add(part); this._clearP0(); this._applyAll();
    if (this.mode === 'paint') this._prepAll();
    this.setExplode(this.state.explode); this.frame(); this.emit('change'); return part;
  }
  frame() {
    const box = new THREE.Box3().setFromObject(this.assembly).union(new THREE.Box3().setFromObject(this.base));
    if (box.isEmpty()) return;
    const c = box.getCenter(new V3()), d = box.getSize(new V3()).length() / 2 / Math.sin(this._halfFov()) * 1.1;
    this.camera.position.copy(c).add(new V3(1, .7, 1).normalize().multiplyScalar(d));
    this.controls.target.copy(c); this.camera.near = d / 100; this.camera.far = d * 100; this.camera.updateProjectionMatrix();
    this.grid.position.y = box.min.y; this.controls.update();
    this._lc.copy(c); this._ls = box.getSize(new V3()).length() / 2; this._placeLights();
  }
  _placeLights() { // lights and the shadow camera follow the model's size, using the chosen preset
    const P = LIGHT_PRESETS[this.preset] || LIGHT_PRESETS.studio, s = this._ls, c = this._lc, key = this.lights[1];
    this.lights[0].userData.base = P.hemi;
    P.dirs.forEach(([k, pos], i) => { const l = this.lights[i + 1]; l.userData.base = k; l.position.copy(c).add(new V3(...pos).multiplyScalar(s * .5)); });
    key.target.position.copy(c);
    Object.assign(key.shadow.camera, { left: -s * 1.6, right: s * 1.6, top: s * 1.6, bottom: -s * 1.6, near: .1, far: s * 12 }); key.shadow.camera.updateProjectionMatrix();
  }
  setLightPreset(name) { this.preset = name; this._placeLights(); }
  setShadows(on) { this.lights[1].castShadow = on; this.floor.visible = on; this.eachMat(m => (m.needsUpdate = true)); }
  _halfFov() { const v = this.camera.fov * Math.PI / 360; return Math.min(v, Math.atan(Math.tan(v) * this.camera.aspect)); } // the narrower of vertical/horizontal: portrait phones are limited by width
  fitIfClipped() { // keep the camera angle but step back when the model does not fit this screen (e.g. a desktop camera opened on a phone)
    const box = new THREE.Box3().setFromObject(this.assembly).union(new THREE.Box3().setFromObject(this.base)); if (box.isEmpty()) return;
    const need = box.getSize(new V3()).length() / 2 / Math.sin(this._halfFov()) * 1.05, dir = this.camera.position.clone().sub(this.controls.target);
    if (dir.length() < need) { this.camera.position.copy(this.controls.target).add(dir.setLength(need)); this.camera.far = Math.max(this.camera.far, need * 20); this.camera.updateProjectionMatrix(); this.controls.update(); }
  }
  meshes() { const a = []; this.assembly.traverseVisible(o => o.isMesh && a.push(o)); return a; }
  meshesAll() { const a = []; this.assembly.traverse(o => o.isMesh && a.push(o)); return a; }
  size() { const b = new THREE.Box3().setFromObject(this.assembly); return b.isEmpty() ? 2 : Math.max(...b.getSize(new V3()).toArray()); }
  triCount() { return this.meshesAll().reduce((n, m) => n + (m.geometry.index ? m.geometry.index.count : m.geometry.attributes.position.count) / 3, 0) | 0; }
  eachMat(fn) {
    for (const root of [this.assembly, this.base]) root.traverse(o => o.isMesh && (Array.isArray(o.material) ? o.material : [o.material]).forEach(fn));
  }

  // ---------- look: stage, light, material, env, clip ----------
  setBackdrop(id) { this.state.backdrop = id; this.container.style.background = BACKDROPS[id] || BACKDROPS.midnight; }
  setLight(v) { this.state.light = v; }
  setMaterial(rough, metal) { Object.assign(this.state, { rough, metal, touched: true }); this._applyAll(); }
  setClip(on, c) {
    Object.assign(this.state, { clipOn: on, clipC: c }); this.clipPlane.constant = c;
    this.eachMat(m => { m.userData.side0 ??= m.side; m.clippingPlanes = on ? [this.clipPlane] : null; m.side = on ? THREE.DoubleSide : m.userData.side0; m.needsUpdate = true; });
  }
  _applyAll() {
    const s = this.state;
    this.eachMat(m => {
      m.envMapIntensity = s.envI;
      if (s.touched && m.userData.isBaseMat !== true) { m.roughness = s.rough; m.metalness = s.metal; }
    });
    if (s.clipOn) this.setClip(true, s.clipC);
  }
  setEnv(preset, intensity = this.state.envI) {
    this.state.env = preset; this.state.envI = intensity;
    if (preset === 'none') this.scene.environment = null;
    else if (preset !== 'custom') {
      this.envCache[preset] ??= this.pmrem.fromScene(this._envScene(preset), preset === 'softbox' ? .04 : 0).texture;
      this.scene.environment = this.envCache[preset];
    }
    this._applyAll();
  }
  async setHdr(urlOrFile, intensity = this.state.envI) {
    const url = typeof urlOrFile === 'string' ? urlOrFile : URL.createObjectURL(urlOrFile);
    const tex = await new RGBELoader().loadAsync(url); tex.mapping = THREE.EquirectangularReflectionMapping;
    this.scene.environment = this.pmrem.fromEquirectangular(tex).texture; tex.dispose();
    if (typeof urlOrFile !== 'string') { this.hdrFile = urlOrFile; URL.revokeObjectURL(url); }
    this.state.env = 'custom'; this.state.envI = intensity; this._applyAll();
  }
  _envScene(kind) {
    if (kind === 'softbox') return new RoomEnvironment();
    const s = new THREE.Scene(), glow = (w, h, x, y, z, k) => {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color().setScalar(k), side: THREE.DoubleSide }));
      m.position.set(x, y, z); m.lookAt(0, 0, 0); s.add(m);
    };
    if (kind === 'showroom') {
      s.add(new THREE.Mesh(new THREE.BoxGeometry(20, 10, 20), new THREE.MeshBasicMaterial({ color: 0x25272b, side: THREE.BackSide })));
      for (let i = -2; i <= 2; i++) glow(2, 14, i * 4, 4.9, 0, 12);
      glow(8, 3, 0, 1, -9, 4); glow(8, 3, 9, 1, 0, 3);
    } else { // daylight sky
      const g = new THREE.SphereGeometry(30, 32, 16), col = [];
      const p = g.attributes.position, top = new THREE.Color(0x5f9bff), hor = new THREE.Color(0xffffff), gnd = new THREE.Color(0x8a8f98);
      for (let i = 0; i < p.count; i++) { const y = p.getY(i) / 30; const c = y > 0 ? hor.clone().lerp(top, Math.pow(y, .6)) : hor.clone().lerp(gnd, Math.min(1, -y * 3)); col.push(c.r, c.g, c.b); }
      g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
      s.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.BackSide })));
      const sun = new THREE.Mesh(new THREE.SphereGeometry(2, 16, 8), new THREE.MeshBasicMaterial({ color: new THREE.Color().setScalar(40) })); sun.position.set(12, 18, 8); s.add(sun);
    }
    return s;
  }

  // ---------- outliner ops ----------
  select(part) {
    this.selected = part || null; this.selBox.visible = !!part;
    if (part && this.gizmo) { this.tc.attach(part); this.tc.visible = true; } else { this.tc.detach(); this.tc.visible = false; }
    this.emit('select', part);
  }
  setGizmoMode(m) { this.tc.setMode(m); }
  topPart(o) { while (o && o.parent !== this.assembly) o = o.parent; return o; }
  toggleVisible(o) { o.visible = !o.visible; this.emit('tree'); }
  solo(o) {
    if (this._solo) { this._solo.forEach((v, k) => (k.visible = v)); this._solo = null; }
    else {
      this._solo = new Map(); const keep = new Set(); o.traverse(c => keep.add(c)); for (let p = o; p; p = p.parent) keep.add(p);
      this.assembly.traverse(c => { if (c === this.assembly) return; this._solo.set(c, c.visible); c.visible = keep.has(c); });
      this._soloObj = o;
    }
    this.emit('tree');
  }
  unsolo() { if (this._solo) this.solo(); }
  remove(o) {
    this.unsolo(); if (this.selected && (o === this.selected || o.getObjectById(this.selected.id))) this.select(null);
    o.parent.remove(o); this._clearP0(); this.setExplode(this.state.explode); this.emit('tree'); this.emit('change');
  }
  rename(o, n) { o.name = n; this.emit('tree'); }
  zUpToYUp() { // for files whose Z axis points up (CAD exports, some OBJ): turn the model -90 degrees about X and stand it on the floor
    const X = new V3(1, 0, 0), a = -Math.PI / 2;
    this.assembly.children.forEach(c => { c.position.applyAxisAngle(X, a); c.rotateOnWorldAxis(X, a); });
    this.assembly.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(this.assembly);
    if (!box.isEmpty()) this.assembly.children.forEach(c => (c.position.y -= box.min.y));
    this._clearP0(); this.setExplode(this.state.explode); this.frame(); this.emit('tree'); this.emit('change');
  }
  mergeAll() {
    const ms = this.meshes(); if (!ms.length) return;
    this.unsolo(); this.setExplode(0); this.select(null); this.assembly.updateMatrixWorld(true); this._follow(true);
    const keep = ['position', 'normal', 'color'], gs = ms.map(m => {
      let g = m.geometry.index ? m.geometry.toNonIndexed() : m.geometry.clone();
      Object.keys(g.attributes).forEach(k => keep.includes(k) || g.deleteAttribute(k));
      if (!g.attributes.normal) g.computeVertexNormals();
      const mat = Array.isArray(m.material) ? m.material[0] : m.material, base = mat.color || new THREE.Color(1, 1, 1);
      const n = g.attributes.position.count, vc = g.attributes.color, arr = new Float32Array(n * 3);
      for (let i = 0; i < n; i++) { arr[3 * i] = (vc ? vc.getX(i) : 1) * base.r; arr[3 * i + 1] = (vc ? vc.getY(i) : 1) * base.g; arr[3 * i + 2] = (vc ? vc.getZ(i) : 1) * base.b; }
      g.setAttribute('color', new THREE.BufferAttribute(arr, 3)); g.applyMatrix4(m.matrixWorld); return g;
    });
    const mesh = new THREE.Mesh(BGU.mergeGeometries(gs, false), new THREE.MeshStandardMaterial({ vertexColors: true, roughness: .6 }));
    const part = new THREE.Group(); part.name = 'Merged'; part.add(mesh);
    this.assembly.clear(); this.assembly.add(part); this._clearP0(); this._applyAll(); part.updateWorldMatrix(true, false);
    this.annotations.forEach(a => this._anchor(a, part)); this.emit('tree'); this.emit('change');
  }

  // ---------- exploded view ----------
  explodeUnits() {
    if (this.assembly.children.length > 1) return this.assembly.children;
    const m = this.meshesAll(); return m.length > 1 ? m : [];
  }
  _clearP0() { this.assembly.traverse(o => delete o.userData.p0); }
  setExplode(v) {
    this.state.explode = v; const u = this.explodeUnits(); if (u.length < 2) return false;
    u.forEach(o => { o.userData.p0 ??= o.position.clone(); o.position.copy(o.userData.p0); });
    this.assembly.updateMatrixWorld(true);
    const bc = new THREE.Box3().setFromObject(this.assembly), c0 = bc.getCenter(new V3()), sz = Math.max(...bc.getSize(new V3()).toArray());
    u.forEach(o => {
      const d = new THREE.Box3().setFromObject(o).getCenter(new V3()).sub(c0); if (d.lengthSq() < 1e-8) d.set(0, 1, 0);
      const w = o.getWorldPosition(new V3()).addScaledVector(d.normalize(), v * sz * .8);
      o.parent.worldToLocal(w); o.position.copy(w);
    });
    return true;
  }

  // ---------- paint ----------
  _prepAll() { this.meshesAll().forEach(m => this._prepMesh(m)); }
  _prepMesh(m) {
    let g = m.geometry;
    if (g.index) { g = g.toNonIndexed(); m.geometry = g; }
    if (!g.attributes.color) g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(g.attributes.position.count * 3).fill(1), 3));
    if (!m.userData.color0 || m.userData.color0.length !== g.attributes.color.array.length) m.userData.color0 = g.attributes.color.array.slice();
    (Array.isArray(m.material) ? m.material : [m.material]).forEach(x => { if (!x.vertexColors) { x.vertexColors = true; x.needsUpdate = true; } });
  }
  _brush(h) {
    const m = h.object; this._prepMesh(m);
    const pos = m.geometry.attributes.position, col = m.geometry.attributes.color;
    if (!this._stroke.has(m)) this._stroke.set(m, col.array.slice());
    const lp = m.worldToLocal(h.point.clone()), r = this.brushR / m.matrixWorld.getMaxScaleOnAxis(), c = this.paintColor;
    for (let i = 0; i < pos.count; i++) {
      const dx = pos.getX(i) - lp.x, dy = pos.getY(i) - lp.y, dz = pos.getZ(i) - lp.z;
      if (dx * dx + dy * dy + dz * dz <= r * r) col.setXYZ(i, c.r, c.g, c.b);
    }
    col.needsUpdate = true;
  }
  // ---------- undo / redo: one history for moves, callouts and paint ----------
  record(e) { this.history.push(e); if (this.history.length > 60) this.history.shift(); this.redoStack = []; this.emit('history'); }
  undo() {
    const e = this.history.pop(); if (!e) return false;
    if (e.paint && !this.canPaintUndo) { this.history.push(e); this.emit('locked', 'paint_undo'); return false; }
    e.undo(); this.redoStack.push(e); this.emit('history'); this.emit('change'); return true;
  }
  redo() {
    const e = this.redoStack.pop(); if (!e) return false;
    e.redo(); this.history.push(e); this.emit('history'); this.emit('change'); return true;
  }
  _pushUndo(before) { // paint: `before` maps mesh -> colours before the stroke; the colours now are the "after"
    if (!before.size) return;
    const after = new Map(); before.forEach((_, m) => after.set(m, m.geometry.attributes.color.array.slice()));
    const put = map => map.forEach((arr, m) => { m.geometry.attributes.color.array.set(arr); m.geometry.attributes.color.needsUpdate = true; });
    this.record({ paint: true, undo: () => put(before), redo: () => put(after) });
  }
  setSnap(on, deg = this.snap.deg) { // rotation in degrees; moves and scale snap to 0.1 units
    this.snap = { on, deg };
    this.tc.setRotationSnap(on ? THREE.MathUtils.degToRad(deg) : null); this.tc.setTranslationSnap(on ? .1 : null); this.tc.setScaleSnap(on ? .1 : null);
    this.emit('snap');
  }
  resetColors() {
    const e = new Map();
    this.meshesAll().forEach(m => {
      const c = m.geometry.attributes.color; if (!c || !m.userData.color0) return;
      e.set(m, c.array.slice()); c.array.set(m.userData.color0); c.needsUpdate = true;
    });
    this._pushUndo(e);
  }
  _adj(m) {
    const g = m.geometry; let a = m.userData.adj; if (a && a.geo === g) return a;
    const pos = g.attributes.position, n = (pos.count / 3) | 0, ids = new Int32Array(pos.count), map = new Map();
    g.computeBoundingBox(); const q = 1e5 / (Math.max(...g.boundingBox.getSize(new V3()).toArray()) || 1);
    let nid = 0;
    for (let i = 0; i < pos.count; i++) {
      const k = Math.round(pos.getX(i) * q) + ',' + Math.round(pos.getY(i) * q) + ',' + Math.round(pos.getZ(i) * q);
      let id = map.get(k); if (id === undefined) { id = nid++; map.set(k, id); } ids[i] = id;
    }
    const em = new Map(), nrm = new Float32Array(n * 3), A = new V3(), B = new V3(), C = new V3();
    for (let t = 0; t < n; t++) {
      A.fromBufferAttribute(pos, 3 * t); B.fromBufferAttribute(pos, 3 * t + 1).sub(A); C.fromBufferAttribute(pos, 3 * t + 2).sub(A);
      B.cross(C).normalize(); nrm.set([B.x, B.y, B.z], 3 * t);
      for (let e = 0; e < 3; e++) {
        const a1 = ids[3 * t + e], b1 = ids[3 * t + (e + 1) % 3], k = a1 < b1 ? a1 * nid + b1 : b1 * nid + a1;
        (em.get(k) || em.set(k, []).get(k)).push(t);
      }
    }
    const nb = Array.from({ length: n }, () => []);
    em.forEach(l => { for (let i = 0; i < l.length; i++) for (let j = i + 1; j < l.length; j++) { nb[l[i]].push(l[j]); nb[l[j]].push(l[i]); } });
    return (m.userData.adj = { geo: g, nb, nrm });
  }
  _region(m, f0) { // triangles reachable from f0 without crossing a crease sharper than fillAngle
    const { nb, nrm } = this._adj(m), cos = Math.cos(this.fillAngle * Math.PI / 180), seen = new Set([f0]), st = [f0];
    while (st.length) {
      const t = st.pop();
      for (const u of nb[t]) {
        if (seen.has(u)) continue;
        if (nrm[3 * t] * nrm[3 * u] + nrm[3 * t + 1] * nrm[3 * u + 1] + nrm[3 * t + 2] * nrm[3 * u + 2] >= cos) { seen.add(u); st.push(u); }
      }
    }
    return seen;
  }
  _fill(h) {
    const m = h.object, col = m.geometry.attributes.color, c = this.paintColor, e = new Map([[m, col.array.slice()]]);
    this._region(m, h.faceIndex).forEach(t => { for (let k = 0; k < 3; k++) col.setXYZ(3 * t + k, c.r, c.g, c.b); });
    col.needsUpdate = true; this._pushUndo(e);
  }
  _preview(h) {
    if (!h || this.tool !== 'fill') { this.hl.visible = false; return; }
    const m = h.object, key = m.geometry.uuid + h.faceIndex + '/' + this.fillAngle;
    if (this._hlKey !== key) {
      this._hlKey = key; const pos = m.geometry.attributes.position, out = [];
      this._region(m, h.faceIndex).forEach(t => { for (let k = 0; k < 3; k++) out.push(pos.getX(3 * t + k), pos.getY(3 * t + k), pos.getZ(3 * t + k)); });
      this.hl.geometry.dispose(); this.hl.geometry = new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(out, 3));
    }
    this.hl.matrix.copy(m.matrixWorld); this.hl.visible = true;
  }
  _cursor(h) {
    if (!h || this.tool !== 'brush') { this.ring.visible = false; return; }
    const n = h.face.normal.clone().transformDirection(h.object.matrixWorld);
    this.ring.position.copy(h.point).addScaledVector(n, this.size() * .002); this.ring.lookAt(h.point.clone().add(n));
    this.ring.scale.setScalar(this.brushR); this.ring.visible = true;
  }

  // ---------- modes & pointer ----------
  setMode(mode, tool) {
    this.mode = mode; if (tool) { if (mode === 'paint') this.tool = tool; if (mode === 'measure') this.mtype = tool; }
    this.ring.visible = this.hl.visible = false; this.mpts = [];
    if (mode === 'paint') this._prepAll();
    this.container.style.cursor = mode === 'select' ? '' : 'crosshair'; this.emit('mode', mode);
  }
  _clipped(p) { return this.state.clipOn && p.z > this.clipPlane.constant; }
  pick(e) {
    const r = this.renderer.domElement.getBoundingClientRect();
    this._ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.ray.setFromCamera(this._ndc, this.camera);
    return this.ray.intersectObjects(this.meshes(), false).find(h => !this._clipped(h.point)) || null;
  }
  _down(e) {
    if (e.button !== 0 || e.target !== this.renderer.domElement || this.tc.axis) return;
    this._d = { x: e.clientX, y: e.clientY };
    if (this.mode === 'paint' && this.tool === 'brush') {
      const h = this.pick(e);
      if (h) { this.controls.enabled = false; this._stroke = new Map(); this._painting = true; this._brush(h); } // empty space still orbits
    }
  }
  _move(e) {
    if (this.mode !== 'paint') return;
    if (e.target !== this.renderer.domElement && !this._painting) return;
    const h = this.pick(e); this._cursor(h); this._preview(h);
    if (this._painting && h) this._brush(h);
  }
  _up(e) {
    if (this._painting) { this._painting = false; this.controls.enabled = true; this._pushUndo(this._stroke); return; }
    if (!this._d) return;
    const moved = Math.hypot(e.clientX - this._d.x, e.clientY - this._d.y) > 4; this._d = null;
    if (moved || e.target !== this.renderer.domElement) return;
    const h = this.pick(e);
    if (this.mode === 'select') {
      if (performance.now() < (this._noSelectUntil || 0) || this.tc.axis) return;
      if (!h) this.unpin();
      if (this.selectable) this.select(h ? this.topPart(h.object) : null);
    } else if (this.mode === 'paint') { if (h && this.tool === 'fill') this._fill(h); }
    else if (this.mode === 'annotate') { if (h) this._annotateAt(h); else this.unpin(); }
    else if (this.mode === 'measure' && h) this._measureAt(h);
  }

  // ---------- 2D labels projected from 3D ----------
  _label(text, pos, cls) {
    const el = Object.assign(document.createElement('div'), { className: cls, textContent: text }); this.labelsEl.appendChild(el);
    const l = { el, pos: pos.clone() }; this.labels.push(l); return l;
  }
  _unlabel(l) { l.el.remove(); this.labels = this.labels.filter(x => x !== l); }
  _updateLabels() {
    for (const l of this.labels) {
      const v = l.pos.clone().project(this.camera), vis = v.z < 1 && v.z > -1 && !l.hidden;
      l.el.style.display = vis ? '' : 'none';
      if (vis) l.el.style.transform = `translate(-50%,-50%) translate(${(v.x * .5 + .5) * this.w}px,${(-v.y * .5 + .5) * this.h}px)`;
    }
  }

  // ---------- measurement ----------
  setUnit(unit, scale) { this.unit = unit; this.unitScale = scale; this.dims.forEach(d => (d.l.el.textContent = this._dimText(d))); }
  _dimText(d) {
    if (d.type === 'angle') return `∠ ${d.v.toFixed(1)}°`;
    return `${d.type === 'thickness' ? 'T ' : ''}${(d.v * this.unitScale).toFixed(2)} ${this.unit}`;
  }
  _dimAdd(type, v, a, b) {
    const s = this.size(), g = new THREE.Group(), mk = p => { const m = new THREE.Mesh(new THREE.SphereGeometry(s * .008, 12, 8), new THREE.MeshBasicMaterial({ color: 0x22d3ee, depthTest: false })); m.position.copy(p); m.renderOrder = 10; return m; };
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]), new THREE.LineBasicMaterial({ color: 0x22d3ee, depthTest: false })); line.renderOrder = 10;
    g.add(mk(a), mk(b), line); this.overlay.add(g);
    const d = { type, v, g }; d.l = this._label('', a.clone().lerp(b, .5), 'lbl badge'); d.l.el.textContent = this._dimText(d);
    this.dims.push(d); this.emit('dims');
  }
  clearDims() { this.dims.forEach(d => { this.overlay.remove(d.g); this._unlabel(d.l); }); this.dims = []; this.mpts = []; this.emit('dims'); }
  _measureAt(h) {
    const n = h.face.normal.clone().transformDirection(h.object.matrixWorld), s = this.size();
    if (this.mtype === 'thickness') {
      const mats = []; this.eachMat(m => { mats.push([m, m.side]); m.side = THREE.DoubleSide; }); // inner walls are back faces
      this.ray.set(h.point.clone().addScaledVector(n, -s * 1e-3), n.clone().negate());
      const hit = this.ray.intersectObjects(this.meshes(), false)[0]; mats.forEach(([m, sd]) => (m.side = sd));
      if (!hit) { this.emit('toast', 'No opposite wall found'); return; }
      this._dimAdd('thickness', h.point.distanceTo(hit.point), h.point, hit.point); return;
    }
    this.mpts.push({ p: h.point.clone(), n });
    if (this.mpts.length < 2) return;
    const [a, b] = this.mpts; this.mpts = [];
    if (this.mtype === 'ruler') this._dimAdd('ruler', a.p.distanceTo(b.p), a.p, b.p);
    else this._dimAdd('angle', 180 - Math.acos(THREE.MathUtils.clamp(a.n.dot(b.n), -1, 1)) * 180 / Math.PI, a.p, b.p); // dihedral
  }

  // ---------- annotations, spotlight, tour ----------
  _annotateAt(h) {
    if (this.maxAnnotations !== null && this.annotations.length >= this.maxAnnotations) { this.emit('locked', 'annotations'); return; }
    const text = this.ask('Callout text:'); if (text === null || !text.trim()) return;
    const n = h.face.normal.clone().transformDirection(h.object.matrixWorld);
    const a = { text: text.trim().slice(0, 120), point: h.point.toArray(), normal: n.toArray(), cam: this.camera.position.toArray(), target: this.controls.target.toArray() };
    this._anchor(a, this.topPart(h.object)); this.annotations.push(a); this.buildAnnos(); this.emit('annos');
    this.record({ undo: () => { this.annotations.splice(this.annotations.indexOf(a), 1); this.unpin(); this.buildAnnos(); this.emit('annos'); },
                  redo: () => { this.annotations.push(a); this.buildAnnos(); this.emit('annos'); } });
  }
  removeAnno(i) {
    const [a] = this.annotations.splice(i, 1); this.unpin(); this.buildAnnos(); this.emit('annos');
    if (a) this.record({ undo: () => { this.annotations.splice(i, 0, a); this.buildAnnos(); this.emit('annos'); }, redo: () => { this.annotations.splice(this.annotations.indexOf(a), 1); this.buildAnnos(); this.emit('annos'); } });
  }
  // A callout is stored relative to the part it sits on (`part` id, `lp`/`ln`/`lcam`/`ltgt` in the space of that part), and the world values
  // (point, normal, cam, target) are recomputed whenever the part moves: with the gizmo, exploded view, merge or the Z-up fix.
  _sid(part) { return (part.userData.sid ??= Math.random().toString(36).slice(2, 10)); } // survives the GLB export as a node extra
  _partById(id) { return id ? this.assembly.children.find(c => c.userData.sid === id) : null; }
  _anchor(a, part) {
    part.updateWorldMatrix(true, false); const inv = part.matrixWorld.clone().invert();
    Object.assign(a, { part: this._sid(part), lp: new V3(...a.point).applyMatrix4(inv).toArray(), ln: new V3(...a.normal).transformDirection(inv).toArray(),
      lcam: new V3(...a.cam).applyMatrix4(inv).toArray(), ltgt: new V3(...a.target).applyMatrix4(inv).toArray() });
  }
  _anchorLegacy() { // callouts saved before anchoring existed: attach each to the nearest part
    this.annotations.forEach(a => {
      if (!a.point || this._partById(a.part)) return;
      const p = new V3(...a.point); let best = null, bd = Infinity;
      this.assembly.children.forEach(c => { const d = new THREE.Box3().setFromObject(c).distanceToPoint(p); if (d < bd) { bd = d; best = c; } });
      if (best) this._anchor(a, best);
    });
  }
  _follow(force) {
    this._annoM ??= [];
    this.annotations.forEach((a, i) => {
      const part = this._partById(a.part); if (!part || !a.lp) return;
      part.updateWorldMatrix(true, false); const m = part.matrixWorld;
      if (!force && this._annoM[i]?.equals(m)) return;
      this._annoM[i] = m.clone();
      const p = new V3(...a.lp).applyMatrix4(m), nrm = new V3(...a.ln).transformDirection(m);
      a.point = p.toArray(); a.normal = nrm.toArray(); a.cam = new V3(...a.lcam).applyMatrix4(m).toArray(); a.target = new V3(...a.ltgt).applyMatrix4(m).toArray();
      const v = this._annoVis?.[i]; if (v) { const c = p.clone().addScaledVector(nrm, this._annoS * .12); v.dot.position.copy(p); v.line.geometry.setFromPoints([p, c]); v.label.pos.copy(c); }
    });
  }
  buildAnnos() {
    this.annoGroup.clear(); this.annoLabels.forEach(l => this._unlabel(l)); this.annoLabels = [];
    const s = this._annoS = this.size(); this._annoVis = []; this._annoM = [];
    this.annotations.forEach((a, i) => {
      const p = new V3(...a.point), c = p.clone().addScaledVector(new V3(...a.normal), s * .12);
      const dot = new THREE.Mesh(new THREE.SphereGeometry(s * .012, 16, 12), new THREE.MeshBasicMaterial({ color: 0xfacc15, depthTest: false }));
      dot.position.copy(p); dot.renderOrder = 10;
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([p, c]), new THREE.LineBasicMaterial({ color: 0xfacc15, depthTest: false })); line.renderOrder = 10;
      this.annoGroup.add(dot, line);
      const l = this._label(`${i + 1}. ${a.text}`, c, 'lbl callout');
      l.el.onmouseenter = () => this.spot(i);
      l.el.onmouseleave = () => this.spot(this.pinned);
      l.el.onclick = () => this.focusAnno(i);
      this.annoLabels.push(l); this._annoVis.push({ dot, line, label: l });
    });
    this.annoGroup.visible = this._annoOn !== false; this.annoLabels.forEach(l => (l.hidden = this._annoOn === false));
  }
  showAnnos(on) { this._annoOn = on; this.annoGroup.visible = on; this.annoLabels.forEach(l => (l.hidden = !on)); }
  spot(i) {
    if (i == null || !this.annotations[i]) { this.dimTarget = 1; this.spotITarget = 0; return; }
    const a = this.annotations[i]; this.spotLight.position.set(...a.point).addScaledVector(new V3(...a.normal), this.size() * .08);
    this.dimTarget = .08; this.spotITarget = .5 * this.size() * this.size() / 4;
  }
  focusAnno(i) { this.pinned = i; this.spot(i); const a = this.annotations[i]; this.flyTo(a.cam, a.target); this.emit('focus', i); }
  unpin() { this.pinned = null; this.spot(null); this.emit('focus', null); }
  flyTo(pos, target, ms = 900) {
    this.fly = { t0: performance.now(), ms, p0: this.camera.position.clone(), t0v: this.controls.target.clone(), p1: new V3(...pos), t1: new V3(...target) };
  }
  tourStep(dir) {
    const n = this.annotations.length; if (!n) return;
    this.tourI = ((this.tourI ?? -1) + dir + n) % n; this.focusAnno(this.tourI);
  }
  tourPlay() { this.tourStop(); this.tourI = -1; this.tourStep(1); this._tour = setInterval(() => this.tourStep(1), 3800); this.emit('tour', true); }
  tourStop() { clearInterval(this._tour); this._tour = null; this.emit('tour', false); }

  // ---------- base / pedestal ----------
  setBase(o) {
    this.base.clear(); this._baseCfg = o; if (!o) { this.grid.position.y = 0; return; }
    const { shape, radius: r, height: h, finish } = o, b = Math.min(h * .3, r * .2);
    const g = shape === 'square' ? new THREE.BoxGeometry(r * 2, h, r * 2)
      : shape === 'beveled' ? new THREE.LatheGeometry([[0, -h / 2], [r, -h / 2], [r, h / 2 - b], [r - b, h / 2], [0, h / 2]].map(([x, y]) => new THREE.Vector2(x, y)), 64)
      : new THREE.CylinderGeometry(r, r, h, 64);
    const m = new THREE.Mesh(g, FINISHES[finish]()); m.name = 'Pedestal'; m.position.y = -h / 2;
    m.material.userData.isBaseMat = true; m.material.envMapIntensity = this.state.envI; m.castShadow = m.receiveShadow = true;
    this.base.add(m); this.grid.position.y = -h;
  }
  bakeBase() {
    const m = this.base.children[0]; if (!m) return;
    const part = new THREE.Group(); part.name = 'Pedestal'; part.add(m); this.assembly.add(part);
    this._baseCfg = null; this._clearP0(); this.emit('tree'); this.emit('change');
  }

  // ---------- optimise ----------
  async decimate(keep) {
    for (const m of this.meshesAll()) {
      const g0 = m.userData.geo0 ??= m.geometry, src = g0;
      let g = new THREE.BufferGeometry(); g.setAttribute('position', src.attributes.position); if (src.index) g.setIndex(src.index);
      g = BGU.mergeVertices(g, 1e-4 * this.size());
      const remove = Math.floor(g.attributes.position.count * (1 - keep));
      if (remove < 1) { m.geometry = g0; continue; }
      await new Promise(r => setTimeout(r)); // let the UI breathe between meshes
      const ng = new SimplifyModifier().modify(g, remove); ng.computeVertexNormals();
      if (src.attributes.color) this._reproject(src, ng);
      m.geometry = ng; m.userData.color0 = null; m.userData.adj = null;
    }
    this.emit('change');
  }
  _reproject(src, dst) { // nearest source vertex colour per new vertex, via a coarse spatial hash
    const sp = src.attributes.position, sc = src.attributes.color, dp = dst.attributes.position;
    src.computeBoundingBox(); const cell = Math.max(...src.boundingBox.getSize(new V3()).toArray()) / Math.max(4, Math.cbrt(sp.count)) || 1;
    const key = (x, y, z) => Math.floor(x / cell) + ',' + Math.floor(y / cell) + ',' + Math.floor(z / cell), grid = new Map();
    for (let i = 0; i < sp.count; i++) { const k = key(sp.getX(i), sp.getY(i), sp.getZ(i)); (grid.get(k) || grid.set(k, []).get(k)).push(i); }
    const out = new Float32Array(dp.count * 3);
    for (let i = 0; i < dp.count; i++) {
      const x = dp.getX(i), y = dp.getY(i), z = dp.getZ(i); let best = -1, bd = Infinity;
      for (let r = 0; r < 4 && best < 0; r++) for (let dx = -r; dx <= r; dx++) for (let dy = -r; dy <= r; dy++) for (let dz = -r; dz <= r; dz++) {
        const l = grid.get(key(x + dx * cell, y + dy * cell, z + dz * cell)); if (!l) continue;
        for (const j of l) { const d = (sp.getX(j) - x) ** 2 + (sp.getY(j) - y) ** 2 + (sp.getZ(j) - z) ** 2; if (d < bd) { bd = d; best = j; } }
      }
      if (best < 0) best = 0; out.set([sc.getX(best), sc.getY(best), sc.getZ(best)], 3 * i);
    }
    dst.setAttribute('color', new THREE.BufferAttribute(out, 3));
  }
  revertDecimate() { this.meshesAll().forEach(m => { if (m.userData.geo0) { m.geometry = m.userData.geo0; m.userData.adj = null; m.userData.color0 = null; } }); this.emit('change'); }

  // ---------- export / capture / config ----------
  async exportAs(fmt) {
    this.unsolo(); const e0 = this.state.explode; this.setExplode(0);
    try {
      if (fmt === 'glb') {
        const buf = await new GLTFExporter().parseAsync([this.assembly, this.base], { binary: true });
        return new Blob([buf], { type: 'model/gltf-binary' });
      }
      const root = new THREE.Group(); root.add(this.assembly.clone(), this.base.clone());
      if (fmt === 'obj') return new Blob([new OBJExporter().parse(root)], { type: 'text/plain' });
      if (fmt === 'stl') return new Blob([new STLExporter().parse(root, { binary: true })], { type: 'model/stl' });
      if (fmt === 'usdz') return new Blob([await new USDZExporter().parse(root)], { type: 'model/vnd.usdz+zip' }); // vertex colors are not part of USDZ
      throw new Error('bad format');
    } finally { this.setExplode(e0); }
  }
  _helpers(on) { [this.grid, this.selBox, this.tc, this.overlay, this.annoGroup].forEach(o => (o.visible = on && (o !== this.annoGroup || this._annoOn !== false))); }
  capturePNG() { // transparent: the backdrop is CSS behind a clear-alpha canvas
    this._helpers(false); this.renderer.render(this.scene, this.camera);
    return new Promise(res => this.renderer.domElement.toBlob(b => { this._helpers(true); res(b); }, 'image/png'));
  }
  captureWebM(sec = 6) {
    return new Promise((res, rej) => {
      if (!window.MediaRecorder) return rej(new Error('MediaRecorder not supported'));
      const mime = ['video/webm;codecs=vp9', 'video/webm'].find(t => MediaRecorder.isTypeSupported(t));
      const rec = new MediaRecorder(this.renderer.domElement.captureStream(30), { mimeType: mime }), chunks = [];
      rec.ondataavailable = e => chunks.push(e.data);
      const off = this.camera.position.clone().sub(this.controls.target), t0 = performance.now(), up = new V3(0, 1, 0);
      this._helpers(false); this.fly = null; this.controls.enabled = false;
      const step = () => {
        const t = (performance.now() - t0) / (sec * 1000);
        if (t >= 1) { rec.stop(); return; }
        this.camera.position.copy(this.controls.target).add(off.clone().applyAxisAngle(up, t * Math.PI * 2)); requestAnimationFrame(step);
      };
      rec.onstop = () => { this._helpers(true); this.controls.enabled = true; res(new Blob(chunks, { type: 'video/webm' })); };
      rec.start(); step();
    });
  }
  // ---------- 360 image pack: view-only links send pictures, never the model ----------
  // Photographs the model from `az` x `el` angles (full turn, elevations elMin..elMax degrees). The callout positions are
  // projected per frame so the image viewer can draw them without any 3D data. Returns the frames as blobs plus the index data.
  async renderTurntable({ az, el, elMin, elMax, px, quality, onProgress, cancelled }) {
    const box = new THREE.Box3().setFromObject(this.assembly).union(new THREE.Box3().setFromObject(this.base));
    if (box.isEmpty()) throw new Error('Nothing to render');
    this._follow(true);
    const r = this.renderer, cam = this.camera, tgt = this.controls.target;
    const keep = { pos: cam.position.clone(), tgt: tgt.clone(), near: cam.near, far: cam.far, pr: r.getPixelRatio() };
    const c = box.getCenter(new V3()), dist = box.getSize(new V3()).length() / 2 / Math.sin(cam.fov * Math.PI / 360) * 1.08;
    const probe = Object.assign(document.createElement('canvas'), { width: 1, height: 1 });
    const webp = (await new Promise(ok => probe.toBlob(b => ok(b?.type), 'image/webp'))) === 'image/webp'; // Safari cannot encode WebP
    const mime = webp ? 'image/webp' : 'image/jpeg';
    const elevs = Array.from({ length: el }, (_, i) => (el === 1 ? (elMin + elMax) / 2 : elMin + (elMax - elMin) * i / (el - 1)));
    const blobs = [], anno = [], thumbs = [], TS = Math.min(320, px);  // 320 px previews the viewer shows while the view is moving
    const tcv = Object.assign(document.createElement('canvas'), { width: TS, height: TS }), tctx = tcv.getContext('2d');
    const meshes = [...this.meshes(), ...(() => { const a = []; this.base.traverseVisible(o => o.isMesh && a.push(o)); return a; })()];
    const ray = new THREE.Raycaster(), eps = this.size() * .004;
    const occlude = this.triCount() * az * el * this.annotations.length <= 1.5e9; // ray tests are slow on huge models: then only the facing test is used
    r.setAnimationLoop(null); this.fly = null; this.controls.enabled = false; this._helpers(false);
    this.dim = 1; this.spotLight.intensity = 0; this.lights.forEach(l => (l.intensity = l.userData.base * this.state.light));
    if (!webp) r.setClearColor(new THREE.Color(BACKDROP_FLAT[this.state.backdrop] || '#0f172a'), 1); // JPEG has no transparency
    r.setPixelRatio(1); r.setSize(px, px, false); cam.aspect = 1; cam.near = dist / 100; cam.far = dist * 100; cam.updateProjectionMatrix();
    try {
      for (let e = 0; e < el; e++) for (let a = 0; a < az; a++) {
        if (cancelled?.()) throw new Error('Cancelled');
        const th = a / az * Math.PI * 2, ph = elevs[e] * Math.PI / 180;
        cam.position.set(c.x + dist * Math.cos(ph) * Math.sin(th), c.y + dist * Math.sin(ph), c.z + dist * Math.cos(ph) * Math.cos(th));
        tgt.copy(c); cam.lookAt(c); cam.updateMatrixWorld();
        r.render(this.scene, cam);
        blobs.push(await new Promise(ok => r.domElement.toBlob(ok, mime, quality)));
        tctx.clearRect(0, 0, TS, TS); tctx.drawImage(r.domElement, 0, 0, TS, TS);
        thumbs.push(await new Promise(ok => tcv.toBlob(ok, mime, .72)));
        anno.push(this.annotations.map(n => { // [x, y, visibility 0..1]: 0 when on the far side, behind other geometry or off screen
          const p = new V3(...n.point), toCam = cam.position.clone().sub(p), d = toCam.length(), v = toCam.clone().divideScalar(d), q = p.clone().project(cam);
          let f = Math.max(0, v.dot(new V3(...n.normal)));                                    // how squarely the surface faces the camera
          if (q.z >= 1 || Math.abs(q.x) > 1 || Math.abs(q.y) > 1) f = 0;
          if (f > 0 && occlude) { ray.set(p.clone().addScaledVector(v, eps), v); ray.far = d - eps; if (ray.intersectObjects(meshes, false).length) f = 0; }
          return [+((q.x + 1) / 2).toFixed(3), +((1 - q.y) / 2).toFixed(3), +f.toFixed(2)];
        }));
        onProgress?.((e * az + a + 1) / (az * el), blobs[blobs.length - 1]);
      }
    } finally {
      cam.position.copy(keep.pos); tgt.copy(keep.tgt); cam.near = keep.near; cam.far = keep.far;
      r.setClearColor(0x000000, 0); r.setPixelRatio(keep.pr); this._resize(); this._helpers(true); this.controls.enabled = true;
      r.setAnimationLoop(() => this._loop());
    }
    return { blobs, thumbs, anno, mime, w: px, h: px, az, el, elevs };
  }
  getConfig() {
    this._follow(true); const s = this.state;
    return {
      camera: { pos: this.camera.position.toArray(), target: this.controls.target.toArray() },
      light: s.light, lightPreset: this.preset, shadows: this.floor.visible, backdrop: s.backdrop, grid: this.grid.visible,
      material: s.touched ? { rough: s.rough, metal: s.metal } : null,
      env: { preset: s.env, intensity: s.envI }, explode: s.explode, units: { unit: this.unit, scale: this.unitScale },
      annotations: this.annotations,
    };
  }
  applyConfig(c) {
    if (!c) return;
    this.setBackdrop(c.backdrop || 'midnight'); this.setLight(c.light ?? 1); this.grid.visible = c.grid !== false;
    if (LIGHT_PRESETS[c.lightPreset]) this.setLightPreset(c.lightPreset);
    if (c.shadows === false) this.setShadows(false);
    if (c.material) this.setMaterial(c.material.rough, c.material.metal);
    if (c.env && c.env.preset !== 'custom') this.setEnv(c.env.preset, c.env.intensity); else if (c.env) this.state.envI = c.env.intensity;
    if (c.units) this.setUnit(c.units.unit, c.units.scale);
    this.annotations = Array.isArray(c.annotations) ? c.annotations : []; this._anchorLegacy(); this.buildAnnos(); this._follow(true);
    if (c.camera) { this.camera.position.set(...c.camera.pos); this.controls.target.set(...c.camera.target); this.controls.update(); }
    this.emit('config');
  }

  // ---------- frame loop ----------
  _loop() {
    const now = performance.now();
    if (this.fly) {
      const k = Math.min(1, (now - this.fly.t0) / this.fly.ms), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      this.camera.position.lerpVectors(this.fly.p0, this.fly.p1, e); this.controls.target.lerpVectors(this.fly.t0v, this.fly.t1, e);
      if (k >= 1) this.fly = null;
    }
    this.dim += (this.dimTarget - this.dim) * .12; this.spotI += (this.spotITarget - this.spotI) * .12;
    this.spotLight.intensity = this.spotI;
    this.lights.forEach(l => (l.intensity = l.userData.base * this.state.light * this.dim));
    this.floor.position.y = this.grid.position.y + .002;
    if (this.selected) this.selBox.setFromObject(this.selected);
    if (this.annotations.length) this._follow();
    this.controls.update(); this._updateLabels();
    this.renderer.render(this.scene, this.camera);
  }
}
