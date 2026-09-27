import * as THREE from "three";
import { version } from "../../plugin.json";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";

document.getElementById("forge-version").textContent = `Forge ${version}`;

const CHANNEL = "uefn-plugin-ui";
const PRIMS = ["box", "rounded_box", "plane", "sphere", "cylinder", "cone", "torus", "capsule", "wedge", "stairs", "arch"];

function call(method, params) {
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      window.removeEventListener("message", onMsg);
      reject(new Error("Timed out talking to host"));
    }, 120000);
    function onMsg(ev) {
      const d = ev.data;
      if (ev.source !== parent || !d || d.channel !== CHANNEL || d.id !== id) return;
      clearTimeout(timer);
      window.removeEventListener("message", onMsg);
      if (d.ok) resolve(d.result);
      else reject(new Error(d.error || "bridge error"));
    }
    window.addEventListener("message", onMsg);
    parent.postMessage({ channel: CHANNEL, id, method, params: params || {} }, "*");
  });
}

function rpc(method, params) {
  return call("plugin.call", { method, params: params || {} });
}

const state = {
  name: "",
  rev: -1,
  geom: -1,
  payload: null,
  mode: "object",
  selectMode: "face",
  gizmo: "translate",
  tab: "props",
  collapsed: new Set(),
  search: "",
  pending: 0,
  epoch: 0,
  nodeMap: new Map(),
  locked: new Set(),
  follow: false,
  dragging: false,
  flying: false,
  polling: false,
  batches: [],
  slots: new Map(),
  parentOf: new Map(),
  pick: null,
  faceLines: null,
  batchEdit: false,
  editId: "",
  flashIds: [],
  picked: new Set(),
  tinted: new Set(),
  matId: "",
};

const CHUNK = 16384;
const WHITE = new THREE.Color(1, 1, 1);
const TINT = new THREE.Color();
const FLASH = new THREE.Color();
const _pos = new THREE.Vector3();
const _quat = new THREE.Quaternion();
const _scl = new THREE.Vector3();
const _euler = new THREE.Euler();
const _m = new THREE.Matrix4();
const _delta = new THREE.Matrix4();
const _inv = new THREE.Matrix4();
const _ident = new THREE.Matrix4();
const geoCache = new Map();
const matCache = new Map();

const host = document.getElementById("view");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(1);
host.appendChild(renderer.domElement);
const scene3 = new THREE.Scene();
scene3.background = new THREE.Color();
const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 200000);
camera.up.set(0, 0, 1);
camera.position.set(1200, -1600, 900);
const orbit = new OrbitControls(camera, renderer.domElement);
orbit.target.set(0, 0, 256);
orbit.mouseButtons.LEFT = -1;
orbit.mouseButtons.MIDDLE = THREE.MOUSE.PAN;
orbit.mouseButtons.RIGHT = -1;
orbit.update();
scene3.add(new THREE.AmbientLight(WHITE, 0.55));
const sun = new THREE.DirectionalLight(WHITE, 1.1);
sun.position.set(400, -800, 1200);
scene3.add(sun);
function fortniteGrid(cell, tiles, color, opacity) {
  const g = new THREE.GridHelper(cell * tiles, tiles, color, color);
  g.rotation.x = -Math.PI / 2;
  for (const m of [].concat(g.material)) {
    m.transparent = true;
    m.opacity = opacity;
  }
  return g;
}
const minorGrid = fortniteGrid(128, 192, WHITE, 0.35);
const majorGrid = fortniteGrid(512, 48, WHITE, 0.9);
scene3.add(minorGrid, majorGrid);
const content = new THREE.Group();
scene3.add(content);
const gizmo = new TransformControls(camera, renderer.domElement);
const gizmoHelper = gizmo.getHelper ? gizmo.getHelper() : gizmo;
scene3.add(gizmoHelper);
gizmo.addEventListener("dragging-changed", (ev) => {
  state.dragging = ev.value;
  orbit.enabled = !ev.value && !state.flying;
  if (ev.value) {
    const slots = state.slots.get(pivot.userData.id) || [];
    state.dragSlots = slots;
    state.dragBase = slots.map((slot) => {
      const m = new THREE.Matrix4();
      slot.mesh.getMatrixAt(slot.index, m);
      return m;
    });
    pivot.updateMatrix();
    state.dragStart = pivot.matrix.clone();
    for (const slot of slots) slot.mesh.frustumCulled = false;
  } else {
    for (const mesh of new Set((state.dragSlots || []).map(slot => slot.mesh))) {
      mesh.frustumCulled = true;
      mesh.computeBoundingBox();
      mesh.computeBoundingSphere();
    }
    commitXform();
  }
});
gizmo.addEventListener("objectChange", () => {
  if (!state.dragging || !state.dragBase) return;
  pivot.updateMatrix();
  _delta.copy(pivot.matrix).multiply(_inv.copy(state.dragStart).invert());
  const touched = new Set();
  state.dragSlots.forEach((slot, i) => {
    _m.copy(state.dragBase[i]).premultiply(_delta);
    slot.mesh.setMatrixAt(slot.index, _m);
    touched.add(slot.mesh);
  });
  for (const mesh of touched) mesh.instanceMatrix.needsUpdate = true;
});

let flashUntil = 0;
const pivot = new THREE.Object3D();
content.add(pivot);

function resize() {
  const w = host.clientWidth || 400;
  const h = host.clientHeight || 300;
  renderer.setSize(w, h, false);
  camera.aspect = w / Math.max(1, h);
  camera.updateProjectionMatrix();
}
new ResizeObserver(() => resize()).observe(host);
resize();

function colorOf(matId, materials) {
  const mat = (materials || []).find((m) => m.id === matId) || (materials || [])[0];
  const c = (mat && mat.base_color) || [0.7, 0.7, 0.75];
  return new THREE.Color(c[0], c[1], c[2]);
}

function composeRow(row, target) {
  _euler.set(
    THREE.MathUtils.degToRad(row[6] || 0),
    THREE.MathUtils.degToRad(row[7] || 0),
    THREE.MathUtils.degToRad(row[8] || 0),
    "ZYX",
  );
  _pos.set(row[3] || 0, row[4] || 0, row[5] || 0);
  _quat.setFromEuler(_euler);
  _scl.set(row[9] ?? 1, row[10] ?? 1, row[11] ?? 1);
  return target.compose(_pos, _quat, _scl);
}

function b64buf(text) {
  const bin = atob(text);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

function geometryFor(proto) {
  let geo = geoCache.get(proto.id);
  if (geo && geo.userData.source.pb === proto.pb && geo.userData.source.ib === proto.ib && geo.userData.source.fb === proto.fb && geo.userData.source.smooth === proto.smooth) return geo;
  if (geo) geo.dispose();
  geo = new THREE.BufferGeometry();
  if (proto.pb) {
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(b64buf(proto.pb)), 3));
    geo.setIndex(new THREE.BufferAttribute(new Uint32Array(b64buf(proto.ib)), 1));
    if (proto.fb) geo.userData.faceIds = new Uint32Array(b64buf(proto.fb));
    if (proto.smooth) geo.computeVertexNormals();
  } else {
    geo.setAttribute("position", new THREE.Float32BufferAttribute(proto.positions, 3));
    if (proto.normals) geo.setAttribute("normal", new THREE.Float32BufferAttribute(proto.normals, 3));
    geo.setIndex(proto.indices);
    geo.userData.faceIds = proto.face_ids || [];
  }
  geo.userData.source = proto;
  geoCache.set(proto.id, geo);
  return geo;
}

function materialFor(matId, materials, smooth) {
  const key = (matId || "") + (smooth ? "|s" : "");
  const color = colorOf(matId, materials);
  let mat = matCache.get(key);
  if (!mat) {
    mat = new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.05, flatShading: !smooth });
    matCache.set(key, mat);
  }
  mat.color.copy(color);
  const authored = (materials || []).find(m => m.id === matId);
  mat.roughness = authored?.roughness ?? 0.5;
  mat.metalness = authored?.metallic ?? 0;
  return mat;
}

function clearView() {
  gizmo.detach();
  for (const mesh of state.batches) { mesh.removeFromParent(); mesh.dispose(); }
  state.batches = [];
  state.slots = new Map();
  state.parentOf = new Map();
  if (state.pick) {
    state.pick.removeFromParent();
    state.pick = null;
  }
  if (state.faceLines) {
    state.faceLines.geometry.dispose();
    state.faceLines.material.dispose();
    state.faceLines.removeFromParent();
    state.faceLines = null;
  }
}

function tintSlot(id, color) {
  const slots = state.slots.get(id);
  if (!slots) return;
  for (const slot of slots) {
    slot.mesh.setColorAt(slot.index, color);
    if (slot.mesh.instanceColor) slot.mesh.instanceColor.needsUpdate = true;
  }
}

function markSelection(force) {
  const want = new Set(state.mode === "object" ? state.picked : []);
  const sel = state.payload && state.payload.selection && state.payload.selection.object;
  if (sel && state.mode === "object") want.add(sel);
  const prev = force ? new Set() : state.tinted;
  for (const id of prev) if (!want.has(id)) tintSlot(id, WHITE);
  for (const id of want) if (force || !prev.has(id)) tintSlot(id, TINT);
  state.tinted = want;
}

function paintColors() {
  for (const batch of state.batches) {
    for (let i = 0; i < batch.count; i++) batch.setColorAt(i, WHITE);
    if (batch.instanceColor) batch.instanceColor.needsUpdate = true;
  }
  markSelection(true);
  for (const id of state.flashIds || []) tintSlot(id, FLASH);
}

function paintFaceLines() {
  if (state.faceLines) {
    state.faceLines.geometry.dispose();
    state.faceLines.material.dispose();
    state.faceLines.removeFromParent();
    state.faceLines = null;
  }
  const picked = new Set((state.payload && state.payload.selection && state.payload.selection.faces) || []);
  if (!state.pick || state.mode !== "edit") return;
  const geo = state.pick.geometry;
  const pos = geo.attributes.position.array;
  const index = geo.index.array;
  const ids = state.pick.userData.faceIds || [];
  const pts = [];
  for (let t = 0; t < ids.length; t++) {
    if (!picked.has(ids[t])) continue;
    const tri = [index[t * 3], index[t * 3 + 1], index[t * 3 + 2]];
    for (let e = 0; e < 3; e++) {
      const i0 = tri[e] * 3;
      const i1 = tri[(e + 1) % 3] * 3;
      pts.push(pos[i0], pos[i0 + 1], pos[i0 + 2], pos[i1], pos[i1 + 1], pos[i1 + 2]);
    }
  }
  const selection = state.payload.selection || {};
  for (const [a,b] of selection.edges || []) {
    if (a < 0 || b < 0 || a*3 >= pos.length || b*3 >= pos.length) continue;
    pts.push(pos[a*3], pos[a*3+1], pos[a*3+2], pos[b*3], pos[b*3+1], pos[b*3+2]);
  }
  for (const id of selection.verts || []) {
    if (id < 0 || id*3 >= pos.length) continue;
    const point = [pos[id*3],pos[id*3+1],pos[id*3+2]];
    for (let axis=0;axis<3;axis++) { const a=point.slice(), b=point.slice(); a[axis]-=6; b[axis]+=6; pts.push(...a,...b); }
  }
  if (!pts.length) return;
  const lines = new THREE.BufferGeometry();
  lines.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  state.faceLines = new THREE.LineSegments(lines, new THREE.LineBasicMaterial({ color: TINT }));
  state.faceLines.matrix.copy(state.pick.matrix);
  state.faceLines.matrixAutoUpdate = false;
  content.add(state.faceLines);
}

function buildBatches() {
  const payload = state.payload;
  clearView();
  if (!payload) return;
  const live = new Set();
  const protos = new Map();
  for (const proto of payload.prototypes || []) {
    live.add(proto.id);
    protos.set(proto.id, proto);
    geometryFor(proto);
  }
  for (const [id, geo] of geoCache) {
    if (!live.has(id)) {
      geo.dispose();
      geoCache.delete(id);
    }
  }
  const byId = new Map((payload.nodes || []).map((n) => [n.id, n]));
  state.nodeMap = byId;
  state.locked = new Set();
  const lockedLayers = new Set((payload.layers || []).filter(l => l.locked).map(l => l.id));
  for (const n of byId.values()) {
    let current = n;
    const seen = new Set();
    while (current && !seen.has(current.id)) {
      seen.add(current.id);
      if (current.locked || lockedLayers.has(current.layer)) { state.locked.add(n.id); break; }
      current = byId.get(current.parent);
    }
  }
  const worldCache = new Map();
  const visiting = new Set();
  function world(id) {
    if (!id || !byId.has(id)) return _ident;
    if (worldCache.has(id)) return worldCache.get(id);
    if (visiting.has(id)) throw new Error("Scene hierarchy contains a cycle");
    visiting.add(id);
    const n = byId.get(id);
    const t = n.transform || { pos: [0, 0, 0], rot: [0, 0, 0], scale: [1, 1, 1] };
    const local = composeRow([0, "", "", t.pos[0], t.pos[1], t.pos[2], t.rot[0], t.rot[1], t.rot[2], t.scale[0], t.scale[1], t.scale[2]], new THREE.Matrix4());
    const m = new THREE.Matrix4().multiplyMatrices(world(n.parent), local);
    visiting.delete(id);
    worldCache.set(id, m);
    return m;
  }
  const sel = (payload.selection && payload.selection.object) || "";
  const editing = state.mode === "edit" && !!sel;
  const layerOff = new Set((payload.layers || []).filter((l) => l.visible === false).map((l) => l.id));
  function culled(id) {
    let n = byId.get(id);
    const seen = new Set();
    while (n && !seen.has(n.id)) {
      seen.add(n.id);
      if (n.visible === false || layerOff.has(n.layer)) return true;
      n = n.parent ? byId.get(n.parent) : null;
    }
    return false;
  }
  let pulled = null;
  const groups = new Map();
  for (const row of payload.instances || []) {
    if (culled(row[0])) continue;
    if (editing && !pulled && row[0] === sel) {
      pulled = row;
      continue;
    }
    const gk = `${row[1]}\0${row[12] || ""}`;
    let list = groups.get(gk);
    if (!list) groups.set(gk, (list = []));
    list.push(row);
  }
  const local = new THREE.Matrix4();
  for (const rows of groups.values()) {
    const proto = protos.get(rows[0][1]);
    if (!proto) continue;
    const geo = geometryFor(proto);
    const mat = materialFor(rows[0][12], payload.materials, !!proto.smooth);
    for (let c = 0; c < rows.length; c += CHUNK) {
      const slice = rows.slice(c, c + CHUNK);
      const batch = new THREE.InstancedMesh(geo, mat, slice.length);
      batch.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      batch.userData.ids = new Array(slice.length);
      for (let i = 0; i < slice.length; i++) {
        const row = slice[i];
        composeRow(row, local);
        _m.multiplyMatrices(world(row[2]), local);
        batch.setMatrixAt(i, _m);
        batch.userData.ids[i] = row[0];
        if (!state.parentOf.has(row[0])) state.parentOf.set(row[0], world(row[2]));
        let slots = state.slots.get(row[0]);
        if (!slots) state.slots.set(row[0], (slots = []));
        slots.push({ mesh: batch, index: i });
      }
      batch.computeBoundingBox();
      batch.computeBoundingSphere();
      content.add(batch);
      state.batches.push(batch);
    }
  }
  if (pulled) {
    const proto = protos.get(pulled[1]);
    if (proto) {
      const mesh = new THREE.Mesh(geometryFor(proto), materialFor(pulled[12], payload.materials, !!proto.smooth));
      composeRow(pulled, local);
      mesh.matrix.multiplyMatrices(world(pulled[2]), local);
      mesh.matrixAutoUpdate = false;
      mesh.userData = { id: pulled[0], faceIds: geometryFor(proto).userData.faceIds || [], matId: pulled[12] || "" };
      content.add(mesh);
      state.pick = mesh;
      state.parentOf.set(pulled[0], world(pulled[2]));
    }
  }
  // Groups point at their descendants' existing instance slots; no mesh copies.
  for (const [id, slots] of [...state.slots]) {
    let parent = byId.get(id)?.parent;
    const seen = new Set();
    while (parent && !seen.has(parent)) {
      seen.add(parent);
      if (!state.slots.has(parent)) state.slots.set(parent, []);
      state.slots.get(parent).push(...slots);
      parent = byId.get(parent)?.parent;
    }
  }
  state.world = worldCache;
  for (const n of byId.values()) world(n.id);
  state.batchEdit = state.mode === "edit";
  state.editId = sel;
  state.tinted = new Set();
  markSelection(true);
  paintFaceLines();
}

function applySnap() {
  const grid = Number(document.getElementById("snap").value) || 0;
  const rot = Number(document.getElementById("rot-snap").value) || 0;
  const scale = Number(document.getElementById("scale-snap").value) || 0;
  gizmo.setTranslationSnap(grid || null);
  gizmo.setRotationSnap(rot ? THREE.MathUtils.degToRad(rot) : null);
  gizmo.setScaleSnap(scale || null);
}

function setWidget(mode) {
  state.gizmo = mode;
  if (mode === "select") gizmo.detach();
  else {
    gizmo.setMode(mode);
    attachGizmo();
  }
  syncChrome();
}

function attachGizmo() {
  const sel = (state.payload && state.payload.selection && state.payload.selection.object) || "";
  const slots = state.slots.get(sel);
  if (state.gizmo === "select" || state.mode !== "object" || !slots || !slots.length) {
    gizmo.detach();
    return;
  }
  if (state.locked.has(sel)) { gizmo.detach(); return; }
  _m.copy(state.world.get(sel));
  _m.decompose(pivot.position, pivot.quaternion, pivot.scale);
  pivot.updateMatrix();
  pivot.userData.id = sel;
  gizmo.attach(pivot);
  gizmo.setMode(state.gizmo);
  applySnap();
}

function syncSelection() {
  const sel = (state.payload && state.payload.selection && state.payload.selection.object) || "";
  const editing = state.mode === "edit";
  if (editing !== state.batchEdit || (editing && sel !== state.editId)) buildBatches();
  else paintFaceLines();
  if (sel && !state.picked.has(sel)) state.picked = new Set([sel]);
  if (!sel) state.picked.clear();
  markSelection(false);
  attachGizmo();
  paintTree();
  paintPanels(false);
  syncChrome();
}

function rebuild(payload) {
  state.payload = payload;
  const selected = payload.selection?.object;
  if (selected && !state.picked.has(selected)) state.picked = new Set([selected]);
  if (!selected) state.picked.clear();
  state.mode = payload.mode || "object";
  state.selectMode = payload.select_mode || "face";
  state.geom = payload.geom || 0;
  state.flashIds = (payload.flash && payload.flash.ids) || [];
  buildBatches();
  markSelection(true);
  attachGizmo();
  if (state.flashIds.length) {
    flashUntil = performance.now() + 700;
    document.getElementById("ai").hidden = false;
    paintColors();
    if (state.follow) {
      const slots = state.slots.get(state.flashIds[0]);
      if (slots && slots[0]) {
        slots[0].mesh.getMatrixAt(slots[0].index, _m);
        orbit.target.setFromMatrixPosition(_m);
      }
    }
  }
  paintPanels();
  syncChrome();
}

async function commitXform() {
  const id = pivot.userData.id;
  const slots = state.dragSlots;
  if (!id || !slots || !slots.length || !state.dragBase) return;
  _m.copy(pivot.matrix);
  const parent = state.world.get(nodeById(id)?.parent);
  if (parent && parent !== _ident) _m.premultiply(_inv.copy(parent).invert());
  _m.decompose(_pos, _quat, _scl);
  _euler.setFromQuaternion(_quat, "ZYX");
  state.dragBase = null;
  await op("edit", {
    id,
    pos: [_pos.x, _pos.y, _pos.z],
    rot: [THREE.MathUtils.radToDeg(_euler.x), THREE.MathUtils.radToDeg(_euler.y), THREE.MathUtils.radToDeg(_euler.z)],
    scale: [_scl.x, _scl.y, _scl.z],
  });
}

const held = new Set();
let camSpeed = 4;
let flyPitch = 0;
const _fwd = new THREE.Vector3();
const _right = new THREE.Vector3();
const _worldUp = new THREE.Vector3(0, 0, 1);

function stepFly(dt) {
  if (!state.flying) return;
  camera.updateMatrixWorld(true);
  camera.getWorldDirection(_fwd);
  _right.setFromMatrixColumn(camera.matrixWorld, 0);
  let x = 0, y = 0, z = 0;
  if (held.has("w")) { x += _fwd.x; y += _fwd.y; z += _fwd.z; }
  if (held.has("s")) { x -= _fwd.x; y -= _fwd.y; z -= _fwd.z; }
  if (held.has("d")) { x += _right.x; y += _right.y; z += _right.z; }
  if (held.has("a")) { x -= _right.x; y -= _right.y; z -= _right.z; }
  if (held.has("e")) z += 1;
  if (held.has("q")) z -= 1;
  const len = Math.hypot(x, y, z);
  if (len > 1e-6) {
    const speed = 400 * 2 ** (camSpeed - 1) * dt;
    camera.position.x += (x / len) * speed;
    camera.position.y += (y / len) * speed;
    camera.position.z += (z / len) * speed;
  }
  camera.getWorldDirection(_fwd);
  const radius = Math.max(256, camera.position.distanceTo(orbit.target));
  orbit.target.copy(camera.position).addScaledVector(_fwd, radius);
}

function look(dx, dy) {
  if (!dx && !dy) return;
  const next = Math.max(-1.45, Math.min(1.45, flyPitch - dy * 0.003));
  const applied = next - flyPitch;
  flyPitch = next;
  camera.quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(_worldUp, -dx * 0.003));
  _right.set(1, 0, 0).applyQuaternion(camera.quaternion);
  camera.quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(_right, applied));
}

let rightDown = false;

function beginFly(ev) {
  if (state.dragging || state.flying) return;
  state.flying = true;
  rightDown = true;
  orbit.enabled = false;
  orbit.enableZoom = false;
  camera.getWorldDirection(_fwd);
  flyPitch = Math.asin(THREE.MathUtils.clamp(_fwd.z, -1, 1));
  renderer.domElement.style.cursor = "none";
  try { renderer.domElement.setPointerCapture(ev.pointerId); } catch { /* already captured */ }
}

function endFly(ev) {
  rightDown = false;
  held.clear();
  if (!state.flying) return;
  state.flying = false;
  orbit.enableZoom = true;
  orbit.enabled = !state.dragging;
  renderer.domElement.style.cursor = "";
  if (ev && renderer.domElement.hasPointerCapture?.(ev.pointerId)) {
    try { renderer.domElement.releasePointerCapture(ev.pointerId); } catch { /* released */ }
  }
}

let lastFrame = performance.now();
function animate() {
  requestAnimationFrame(animate);
  const now = performance.now();
  stepFly(Math.min(0.05, (now - lastFrame) / 1000));
  lastFrame = now;
  if (flashUntil && now > flashUntil) {
    flashUntil = 0;
    state.flashIds = [];
    document.getElementById("ai").hidden = true;
    paintColors();
  }
  renderer.render(scene3, camera);
}
animate();

renderer.domElement.addEventListener("contextmenu", (ev) => ev.preventDefault());
renderer.domElement.addEventListener("pointermove", (ev) => {
  if (state.flying && (ev.buttons & 2) === 0) { endFly(ev); return; }
  if (!state.flying) return;
  const dx = ev.movementX || 0;
  const dy = ev.movementY || 0;
  if (!dx && !dy) return;
  look(dx, dy);
});
renderer.domElement.addEventListener("pointerup", (ev) => {
  if (ev.button === 2) endFly(ev);
});
renderer.domElement.addEventListener("pointercancel", (ev) => endFly(ev));
renderer.domElement.addEventListener("lostpointercapture", (ev) => {
  if (state.flying) endFly(ev);
});
renderer.domElement.addEventListener("wheel", (ev) => {
  if (!state.flying) return;
  ev.preventDefault();
  ev.stopImmediatePropagation();
  camSpeed = Math.max(1, Math.min(8, camSpeed + (ev.deltaY < 0 ? 1 : -1)));
  const chip = document.getElementById("cam-speed");
  if (chip) chip.textContent = "Speed " + camSpeed;
}, { capture: true, passive: false });

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const _pickRay = new THREE.Ray();
const _pickHit = new THREE.Vector3();
const _pickInv = new THREE.Matrix4();

function pickBounds(ray) {
  let best = null;
  let bestD = Infinity;
  for (const batch of state.batches) {
    const geo = batch.geometry;
    if (!geo.boundingBox) geo.computeBoundingBox();
    const box = geo.boundingBox;
    const ids = batch.userData.ids;
    if (batch.boundingBox && !ray.intersectsBox(batch.boundingBox)) continue;
    for (let i = 0; i < ids.length; i++) {
      if (state.locked.has(ids[i])) continue;
      batch.getMatrixAt(i, _m);
      if (Math.abs(_m.determinant()) < 1e-12) continue;
      _pickInv.copy(_m).invert();
      _pickRay.copy(ray).applyMatrix4(_pickInv);
      if (!_pickRay.intersectBox(box, _pickHit)) continue;
      _pickHit.applyMatrix4(_m);
      const dist = _pickHit.distanceToSquared(ray.origin);
      if (dist < bestD) {
        bestD = dist;
        best = ids[i];
      }
    }
  }
  return best;
}

function choose(id, additive, faces, elements) {
  if (!state.payload) return;
  if ((faces || elements) && state.mode === "edit") {
    const previous = state.payload.selection?.faces || [];
    if (faces) faces = additive ? [...new Set([...previous, ...faces])] : faces;
    state.picked = new Set([id]);
  } else rememberPick(id, additive);
  const primary = id && state.picked.has(id) ? id : ([...state.picked].pop() || null);
  const oldSelection = state.payload.selection;
  const old = oldSelection?.object;
  state.payload.selection = { object: primary, verts: [], edges: [], faces: faces || (old === primary ? state.payload.selection?.faces || [] : []) };
  if (elements) for (const [key, values] of Object.entries(elements)) {
    state.payload.selection[key] = additive ? [...new Map([...(oldSelection?.[key] || []), ...values].map(v => [JSON.stringify(v), v])).values()] : values;
  }
  syncSelection();
  op("select", { ...state.payload.selection });
}

renderer.domElement.addEventListener("pointerdown", (ev) => {
  if (ev.button === 2) { beginFly(ev); return; }
  if (ev.button !== 0 || state.dragging || state.flying || gizmo.axis) return;
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const additive = ev.shiftKey || ev.ctrlKey || ev.metaKey;
  if (state.mode === "edit" && state.pick) {
    const hits = raycaster.intersectObject(state.pick, false);
    if (!hits.length) { choose(null, false); return; }
    const face = (state.pick.userData.faceIds || [])[hits[0].faceIndex];
    if (face == null) return;
    if (state.selectMode === "face") choose(state.pick.userData.id, additive, [face]);
    else {
      const geo = state.pick.geometry;
      const point = state.pick.worldToLocal(hits[0].point.clone());
      const edges = new Map(), verts = new Set();
      for (let i = 0; i < geo.userData.faceIds.length; i++) {
        if (geo.userData.faceIds[i] !== face) continue;
        const tri = [geo.index.getX(i*3), geo.index.getX(i*3+1), geo.index.getX(i*3+2)];
        for (let j = 0; j < 3; j++) {
          verts.add(tri[j]);
          const edge = [tri[j], tri[(j+1)%3]].sort((a,b) => a-b);
          const key = edge.join(",");
          const entry = edges.get(key);
          edges.set(key, { edge, count: (entry?.count || 0) + 1 });
        }
      }
      const vertex = id => new THREE.Vector3().fromBufferAttribute(geo.attributes.position, id);
      if (state.selectMode === "vert") {
        const id = [...verts].sort((a,b) => vertex(a).distanceToSquared(point) - vertex(b).distanceToSquared(point))[0];
        choose(state.pick.userData.id, additive, undefined, { verts: [id] });
      } else {
        const candidates = [...edges.values()].filter(e => e.count === 1);
        const distance = e => new THREE.Line3(vertex(e.edge[0]), vertex(e.edge[1])).closestPointToPoint(point, true, new THREE.Vector3()).distanceToSquared(point);
        candidates.sort((a,b) => distance(a)-distance(b));
        if (candidates.length) choose(state.pick.userData.id, additive, undefined, { edges: [candidates[0].edge] });
      }
    }
    return;
  }
  choose(pickBounds(raycaster.ray), additive);
});

let operations = Promise.resolve();
function op(name, params) {
  const sceneName = state.name;
  const epoch = ++state.epoch;
  state.pending++;
  const run = async () => {
    try {
      if (name !== "select") setStatus("Working…");
      const result = await rpc("forge_call", { op: name, name: sceneName, params: params || {}, actor: "user" });
      if (result?.ok === false) throw new Error(result.error || "Operation failed");
      if (sceneName !== state.name) return result;
      if (name === "select") {
        if (epoch === state.epoch && state.payload) {
          state.payload.selection = result.selection;
          state.mode = result.mode || state.mode;
          state.selectMode = result.select_mode || state.selectMode;
          state.rev = result.rev;
          syncSelection();
        }
      } else {
        if (result.material?.id) state.matId = result.material.id;
        state.rev = -1;
        if (name === "scene_open" || name === "scene_import_blend") resetScene();
        setStatus(result.summary || "Ready");
      }
      return result;
    } catch (err) {
      setStatus(err.message || String(err), true);
      state.rev = -1;
      return { ok: false, error: err.message || String(err) };
    } finally { state.pending--; }
  };
  const task = operations.then(run);
  operations = task.catch(() => {});
  return task;
}

function resetScene() {
  state.epoch++;
  state.rev = -1;
  state.geom = -1;
  state.payload = null;
  state.picked.clear();
  state.collapsed.clear();
  state.search = "";
  endFly();
  clearView();
  for (const geo of geoCache.values()) geo.dispose();
  geoCache.clear();
  for (const mat of matCache.values()) mat.dispose();
  matCache.clear();
}

const LOAD_SCENE = [
  ["read", "Read the scene"],
  ["pack", "Pack the meshes"],
  ["smooth", "Smooth the round parts"],
  ["place", "Place the pieces"],
];
const LOAD_BLEND = [
  ["export", "Export from Blender"],
  ["pack", "Pack the meshes"],
  ["smooth", "Smooth the round parts"],
  ["place", "Place the pieces"],
];

function showLoading(name, steps) {
  const el = document.getElementById("loading");
  const label = document.getElementById("loading-name");
  const list = document.getElementById("loading-steps");
  if (label) label.textContent = "Loading " + (name || "scene");
  if (list) {
    list.innerHTML = "";
    for (const [id, text] of steps || LOAD_SCENE) {
      const li = document.createElement("li");
      li.dataset.step = id;
      li.textContent = text;
      list.appendChild(li);
    }
  }
  if (el) el.hidden = false;
  loadStep((steps || LOAD_SCENE)[0][0], "");
}

function loadStep(id, why) {
  const list = document.getElementById("loading-steps");
  if (list) {
    let seen = false;
    for (const li of list.children) {
      if (li.dataset.step === id) {
        li.className = "on";
        seen = true;
      } else li.className = seen ? "" : "done";
    }
  }
  const whyEl = document.getElementById("loading-why");
  if (whyEl) whyEl.textContent = why || "";
}

function frame() {
  return new Promise((resolve) => requestAnimationFrame(resolve));
}

async function prepareGeometry(payload) {
  const protos = payload.prototypes || [];
  const smoothN = protos.reduce((n, proto) => n + (proto.smooth ? 1 : 0), 0);
  loadStep("smooth", smoothN
    ? `Building normals for ${smoothN.toLocaleString()} smooth meshes so circles match Blender. Flat pieces skip this. One dense mesh can hold this step by itself.`
    : "Nothing in this scene is smooth-shaded, so this step is quick.");
  await frame();
  for (let i = 0; i < protos.length; i++) {
    geometryFor(protos[i]);
    if (i % 12 === 11) {
      loadStep("smooth", `Mesh ${(i + 1).toLocaleString()} of ${protos.length.toLocaleString()}. Smooth meshes take longer because their normals are built here.`);
      await frame();
    }
  }
}

function hideLoading() {
  const el = document.getElementById("loading");
  if (el) el.hidden = true;
}

function setStatus(text, isErr) {
  const el = document.getElementById("status");
  document.getElementById("status-text").textContent = text || "Ready";
  el.className = "status" + (isErr ? " err" : "");
}

async function refreshScenes(select) {
  const listed = await rpc("forge_call", { op: "scene_list", name: "", params: {}, actor: "user" });
  let scenes = (listed && listed.scenes) || [];
  if (!scenes.length) {
    await rpc("forge_call", { op: "scene_new", name: "", params: { name: "Scene" }, actor: "user" });
    scenes = [{ name: "Scene" }];
  }
  const sel = document.getElementById("scene");
  sel.innerHTML = "";
  for (const s of scenes) {
    const o = document.createElement("option");
    o.value = s.name;
    o.textContent = s.name;
    sel.appendChild(o);
  }
  state.name = select || state.name || scenes[0].name;
  sel.value = state.name;
}

async function poll() {
  if (!state.name || state.dragging || state.polling || state.pending) return;
  const sceneName = state.name;
  const epoch = state.epoch;
  const stale = () => state.name !== sceneName || epoch !== state.epoch || state.pending;
  state.polling = true;
  try {
    const info = await rpc("forge_call", { op: "scene_get", name: state.name, params: { brief: true }, actor: "user" });
    if (stale()) return;
    if (!info || info.ok === false) throw new Error(info?.error || "Could not read scene");
    if (info.rev === state.rev && state.payload?.name === sceneName) return;
    if (!state.payload || state.payload.name !== sceneName || info.geom !== state.geom) {
      const switching = !state.payload || state.payload.name !== state.name;
      if (switching) {
        const overlay = document.getElementById("loading");
        if (!overlay || overlay.hidden) showLoading(state.name);
        const objects = Number(info.objects) || 0;
        const meshes = Number(info.prototypes) || 0;
        loadStep("pack", objects
          ? `Packing ${objects.toLocaleString()} objects from ${meshes.toLocaleString()} unique meshes. This is the long step: each unique mesh is packed once and shared by its instances.`
          : "Packing the meshes for this scene.");
        await frame();
      }
      const payload = await rpc("forge_call", { op: "scene_payload", name: state.name, params: {}, actor: "user" });
      if (stale()) return;
      if (!payload || payload.ok === false) throw new Error(payload?.error || "Could not load scene");
      if (switching) {
        await prepareGeometry(payload);
        const pieces = (payload.instances || []).length;
        const rows = (payload.nodes || []).length;
        loadStep("place", `Placing ${pieces.toLocaleString()} pieces and indexing ${rows.toLocaleString()} Outliner entries. The panel stays here until the last piece is in, which is why a stadium pauses at the end.`);
        await frame();
      }
      if (stale()) return;
      state.rev = payload.rev;
      rebuild(payload);
      if (switching) hideLoading();
      const last = (payload.activity || []).slice(-1)[0];
      if (last) setStatus((last.actor === "agent" ? "AI: " : "") + last.summary);
    } else {
      state.rev = info.rev;
      state.geom = info.geom;
      state.payload.selection = info.selection;
      state.payload.mode = info.mode;
      state.payload.select_mode = info.select_mode;
      state.mode = info.mode || "object";
      state.selectMode = info.select_mode || "face";
      syncSelection();
    }
  } catch (err) {
    if (stale()) return;
    hideLoading();
    setStatus(err.message || String(err), true);
  } finally {
    state.polling = false;
  }
}

function syncChrome() {
  document.getElementById("scene-stats").textContent = state.payload ? `${(state.payload.instances || []).length.toLocaleString()} instances · ${state.picked.size} selected` : "";
  for (const button of document.querySelectorAll("button[title]")) button.setAttribute("aria-label", button.title);
  for (const button of document.querySelectorAll(".rail button, .hud button, .tabs button")) button.setAttribute("aria-pressed", String(button.classList.contains("on")));
  const activeNode = nodeById(state.payload?.selection?.object);
  const editable = activeNode?.type === "object" && !state.locked.has(activeNode.id);
  for (const id of ["sm-face", "sm-edge", "sm-vert"]) document.getElementById(id).disabled = !editable;
  for (const id of ["extrude", "inset", "bevel", "loop", "sub", "fill"]) document.getElementById(id).disabled = !editable;
  document.getElementById("del").disabled = !state.picked.size;
  const mode = document.getElementById("mode");
  if (mode) mode.textContent = state.mode === "edit" ? "Edit" : "Object";
  for (const [id, value] of [["sm-face", "face"], ["sm-edge", "edge"], ["sm-vert", "vert"]]) {
    const button = document.getElementById(id);
    if (button) button.classList.toggle("on", state.selectMode === value);
  }
  const gizmoId = { select: "g-select", translate: "g-move", rotate: "g-rot", scale: "g-scale" }[state.gizmo];
  for (const id of ["g-select", "g-move", "g-rot", "g-scale"]) {
    const button = document.getElementById(id);
    if (button) button.classList.toggle("on", id === gizmoId);
  }
  document.querySelectorAll(".tabs button").forEach((button) => {
    button.classList.toggle("on", button.dataset.tab === state.tab);
  });
  for (const button of document.querySelectorAll(".rail button, .hud button, .tabs button")) button.setAttribute("aria-pressed", String(button.classList.contains("on")));
}

function rememberPick(id, shift) {
  if (!id) {
    if (!shift) state.picked = new Set();
    return;
  }
  if (shift) {
    if (state.picked.has(id)) state.picked.delete(id);
    else state.picked.add(id);
  } else state.picked = new Set([id]);
}

function chosen() {
  const ids = [...state.picked];
  const sel = state.payload && state.payload.selection && state.payload.selection.object;
  if (sel && !ids.includes(sel)) ids.unshift(sel);
  return ids.filter(Boolean);
}

function nodeById(id) {
  return state.nodeMap.get(id);
}

function objectIds() {
  return chosen().filter((id) => {
    const n = nodeById(id);
    return n && n.type === "object";
  });
}

const EYE = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 12s4-6 10-6 10 6 10 6-4 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="2.5"/></svg>`;

function paintPanels(refreshTree = true) {
  const p = state.payload;
  if (!p) return;
  const live = new Set((p.nodes || []).map((n) => n.id));
  for (const id of [...state.picked]) if (!live.has(id)) state.picked.delete(id);
  const sel = p.selection && p.selection.object;
  if (sel && !state.picked.size) state.picked.add(sel);
  if (refreshTree) paintOutliner(p);
  const panel = document.getElementById("panel");
  if (state.tab === "layers") panel.innerHTML = layersPanel(p);
  else if (state.tab === "props") panel.innerHTML = props(p);
  else if (state.tab === "materials") panel.innerHTML = materials(p);
  else if (state.tab === "check") panel.innerHTML = `<div id="check"></div><div class="row"><button id="run-check" type="button">Check</button><button id="fix-all" type="button">Fix all</button></div>`;
  else if (state.tab === "activity") panel.innerHTML = activity(p);
  else panel.innerHTML = exportPanel();
  bindPanel();
  syncChrome();
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let treeRows = [];
function paintOutliner(p) {
  const container = document.getElementById("outliner");
  const scroll = document.getElementById("tree")?.scrollTop || 0;
  container.innerHTML = `<h1 class="panel-title">Outliner <span>${p.nodes.length.toLocaleString()} entries</span></h1>
    <div class="tree-tools"><input id="tree-search" type="search" placeholder="Search scene…" aria-label="Search Outliner" value="${esc(state.search)}" /><button id="collapse-tree" title="Collapse all groups">−</button><button id="expand-tree" title="Expand all groups">+</button></div>
    <div id="tree" role="tree" aria-label="Scene hierarchy"><div class="tree-space"></div></div>
    <div class="tree-footer">Ctrl / Shift-click to add · F to frame</div>`;
  const tree = document.getElementById("tree");
  tree.onscroll = paintTree;
  document.getElementById("tree-search").oninput = ev => { state.search = ev.target.value; tree.scrollTop = 0; indexTree(); };
  document.getElementById("collapse-tree").onclick = () => { state.collapsed = new Set(p.nodes.filter(n => n.type === "group").map(n => n.id)); indexTree(); };
  document.getElementById("expand-tree").onclick = () => { state.collapsed.clear(); indexTree(); };
  indexTree();
  tree.scrollTop = scroll;
  paintTree();
}

function indexTree() {
  const nodes = state.payload?.nodes || [];
  const kids = new Map();
  for (const n of nodes) {
    const parent = state.nodeMap.has(n.parent) ? n.parent : "";
    if (!kids.has(parent)) kids.set(parent, []);
    kids.get(parent).push(n);
  }
  const query = state.search.trim().toLowerCase();
  const matches = new Set();
  if (query) for (const n of nodes) {
    if (!String(n.name || n.id).toLowerCase().includes(query)) continue;
    let current = n;
    while (current && !matches.has(current.id)) { matches.add(current.id); current = nodeById(current.parent); }
  }
  treeRows = [];
  const seen = new Set();
  const stack = (kids.get("") || []).slice().reverse().map(n => [n, 0]);
  while (stack.length) {
    const [n, depth] = stack.pop();
    if (seen.has(n.id) || (query && !matches.has(n.id))) continue;
    seen.add(n.id);
    treeRows.push({ n, depth, children: kids.has(n.id) });
    if (!query && state.collapsed.has(n.id)) continue;
    for (const child of (kids.get(n.id) || []).slice().reverse()) stack.push([child, depth + 1]);
  }
  paintTree();
}

function paintTree() {
  const tree = document.getElementById("tree");
  if (!tree) return;
  const start = Math.max(0, Math.floor(tree.scrollTop / 30) - 4);
  const end = Math.min(treeRows.length, start + Math.ceil(tree.clientHeight / 30) + 9);
  const space = tree.firstElementChild;
  space.style.height = `${Math.max(40, treeRows.length * 30)}px`;
  space.innerHTML = treeRows.length ? treeRows.slice(start, end).map(({ n, depth, children }, i) => {
    const selected = state.picked.has(n.id), locked = state.locked.has(n.id), expanded = !state.collapsed.has(n.id) || !!state.search;
    return `<div class="tree-row${selected ? " on" : ""}${n.visible === false ? " is-hidden" : ""}${locked ? " is-locked" : ""}" role="treeitem" tabindex="0" aria-selected="${selected}" aria-level="${depth+1}" ${children ? `aria-expanded="${expanded}"` : ""} data-id="${esc(n.id)}" style="top:${(start+i)*30}px;padding-left:${6+Math.min(depth,12)*14}px" title="${esc(n.name)}">
      <button class="icon-btn" data-fold="${esc(n.id)}" aria-label="${expanded ? "Collapse" : "Expand"} ${esc(n.name)}" ${children ? "" : 'disabled aria-hidden="true"'}>${children ? expanded ? "▾" : "▸" : "·"}</button>
      <span class="nm">${esc(n.name || n.id)}</span><span class="kind">${n.type === "group" ? "Group" : "Mesh"}</span>
      <button class="icon-btn${n.visible === false ? " off" : ""}" data-eye="node" data-node="${esc(n.id)}" data-on="${n.visible !== false ? 1 : 0}" title="Toggle visibility">${EYE}</button>
    </div>`;
  }).join("") : `<div class="empty">${state.search ? "No matching objects." : "Empty scene. Use Add to create a primitive."}</div>`;
  space.querySelectorAll("[data-fold]").forEach(button => button.onclick = ev => {
    ev.stopPropagation();
    const id = button.dataset.fold;
    if (state.collapsed.has(id)) state.collapsed.delete(id); else state.collapsed.add(id);
    indexTree();
  });
  space.querySelectorAll("[data-id]").forEach(row => {
    row.onclick = ev => { if (!ev.target.closest("button")) choose(row.dataset.id, ev.ctrlKey || ev.shiftKey || ev.metaKey); };
    row.onkeydown = ev => {
      if (ev.target !== row) return;
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); choose(row.dataset.id, ev.ctrlKey || ev.shiftKey); }
      if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") { ev.preventDefault(); if (ev.key === "ArrowLeft") state.collapsed.add(row.dataset.id); else state.collapsed.delete(row.dataset.id); indexTree(); }
    };
  });
  space.querySelectorAll("[data-eye]").forEach(button => button.onclick = ev => { ev.stopPropagation(); op("edit", { id: button.dataset.node, visible: button.dataset.on !== "1" }); });
}

function layersPanel(p) {
  return `<h2>Layers</h2><div class="list">${(p.layers || []).map(l => `<div class="item">
    <button class="icon-btn${l.visible === false ? " off" : ""}" data-eye="layer" data-layer="${esc(l.id)}" data-on="${l.visible !== false ? 1 : 0}" title="Toggle layer visibility">${EYE}</button>
    <input class="nm" data-layer-name="${esc(l.id)}" aria-label="Layer name" value="${esc(l.name)}" />
    <button class="icon-btn" data-lock-layer="${esc(l.id)}" title="${l.locked ? "Unlock" : "Lock"} layer">${l.locked ? "◆" : "◇"}</button>
    <button class="icon-btn" data-assign="${esc(l.id)}" title="Assign selection to layer">→</button></div>`).join("")}</div>
    <div class="row"><button id="add-layer">+ Layer</button></div><h2>Hierarchy</h2>
    <div class="row"><button id="group">Group</button><button id="nest">Nest</button><button id="ungroup">Ungroup</button></div>
    <h2>Combine selected meshes</h2><div class="row"><button id="join-sel">Join</button><button id="union-sel">Union</button><button id="cut-sel">Cut</button></div>
    <p class="dim">Grouping preserves world positions. Nest uses the selected group. Union and Cut require two convex meshes.</p>`;
}

function props(p) {
  const n = nodeById(p.selection?.object);
  if (!n) return `<div class="empty">Select an object or group to edit its details.</div>`;
  const t = n.transform || { pos: [0,0,0], rot: [0,0,0], scale: [1,1,1] };
  const layers = (p.layers || []).map(l => `<option value="${esc(l.id)}"${l.id === n.layer ? " selected" : ""}>${esc(l.name)}</option>`).join("");
  const descendant = g => { const seen = new Set(); while (g && !seen.has(g.id)) { if (g.id === n.id) return true; seen.add(g.id); g = nodeById(g.parent); } return false; };
  const groups = p.nodes.filter(g => g.type === "group" && !descendant(g)).map(g => `<option value="${esc(g.id)}"${g.id === n.parent ? " selected" : ""}>${esc(g.name)}</option>`).join("");
  const transforms = [["Location", "pos", "p"], ["Rotation", "rot", "r"], ["Scale", "scale", "s"]].map(([label,key,prefix]) => `<div class="transform-row"><span>${label}</span>${["x","y","z"].map((axis,i) => `<label>${axis.toUpperCase()}<input id="${prefix+axis}" type="number" step="any" aria-label="${label} ${axis.toUpperCase()}" value="${Number(t[key][i].toFixed(5))}" /></label>`).join("")}</div>`).join("");
  const primitive = n.primitive ? `<h2>Primitive · ${esc(n.primitive.kind)}</h2>` + Object.entries(n.primitive.params || {}).map(([k,v]) => `<div class="row"><label>${esc(k)}</label><input type="number" step="any" data-param="${esc(k)}" value="${esc(v)}" /></div>`).join("") : "";
  return `<h2>${n.type === "group" ? "Group" : "Object"}${state.picked.size > 1 ? " · active selection" : ""}</h2>
    <div class="row"><label for="prop-name">Name</label><input id="prop-name" value="${esc(n.name)}" /></div>
    <div class="row"><label for="prop-lock">Locked</label><input id="prop-lock" type="checkbox" ${n.locked ? "checked" : ""} /></div>
    <h2>Local transform · cm / degrees</h2>${transforms}
    <h2>Organization</h2><div class="row"><label for="prop-layer">Layer</label><select id="prop-layer">${layers}</select></div>
    <div class="row"><label for="prop-parent">Parent</label><select id="prop-parent"><option value="">Scene root</option>${groups}</select></div>
    ${primitive}<div class="row"><button id="apply-props" ${state.locked.has(n.id) ? "disabled" : ""}>Apply changes</button><span class="dim">${(n.tris || 0).toLocaleString()} triangles</span></div>
    ${state.locked.has(n.id) ? '<p class="dim">Unlock the object or its layer before editing.</p>' : ""}`;
}

function hexOf(color) {
  const c = color || [0.8, 0.8, 0.8];
  const n = (v) => Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, "0");
  return `#${n(c[0])}${n(c[1])}${n(c[2])}`;
}

function materials(p) {
  const current = (p.materials || []).find((m) => m.id === state.matId) || (p.materials || [])[0];
  if (current) state.matId = current.id;
  const rows = (p.materials || []).map((m) => {
    const css = hexOf(m.base_color);
    const on = current && m.id === current.id ? " on" : "";
    return `<div class="item${on}" data-mat="${m.id}"><span class="swatch" style="background:${css}"></span><span class="nm">${esc(m.name)}</span></div>`;
  }).join("");
  const tex = (current && current.textures) || {};
  const thumb = (slot, label) => `<figure><img data-tex="${esc(tex[slot] || "")}" alt="" ${tex[slot] ? "" : "hidden"} /><figcaption>${label}</figcaption></figure>`;
  return `<div class="list">${rows}</div>
    <div class="row"><button id="mat-new" type="button">New</button></div>
    <div class="row"><label>Name</label><input id="mat-name" value="${esc((current && current.name) || "")}" /></div>
    <div class="row"><label>Color</label><input id="mat-color" type="color" value="${hexOf(current && current.base_color)}" /></div>
    <div class="row"><label>Rough</label><input id="rough" type="range" min="0" max="1" step="0.01" value="${current ? current.roughness ?? 0.5 : 0.5}" /></div>
    <div class="row"><label>Metal</label><input id="metal" type="range" min="0" max="1" step="0.01" value="${current ? current.metallic ?? 0 : 0}" /></div>
    <div class="row"><button id="mat-save" type="button">Save</button><button id="mat-assign" type="button">Assign</button></div>
    <h2>Textures</h2>
    <div class="thumbs">${thumb("base_color", "Color")}${thumb("normal", "Normal")}${thumb("orm", "ORM")}</div>
    <div class="row"><input id="tex-file" type="file" accept="image/*" /><button id="tex-import" type="button">Import</button></div>
    <div class="row"><input id="prompt" placeholder="worn painted metal, tileable" style="flex:1" /></div>
    <div class="row"><button id="gen" type="button">Generate maps</button></div>
    <h2>UVs</h2>
    <div class="row"><button id="uv-box" type="button">Box</button><button id="uv-planar" type="button">Planar</button><button id="uv-cyl" type="button">Cylinder</button></div>
    <p class="dim">Send to UEFN builds MI_ on M_Forge_Master and imports these as T_ textures.</p>`;
}

function activity(p) {
  const rows = [...(p.activity || [])].reverse().map((a) => {
    const dim = a.kind === "look" ? " dim" : "";
    return `<div class="item${dim}" data-rev="${a.rev}" data-id="${(a.ids || [])[0] || ""}"><span class="nm">${a.actor === "agent" ? "AI" : "You"}: ${esc(a.summary)}</span><button data-undo="${a.rev}" type="button">Undo</button></div>`;
  }).join("");
  return `<div class="list">${rows || "<span class='dim'>No edits yet.</span>"}</div>`;
}

function exportPanel() {
  return `<div class="row"><label>Mode</label><select id="ex-mode"><option value="each">Unique meshes</option><option value="group">Groups combined</option><option value="all">Everything</option></select></div>
    <div class="row"><button id="send" type="button">Send to UEFN</button></div>
    <h2>File</h2>
    <div class="row"><input id="blend-path" placeholder="C:\\path\\stadium.blend" style="flex:1" /></div>
    <div class="row"><button id="import-blend" type="button">Import Blend</button></div>
    <p class="dim">Writes one SM_ per shape or group, imports T_ color, normal, and ORM, and creates an MI_ on M_Forge_Master for each material. Collision uses the simple fallback; custom collision meshes are not imported. UEFN needs to be open.</p>`;
}

function pairBool(kind) {
  const ids = objectIds();
  if (ids.length !== 2) {
    setStatus("Select exactly two shapes", true);
    return;
  }
  op("boolean", { a: ids[0], b: ids[1], op: kind });
}

function bindPanel() {
  document.querySelectorAll("[data-layer-name]").forEach(input => input.onchange = () => op("layer", { action: "set", id: input.dataset.layerName, name: input.value.trim() || "Layer" }));
  document.querySelectorAll("[data-lock-layer]").forEach(button => button.onclick = () => {
    const layer = state.payload.layers.find(l => l.id === button.dataset.lockLayer);
    op("layer", { action: "set", id: layer.id, locked: !layer.locked });
  });
  const lock = document.getElementById("prop-lock");
  if (lock) lock.onchange = () => op("edit", { id: state.payload.selection.object, locked: lock.checked });
  const parentSelect = document.getElementById("prop-parent");
  if (parentSelect) parentSelect.onchange = () => op("edit", { id: state.payload.selection.object, parent: parentSelect.value });
  document.querySelectorAll("#panel [data-id]").forEach((el) => {
    el.onclick = (ev) => {
      if (ev.target.closest("button")) return;
      choose(el.dataset.id, ev.shiftKey || ev.ctrlKey || ev.metaKey);
    };
  });
  document.querySelectorAll("[data-eye]").forEach((el) => {
    el.onclick = (ev) => {
      ev.stopPropagation();
      const show = el.dataset.on !== "1";
      if (el.dataset.eye === "layer") op("layer", { action: "set", id: el.dataset.layer, visible: show });
      else op("edit", { id: el.dataset.node, visible: show });
    };
  });
  document.querySelectorAll("[data-assign]").forEach((el) => {
    el.onclick = (ev) => {
      ev.stopPropagation();
      const ids = chosen();
      if (!ids.length) { setStatus("Select something first", true); return; }
      op("layer", { action: "assign", id: el.dataset.assign, ids });
    };
  });
  const addLayer = document.getElementById("add-layer");
  if (addLayer) addLayer.onclick = () => {
    const n = ((state.payload && state.payload.layers) || []).length + 1;
    op("layer", { action: "add", name: "Layer " + n });
  };
  document.querySelectorAll("[data-nest]").forEach((el) => {
    el.onclick = (ev) => {
      ev.stopPropagation();
      const ids = chosen().filter((id) => id !== el.dataset.nest);
      if (!ids.length) { setStatus("Shift-click the shapes, then this arrow", true); return; }
      op("group", { into: el.dataset.nest, ids });
    };
  });
  const group = document.getElementById("group");
  if (group) group.onclick = () => {
    const ids = chosen();
    if (!ids.length) { setStatus("Select shapes to group", true); return; }
    op("group", { ids, name: "Group" });
  };
  const nest = document.getElementById("nest");
  if (nest) nest.onclick = () => {
    const ids = chosen();
    const groupNode = ids.map(nodeById).find((n) => n && n.type === "group");
    const rest = chosen().filter((id) => !groupNode || id !== groupNode.id);
    if (groupNode && rest.length) op("group", { into: groupNode.id, ids: rest });
    else if (rest.length) op("group", { ids: rest, name: "Group" });
    else setStatus("Shift-click shapes, then Nest", true);
  };
  const ungroup = document.getElementById("ungroup");
  if (ungroup) ungroup.onclick = () => {
    const groupNode = chosen().map(nodeById).find((n) => n && n.type === "group");
    if (!groupNode) { setStatus("Select a group", true); return; }
    op("ungroup", { id: groupNode.id });
  };
  const joinSel = document.getElementById("join-sel");
  if (joinSel) joinSel.onclick = () => {
    const ids = objectIds();
    if (ids.length < 2) { setStatus("Shift-click two shapes to join", true); return; }
    op("join", { ids });
  };
  const unionSel = document.getElementById("union-sel");
  if (unionSel) unionSel.onclick = () => pairBool("union");
  const cutSel = document.getElementById("cut-sel");
  if (cutSel) cutSel.onclick = () => pairBool("difference");
  document.querySelectorAll("[data-undo]").forEach((el) => {
    el.onclick = (ev) => {
      ev.stopPropagation();
      op("revert", { rev: Number(el.dataset.undo) - 1 });
    };
  });
  const apply = document.getElementById("apply-props");
  if (apply) apply.onclick = () => {
    const id = state.payload.selection.object;
    const params = {};
    document.querySelectorAll("[data-param]").forEach((input) => { params[input.dataset.param] = Number(input.value); });
    if ([...document.querySelectorAll("#panel input[type=number]")].some(input => !input.value.trim() || !Number.isFinite(Number(input.value)))) { setStatus("Enter valid numbers for all transform values", true); return; }
    op("edit", {
      id,
      name: document.getElementById("prop-name").value.trim() || nodeById(id).name,
      ...Object.fromEntries([["pos", "p"], ["rot", "r"], ["scale", "s"]].map(([key, prefix]) => [key, ["x", "y", "z"].map(axis => Number(document.getElementById(prefix + axis).value))])),
      layer: document.getElementById("prop-layer").value,
      params,
    });
  };
  const save = document.getElementById("mat-save");
  if (save) save.onclick = () => {
    const hex = document.getElementById("mat-color").value;
    const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    op("material_set", {
      id: state.matId,
      name: document.getElementById("mat-name").value || "Material",
      base_color: rgb,
      roughness: Number(document.getElementById("rough").value),
      metallic: Number(document.getElementById("metal").value),
    });
  };
  const matNew = document.getElementById("mat-new");
  if (matNew) matNew.onclick = () => {
    state.matId = "";
    const names = new Set((state.payload.materials || []).map(m => m.name));
    let number = 1;
    while (names.has(`Material ${number}`)) number++;
    op("material_set", { name: `Material ${number}`, base_color: [0.75, 0.75, 0.78, 1] });
  };
  const assign = document.getElementById("mat-assign");
  if (assign) assign.onclick = () => {
    if (state.matId) op("material_assign", { material: state.matId });
  };
  document.querySelectorAll("[data-mat]").forEach((el) => {
    el.onclick = () => {
      state.matId = el.dataset.mat;
      paintPanels();
    };
  });
  document.querySelectorAll("[data-tex]").forEach(async (img) => {
    if (!img.dataset.tex) return;
    const res = await rpc("forge_texture", { name: state.name, file: img.dataset.tex }).catch(() => null);
    if (res && res.ok && res.base64) img.src = `data:${res.mime || "image/png"};base64,${res.base64}`;
  });
  const importer = document.getElementById("tex-import");
  if (importer) importer.onclick = () => {
    const file = document.getElementById("tex-file").files[0];
    if (!file) { setStatus("Choose an image first", true); return; }
    const reader = new FileReader();
    reader.onload = async () => {
      setStatus("Importing texture…");
      const result = await op("texture", { png_base64: String(reader.result || ""), material: state.matId, tileable: false, derive_maps: true });
      if (result?.ok !== false) setStatus("Texture imported. Send to UEFN to create the MI_.");
    };
    reader.readAsDataURL(file);
  };
  for (const [id, mode] of [["uv-box", "box"], ["uv-planar", "planar"], ["uv-cyl", "cylinder"]]) {
    const button = document.getElementById(id);
    if (button) button.onclick = () => op("uv", { mode });
  }
  const gen = document.getElementById("gen");
  if (gen) gen.onclick = async () => {
    setStatus("Generating texture…");
    const result = await op("texture", { prompt: document.getElementById("prompt").value, material: state.matId, tileable: true, derive_maps: true });
    if (result?.ok !== false) setStatus("Texture ready");
  };
  const check = document.getElementById("run-check");
  if (check) check.onclick = async () => {
    const result = await op("check", {});
    if (result?.ok === false) return;
    const box = document.getElementById("check");
    if (!box) return;
    const issues = (result && result.issues) || [];
    box.innerHTML = issues.map((i) => `<div class="item" data-id="${i.object}"><span class="nm">${esc(i.message)}</span></div>`).join("") || "<span class='dim'>No issues.</span>";
    bindPanel();
  };
  const fix = document.getElementById("fix-all");
  if (fix) fix.onclick = () => op("fix", { issues: "all" });
  const importBlend = document.getElementById("import-blend");
  if (importBlend) importBlend.onclick = async () => {
    const path = document.getElementById("blend-path").value.trim();
    if (!path) { setStatus("Paste the path to a .blend file", true); return; }
    const fileName = path.split(/[/\\]/).pop();
    showLoading(fileName, LOAD_BLEND);
    loadStep("export", "Blender is reading every object and group. A stadium stays here because the export has to finish before any of it can draw.");
    setStatus("Importing Blend…");
    const opened = await op("scene_import_blend", { path });
    if (!opened || opened.ok === false) { hideLoading(); return; }
    const sceneName = opened.opened || opened.name;
    await refreshScenes(sceneName);
    state.name = sceneName;
    state.rev = -1;
    setStatus(`Imported ${opened.objects || 0} objects into ${sceneName}`);
  };
  const send = document.getElementById("send");
  if (send) send.onclick = async () => {
    setStatus("Sending to UEFN…");
    send.disabled = true;
    const result = await rpc("forge_send", { name: state.name, mode: document.getElementById("ex-mode").value, collision: "simple" }).catch(err => ({ ok: false, error: err.message }));
    send.disabled = false;
    if (!result || result.ok === false) setStatus((result && result.error) || "Send failed", true);
    else setStatus(`Imported ${result.count} mesh${result.count === 1 ? "" : "es"}`);
  };
}

function wire() {
  const prims = document.getElementById("prims");
  for (const kind of PRIMS) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = kind.replaceAll("_", " ");
    b.onclick = () => {
      prims.hidden = true;
      op("add", { kind, name: kind });
    };
    prims.appendChild(b);
  }
  document.getElementById("add-btn").onclick = () => { prims.hidden = !prims.hidden; };
  document.getElementById("mode").onclick = () => op("select", { mode: state.mode === "object" ? "edit" : "object" });
  for (const [id, mode] of [["sm-face", "face"], ["sm-edge", "edge"], ["sm-vert", "vert"]]) {
    document.getElementById(id).onclick = () => op("select", { select_mode: mode, mode: "edit" });
  }
  document.getElementById("g-select").onclick = () => setWidget("select");
  document.getElementById("g-move").onclick = () => setWidget("translate");
  document.getElementById("g-rot").onclick = () => setWidget("rotate");
  document.getElementById("g-scale").onclick = () => setWidget("scale");
  for (const id of ["snap", "rot-snap", "scale-snap"]) {
    document.getElementById(id).onchange = () => applySnap();
  }
  document.getElementById("undo").onclick = () => op("undo", {});
  document.getElementById("redo").onclick = () => op("redo", {});
  document.getElementById("extrude").onclick = () => op("mesh_op", { op: "extrude", distance: 64 });
  document.getElementById("inset").onclick = () => op("mesh_op", { op: "inset", amount: 32 });
  document.getElementById("bevel").onclick = () => op("mesh_op", { op: "bevel", width: 32 });
  document.getElementById("loop").onclick = () => op("mesh_op", { op: "loopcut" });
  document.getElementById("sub").onclick = () => op("mesh_op", { op: "subdivide" });
  document.getElementById("fill").onclick = () => op("mesh_op", { op: "fill" });
  document.getElementById("del").onclick = deleteSelection;
  document.getElementById("bool").onclick = () => pairBool("difference");
  document.getElementById("union").onclick = () => pairBool("union");
  document.getElementById("join").onclick = () => {
    const ids = objectIds();
    if (ids.length < 2) { setStatus("Shift-click two shapes to join", true); return; }
    op("join", { ids });
  };
  document.getElementById("new").onclick = async () => {
    const names = new Set([...document.getElementById("scene").options].map(option => option.value));
    let number = 1;
    while (names.has(`Scene_${number}`)) number++;
    const name = `Scene ${number}`;
    const made = await rpc("forge_call", { op: "scene_new", name: "", params: { name }, actor: "user" });
    if (!made || made.ok === false) { setStatus((made && made.error) || "Could not make a scene", true); return; }
    resetScene();
    await refreshScenes(made.name || name);
    state.rev = -1;
  };
  document.getElementById("save-file").onclick = async () => {
    const saved = await op("scene_save", {});
    if (saved && saved.path) setStatus("Saved " + saved.path);
  };
  document.getElementById("open-file").onclick = async () => {
    const box = document.getElementById("files");
    if (!box.hidden) { box.hidden = true; return; }
    const listed = await rpc("forge_call", { op: "scene_files", name: state.name, params: {}, actor: "user" });
    const files = (listed && listed.files) || [];
    box.innerHTML = "";
    if (!files.length) {
      box.hidden = false;
      box.textContent = "No saved files yet";
      return;
    }
    for (const file of files) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = file.name;
      b.onclick = async () => {
        box.hidden = true;
        showLoading(file.name);
        loadStep("read", "Opening the saved file. Objects, groups, and meshes are stored together, so a large file waits here before the meshes are packed.");
        const opened = await op("scene_open", { path: file.path });
        if (!opened || opened.ok === false) { hideLoading(); return; }
        const sceneName = (opened && (opened.opened || opened.name)) || file.name;
        await refreshScenes(sceneName);
        state.name = sceneName;
        state.rev = -1;
        setStatus("Opened " + sceneName);
      };
      box.appendChild(b);
    }
    box.hidden = false;
  };
  document.getElementById("scene").onchange = (ev) => {
    if (ev.target.value === state.name) return;
    resetScene();
    state.name = ev.target.value;
    state.rev = -1;
    showLoading(state.name);
    loadStep("read", "Switching scenes. The long wait is the next step, which packs every mesh before the view can open.");
  };
  document.getElementById("follow").onchange = (ev) => { state.follow = ev.target.checked; };
  const tabs = document.getElementById("tabs");
  const tabIcons = {
    outliner: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 6h16M8 12h12M8 18h12M4 12h.01M4 18h.01"/></svg>`,
    props: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="4" y="4" width="16" height="16"/><path d="M8 9h8M8 13h5"/></svg>`,
    materials: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="7"/><path d="M12 5v14M5 12h14"/></svg>`,
    check: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M5 13l4 4L19 7"/></svg>`,
    activity: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 18V6M4 18h16M8 14l3-4 3 2 4-6"/></svg>`,
    export: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 4v10M8 10l4 4 4-4M5 20h14"/></svg>`,
  };
  for (const [id, label] of [["props", "Details"], ["layers", "Layers"], ["materials", "Materials"], ["check", "Validate"], ["activity", "History"], ["export", "UEFN"]]) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.tab = id;
    b.title = label;
    b.innerHTML = (tabIcons[id] || tabIcons.outliner) + `<span>${label}</span>`;
    b.onclick = () => { state.tab = id; paintPanels(); };
    tabs.appendChild(b);
  }
  function focusSelection() {
    const bounds = new THREE.Box3();
    const slotSet = new Set(chosen().flatMap(id => state.slots.get(id) || []));
    for (const slot of slotSet) {
      const geo = slot.mesh.geometry;
      if (!geo.boundingBox) geo.computeBoundingBox();
      slot.mesh.getMatrixAt(slot.index, _m);
      bounds.union(geo.boundingBox.clone().applyMatrix4(_m));
    }
    if (bounds.isEmpty()) return;
    const center = bounds.getCenter(new THREE.Vector3());
    const radius = bounds.getSize(new THREE.Vector3()).length() / 2;
    const direction = camera.position.clone().sub(orbit.target).normalize();
    const fov = Math.min(camera.fov * Math.PI / 180, 2 * Math.atan(Math.tan(camera.fov * Math.PI / 360) * camera.aspect));
    orbit.target.copy(center);
    camera.position.copy(center).addScaledVector(direction, Math.max(128, radius / Math.sin(fov / 2) * 1.15));
    orbit.update();
  }

  window.addEventListener("keydown", (ev) => {
    if (ev.target.matches("input, textarea, select")) return;
    const k = ev.key.toLowerCase();
    if (!ev.repeat) held.add(k);
    if (k === "escape") endFly();
    if (state.flying || rightDown) {
      if (k === "w" || k === "a" || k === "s" || k === "d" || k === "q" || k === "e") ev.preventDefault();
      return;
    }
    if (k === "escape") { endFly(); document.getElementById("prims").hidden = true; document.getElementById("files").hidden = true; return; }
    if ((ev.ctrlKey || ev.metaKey) && ["z", "b", "r", "j"].includes(k)) ev.preventDefault();
    if (k === "tab") return;
    else if (k === "q") setWidget("select");
    else if (k === "w") setWidget("translate");
    else if (k === "e") setWidget("rotate");
    else if (k === "r" && !ev.ctrlKey) setWidget("scale");
    else if (k === "f") focusSelection();
    else if (k === "i") op("mesh_op", { op: "inset", amount: 32 });
    else if (k === "x" || k === "delete") deleteSelection();
    else if (k === "z" && (ev.ctrlKey || ev.metaKey)) op(ev.shiftKey ? "redo" : "undo", {});
    else if (k === "d" && ev.shiftKey) op("duplicate", {});
    else if (ev.ctrlKey && k === "b") op("mesh_op", { op: "bevel", width: 32 });
    else if (ev.ctrlKey && k === "r") op("mesh_op", { op: "loopcut" });
    else if (ev.ctrlKey && k === "j") {
      const ids = objectIds();
      if (ids.length >= 2) op("join", { ids });
    }
  });
  window.addEventListener("keyup", (ev) => held.delete(ev.key.toLowerCase()));
  window.addEventListener("blur", () => { held.clear(); endFly(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) endFly(); });
}

function deleteSelection() {
  if (state.mode === "edit") return op("mesh_op", { op: "delete", kind: state.selectMode });
  const ids = chosen().filter(id => !state.locked.has(id));
  if (ids.length) return op("delete", { ids });
}

// The parent ThemeProvider is the sole source of Appearance values.
let themeVersion = 0;
function applyTheme(vars) {
  if (!vars || typeof vars !== "object") return;
  for (const [key, value] of Object.entries(vars)) {
    if (/^[a-z][a-z0-9-]*$/.test(key) && typeof value === "string") document.documentElement.style.setProperty(`--${key}`, value);
  }
  const probe = document.createElement("span");
  document.body.appendChild(probe);
  const color = token => {
    probe.style.color = `var(--${token})`;
    return new THREE.Color(getComputedStyle(probe).color);
  };
  scene3.background.copy(color("bg"));
  for (const mat of [].concat(minorGrid.material)) mat.color.copy(color("border-light"));
  for (const mat of [].concat(majorGrid.material)) mat.color.copy(color("muted"));
  TINT.copy(color("accent")).lerp(WHITE, 0.5);
  gizmo.setColors(color("red"), color("green"), color("blue"), color("accent"));
  FLASH.copy(color("blue"));
  // Native form controls follow the luminance of the host background too.
  document.documentElement.style.colorScheme = scene3.background.getHSL({}, THREE.SRGBColorSpace).l >= .5 ? "light" : "dark";
  probe.remove();
  markSelection(true);
  paintColors();
  if (state.faceLines) state.faceLines.material.color.copy(TINT);
}
window.addEventListener("message", ev => {
  if (ev.source !== parent || ev.data?.channel !== CHANNEL || ev.data?.event?.type !== "appearance_theme") return;
  themeVersion++;
  applyTheme(ev.data.event.vars);
});
const requestedThemeVersion = themeVersion;
call("theme.get").then(theme => { if (requestedThemeVersion === themeVersion) applyTheme(theme.vars); }).catch(err => setStatus(`Appearance unavailable: ${err.message}`, true));

wire();
document.addEventListener("click", ev => {
  if (!ev.target.closest(".add-wrap")) { document.getElementById("prims").hidden = true; document.getElementById("files").hidden = true; }
});
window.addEventListener("unhandledrejection", ev => { setStatus(ev.reason?.message || "Operation failed", true); });
refreshScenes().then(() => { state.rev = -1; poll(); setInterval(poll, 250); }).catch((err) => setStatus(err.message || String(err), true));
