import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";

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
      if (!d || d.channel !== CHANNEL || d.id !== id) return;
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
  tab: "outliner",
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
  matId: "",
};

const CHUNK = 16384;
const WHITE = new THREE.Color(1, 1, 1);
const TINT = new THREE.Color(0.65, 0.78, 1);
const FLASH = new THREE.Color(0.45, 0.6, 1);
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
scene3.background = new THREE.Color(0x141418);
const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 200000);
camera.up.set(0, 0, 1);
camera.position.set(1200, -1600, 900);
const orbit = new OrbitControls(camera, renderer.domElement);
orbit.target.set(0, 0, 256);
orbit.mouseButtons.LEFT = -1;
orbit.mouseButtons.MIDDLE = THREE.MOUSE.PAN;
orbit.mouseButtons.RIGHT = -1;
orbit.update();
scene3.add(new THREE.AmbientLight(0xffffff, 0.55));
const sun = new THREE.DirectionalLight(0xffffff, 1.1);
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
scene3.add(fortniteGrid(128, 192, 0x2a3140, 0.35));
scene3.add(fortniteGrid(512, 48, 0x5d7ea3, 0.9));
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
    for (const slot of state.dragSlots || []) {
      slot.mesh.frustumCulled = true;
      slot.mesh.computeBoundingBox();
      slot.mesh.computeBoundingSphere();
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
    "XYZ",
  );
  _pos.set(row[3] || 0, row[4] || 0, row[5] || 0);
  _quat.setFromEuler(_euler);
  _scl.set(row[9] || 1, row[10] || 1, row[11] || 1);
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
  if (geo) return geo;
  geo = new THREE.BufferGeometry();
  if (proto.pb) {
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(b64buf(proto.pb)), 3));
    geo.setIndex(new THREE.BufferAttribute(new Uint32Array(b64buf(proto.ib)), 1));
    if (proto.fb) geo.userData.faceIds = new Uint32Array(b64buf(proto.fb));
  } else {
    geo.setAttribute("position", new THREE.Float32BufferAttribute(proto.positions, 3));
    if (proto.normals) geo.setAttribute("normal", new THREE.Float32BufferAttribute(proto.normals, 3));
    geo.setIndex(proto.indices);
    geo.userData.faceIds = proto.face_ids || [];
  }
  geoCache.set(proto.id, geo);
  return geo;
}

function materialFor(matId, materials) {
  const color = colorOf(matId, materials);
  let mat = matCache.get(matId || "");
  if (!mat) {
    mat = new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.05, flatShading: true });
    matCache.set(matId || "", mat);
    return mat;
  }
  mat.color.copy(color);
  return mat;
}

function clearView() {
  gizmo.detach();
  for (const mesh of state.batches) mesh.removeFromParent();
  state.batches = [];
  state.slots = new Map();
  state.parentOf = new Map();
  if (state.pick) {
    if (state.pick.material && state.pick.material !== matCache.get(state.pick.userData.matId)) state.pick.material.dispose();
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

function paintColors() {
  const sel = (state.payload && state.payload.selection && state.payload.selection.object) || "";
  const flash = new Set(state.flashIds || []);
  for (const batch of state.batches) {
    const ids = batch.userData.ids;
    for (let i = 0; i < ids.length; i++) {
      const id = ids[i];
      batch.setColorAt(i, flash.has(id) ? FLASH : id === sel && state.mode === "object" ? TINT : WHITE);
    }
    if (batch.instanceColor) batch.instanceColor.needsUpdate = true;
  }
}

function paintFaceLines() {
  if (state.faceLines) {
    state.faceLines.geometry.dispose();
    state.faceLines.material.dispose();
    state.faceLines.removeFromParent();
    state.faceLines = null;
  }
  const picked = new Set((state.payload && state.payload.selection && state.payload.selection.faces) || []);
  if (!state.pick || !picked.size || state.mode !== "edit") return;
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
  if (!pts.length) return;
  const lines = new THREE.BufferGeometry();
  lines.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  state.faceLines = new THREE.LineSegments(lines, new THREE.LineBasicMaterial({ color: 0x6c8cff }));
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
  const worldCache = new Map();
  function world(id) {
    if (!id || !byId.has(id)) return _ident;
    if (worldCache.has(id)) return worldCache.get(id);
    const n = byId.get(id);
    const t = n.transform || { pos: [0, 0, 0], rot: [0, 0, 0], scale: [1, 1, 1] };
    const local = composeRow([0, "", "", t.pos[0], t.pos[1], t.pos[2], t.rot[0], t.rot[1], t.rot[2], t.scale[0], t.scale[1], t.scale[2]], new THREE.Matrix4());
    const m = new THREE.Matrix4().multiplyMatrices(world(n.parent), local);
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
    const mat = materialFor(rows[0][12], payload.materials);
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
      const mesh = new THREE.Mesh(geometryFor(proto), materialFor(pulled[12], payload.materials));
      composeRow(pulled, local);
      mesh.matrix.multiplyMatrices(world(pulled[2]), local);
      mesh.matrixAutoUpdate = false;
      mesh.userData = { id: pulled[0], faceIds: geometryFor(proto).userData.faceIds || [], matId: pulled[12] || "" };
      content.add(mesh);
      state.pick = mesh;
      state.parentOf.set(pulled[0], world(pulled[2]));
    }
  }
  state.batchEdit = state.mode === "edit";
  state.editId = sel;
  paintColors();
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
  slots[0].mesh.getMatrixAt(slots[0].index, _m);
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
  else {
    paintColors();
    paintFaceLines();
  }
  attachGizmo();
  paintPanels();
  syncChrome();
}

function rebuild(payload) {
  state.payload = payload;
  state.mode = payload.mode || "object";
  state.selectMode = payload.select_mode || "face";
  state.geom = payload.geom || 0;
  state.flashIds = (payload.flash && payload.flash.ids) || [];
  buildBatches();
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
  slots[0].mesh.getMatrixAt(slots[0].index, _m);
  const parent = state.parentOf.get(id);
  if (parent && parent !== _ident) _m.premultiply(_inv.copy(parent).invert());
  _m.decompose(_pos, _quat, _scl);
  _euler.setFromQuaternion(_quat, "XYZ");
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

function beginFly() {
  if (state.dragging || state.flying) return;
  state.flying = true;
  orbit.enabled = false;
  orbit.enableZoom = false;
  camera.getWorldDirection(_fwd);
  flyPitch = Math.asin(THREE.MathUtils.clamp(_fwd.z, -1, 1));
  renderer.domElement.requestPointerLock?.();
}

function endFly() {
  if (!state.flying) return;
  state.flying = false;
  orbit.enableZoom = true;
  orbit.enabled = !state.dragging;
  if (document.pointerLockElement === renderer.domElement) document.exitPointerLock();
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
document.addEventListener("mousemove", (ev) => {
  if (state.flying) look(ev.movementX, ev.movementY);
});
document.addEventListener("pointerup", (ev) => {
  if (ev.button === 2) endFly();
});
document.addEventListener("pointerlockchange", () => {
  if (state.flying && document.pointerLockElement !== renderer.domElement) endFly();
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
renderer.domElement.addEventListener("pointerdown", (ev) => {
  if (ev.button === 2) { beginFly(); return; }
  if (ev.button !== 0 || state.dragging || state.flying || gizmo.axis) return;
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(state.batches.concat(state.pick ? [state.pick] : []), false);
  if (!hits.length) return;
  const hit = hits[0];
  const id = hit.instanceId != null && hit.object.userData.ids
    ? hit.object.userData.ids[hit.instanceId]
    : hit.object.userData.id;
  const face = hit.instanceId == null ? (hit.object.userData.faceIds || [])[hit.faceIndex] : undefined;
  rememberPick(id, ev.shiftKey);
  op("select", { object: id, faces: state.mode === "edit" && state.selectMode === "face" && face != null ? [face] : undefined });
});

async function op(name, params) {
  try {
    setStatus("");
    const result = await rpc("forge_call", { op: name, name: state.name, params: params || {}, actor: "user" });
    if (result && result.ok === false) {
      setStatus(result.error || "Failed", true);
      return result;
    }
    state.rev = -1;
    return result;
  } catch (err) {
    setStatus(err.message || String(err), true);
    return { ok: false, error: err.message || String(err) };
  }
}

function setStatus(text, isErr) {
  const el = document.getElementById("status");
  el.textContent = text || "";
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
  if (!state.name || state.dragging || state.polling) return;
  state.polling = true;
  try {
    const info = await rpc("forge_call", { op: "scene_get", name: state.name, params: { brief: true }, actor: "user" });
    if (!info || info.ok === false || info.rev === state.rev) return;
    if (!state.payload || info.geom !== state.geom) {
      const payload = await rpc("forge_call", { op: "scene_payload", name: state.name, params: {}, actor: "user" });
      if (!payload || payload.ok === false) return;
      state.rev = payload.rev;
      rebuild(payload);
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
    setStatus(err.message || String(err), true);
  } finally {
    state.polling = false;
  }
}

function syncChrome() {
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
}

function rememberPick(id, shift) {
  if (!id) return;
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
  return ((state.payload && state.payload.nodes) || []).find((n) => n.id === id);
}

function objectIds() {
  return chosen().filter((id) => {
    const n = nodeById(id);
    return n && n.type === "object";
  });
}

const EYE = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 12s4-6 10-6 10 6 10 6-4 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="2.5"/></svg>`;

function paintPanels() {
  const p = state.payload;
  if (!p) return;
  const live = new Set((p.nodes || []).map((n) => n.id));
  for (const id of [...state.picked]) if (!live.has(id)) state.picked.delete(id);
  const sel = p.selection && p.selection.object;
  if (sel && !state.picked.size) state.picked.add(sel);
  const panel = document.getElementById("panel");
  if (state.tab === "outliner") panel.innerHTML = outliner(p);
  else if (state.tab === "props") panel.innerHTML = props(p);
  else if (state.tab === "materials") panel.innerHTML = materials(p);
  else if (state.tab === "check") panel.innerHTML = `<div id="check"></div><div class="row"><button id="run-check" type="button">Check</button><button id="fix-all" type="button">Fix all</button></div>`;
  else if (state.tab === "activity") panel.innerHTML = activity(p);
  else panel.innerHTML = exportPanel();
  bindPanel();
  syncChrome();
}

function layerColor(p, id) {
  const layer = (p.layers || []).find((l) => l.id === id);
  return (layer && layer.color) || "#8aa";
}

function outliner(p) {
  const layers = p.layers || [];
  const layerRows = layers.map((l) => {
    const on = l.visible !== false;
    return `<div class="item">
      <button class="icon-btn${on ? "" : " off"}" type="button" data-eye="layer" data-layer="${l.id}" data-on="${on ? 1 : 0}" title="${on ? "Hide layer" : "Show layer"}">${EYE}</button>
      <i class="dot" style="background:${l.color || "#8aa"}"></i>
      <span class="nm">${l.name}</span>
      <button class="icon-btn" type="button" data-assign="${l.id}" title="Put the selection on ${l.name}">→</button>
    </div>`;
  }).join("");
  const kids = new Map();
  for (const n of p.nodes || []) {
    const key = n.parent || "";
    if (!kids.has(key)) kids.set(key, []);
    kids.get(key).push(n);
  }
  const rows = [];
  function walk(parent, depth) {
    for (const n of kids.get(parent) || []) {
      if (rows.length > 500) return;
      const on = state.picked.has(n.id) ? " on" : "";
      const shown = n.visible !== false;
      const mark = n.type === "group" ? "▸ " : "";
      rows.push(`<div class="item${on}" data-id="${n.id}" style="padding-left:${4 + depth * 14}px">
        <button class="icon-btn${shown ? "" : " off"}" type="button" data-eye="node" data-node="${n.id}" data-on="${shown ? 1 : 0}" title="Show or hide">${EYE}</button>
        <i class="dot" style="background:${layerColor(p, n.layer)}"></i>
        <span class="nm">${mark}${n.name || n.id}</span>
        ${n.type === "group" ? `<button class="icon-btn" type="button" data-nest="${n.id}" title="Put the shift-clicked shapes in this group">→</button>` : ""}
      </div>`);
      walk(n.id, depth + 1);
    }
  }
  walk("", 0);
  const more = (p.nodes || []).length > rows.length ? `<div class="dim">${(p.nodes || []).length - rows.length} more</div>` : "";
  return `<h2>Layers</h2><div class="list">${layerRows}</div>
    <div class="row"><button id="add-layer" type="button">+ Layer</button></div>
    <h2>Scene</h2>
    <div class="list">${rows.join("") || "<span class='dim'>Empty scene. Use Add.</span>"}${more}</div>
    <div class="row">
      <button id="group" type="button" title="Group the shift-clicked shapes">Group</button>
      <button id="nest" type="button" title="Put the other selected shapes inside the selected group">Nest</button>
      <button id="ungroup" type="button">Ungroup</button>
    </div>
    <div class="row">
      <button id="join-sel" type="button">Join</button>
      <button id="union-sel" type="button">Union</button>
      <button id="cut-sel" type="button">Cut</button>
    </div>
    <p class="dim">Shift-click to select more than one. Join welds shapes. Union and Cut are booleans. Groups export as one mesh.</p>`;
}

function props(p) {
  const id = p.selection && p.selection.object;
  const n = (p.nodes || []).find((x) => x.id === id);
  if (!n) return "<span class='dim'>Select an object.</span>";
  const t = n.transform || { pos: [0, 0, 0], rot: [0, 0, 0], scale: [1, 1, 1] };
  const layers = (p.layers || []).map((l) => `<option value="${l.id}"${l.id === n.layer ? " selected" : ""}>${l.name}</option>`).join("");
  const groups = (p.nodes || []).filter((x) => x.type === "group" && x.id !== n.id)
    .map((g) => `<option value="${g.id}"${g.id === n.parent ? " selected" : ""}>${g.name}</option>`).join("");
  const prim = n.type === "group"
    ? "<span class='dim'>Group. Children move with it. Combine them on Export.</span>"
    : n.primitive ? `<h2>${n.primitive.kind}</h2>` + Object.entries(n.primitive.params || {}).map(([k, v]) =>
      `<div class="row"><label>${k}</label><input data-param="${k}" value="${v}" /></div>`).join("") : "<span class='dim'>Converted to a mesh.</span>";
  return `<h2>${n.name}</h2>
    <div class="row"><label>X</label><input id="px" value="${t.pos[0]}" /></div>
    <div class="row"><label>Y</label><input id="py" value="${t.pos[1]}" /></div>
    <div class="row"><label>Z</label><input id="pz" value="${t.pos[2]}" /></div>
    <div class="row"><label>Layer</label><select id="prop-layer">${layers}</select></div>
    <div class="row"><label>Parent</label><select id="prop-parent"><option value="">Scene</option>${groups}</select></div>
    <div class="row"><span class="dim">${n.tris || 0} tris</span></div>
    ${prim}
    <div class="row"><button id="apply-props" type="button">Apply</button></div>`;
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
    return `<div class="item${on}" data-mat="${m.id}"><span class="swatch" style="background:${css}"></span><span class="nm">${m.name}</span></div>`;
  }).join("");
  const tex = (current && current.textures) || {};
  const thumb = (slot, label) => `<figure><img data-tex="${tex[slot] || ""}" alt="" ${tex[slot] ? "" : "hidden"} /><figcaption>${label}</figcaption></figure>`;
  return `<div class="list">${rows}</div>
    <div class="row"><button id="mat-new" type="button">New</button></div>
    <div class="row"><label>Name</label><input id="mat-name" value="${(current && current.name) || ""}" /></div>
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
    return `<div class="item${dim}" data-rev="${a.rev}" data-id="${(a.ids || [])[0] || ""}"><span class="nm">${a.actor === "agent" ? "AI" : "You"}: ${a.summary}</span><button data-undo="${a.rev}" type="button">Undo</button></div>`;
  }).join("");
  return `<div class="list">${rows || "<span class='dim'>No edits yet.</span>"}</div>`;
}

function exportPanel() {
  return `<div class="row"><label>Mode</label><select id="ex-mode"><option value="each">Each shape</option><option value="group">Groups combined</option><option value="all">Everything</option></select></div>
    <div class="row"><button id="send" type="button">Send to UEFN</button></div>
    <h2>File</h2>
    <div class="row"><input id="blend-path" placeholder="C:\\path\\stadium.blend" style="flex:1" /></div>
    <div class="row"><button id="import-blend" type="button">Import Blend</button></div>
    <p class="dim">Writes one SM_ per shape or group, imports T_ color, normal, and ORM, and creates an MI_ on M_Forge_Master for each material. The Collision layer is the simple collision. UEFN needs to be open.</p>`;
}

function pairBool(kind) {
  const ids = objectIds();
  if (ids.length < 2) {
    setStatus("Shift-click two shapes first", true);
    return;
  }
  op("boolean", { a: ids[0], b: ids[1], op: kind });
}

function bindPanel() {
  document.querySelectorAll("[data-id]").forEach((el) => {
    el.onclick = (ev) => {
      if (ev.target.closest(".icon-btn")) return;
      rememberPick(el.dataset.id, ev.shiftKey);
      op("select", { object: el.dataset.id });
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
    const colors = ["#6c8cff", "#7dba6a", "#d4a017", "#c46ad4", "#5ec8c8", "#d27b4a"];
    op("layer", { action: "add", name: "Layer " + n, color: colors[n % colors.length] });
  };
  document.querySelectorAll("[data-nest]").forEach((el) => {
    el.onclick = (ev) => {
      ev.stopPropagation();
      const ids = objectIds().filter((id) => id !== el.dataset.nest);
      if (!ids.length) { setStatus("Shift-click the shapes, then this arrow", true); return; }
      op("group", { into: el.dataset.nest, ids });
    };
  });
  const group = document.getElementById("group");
  if (group) group.onclick = () => {
    const ids = objectIds();
    if (!ids.length) { setStatus("Select shapes to group", true); return; }
    op("group", { ids, name: "Group" });
  };
  const nest = document.getElementById("nest");
  if (nest) nest.onclick = () => {
    const ids = chosen();
    const groupNode = ids.map(nodeById).find((n) => n && n.type === "group");
    const rest = objectIds().filter((id) => !groupNode || id !== groupNode.id);
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
    const parent = document.getElementById("prop-parent").value;
    op("edit", {
      id,
      pos: [Number(document.getElementById("px").value), Number(document.getElementById("py").value), Number(document.getElementById("pz").value)],
      layer: document.getElementById("prop-layer").value,
      parent: parent || "",
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
    op("material_set", { name: "Material", base_color: [0.75, 0.75, 0.78, 1] });
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
    const res = await rpc("forge_texture", { name: state.name, file: img.dataset.tex });
    if (res && res.ok && res.base64) img.src = `data:${res.mime || "image/png"};base64,${res.base64}`;
  });
  const importer = document.getElementById("tex-import");
  if (importer) importer.onclick = () => {
    const file = document.getElementById("tex-file").files[0];
    if (!file) { setStatus("Choose an image first", true); return; }
    const reader = new FileReader();
    reader.onload = async () => {
      setStatus("Importing texture…");
      await op("texture", { png_base64: String(reader.result || ""), material: state.matId, tileable: false, derive_maps: true });
      setStatus("Texture imported. Send to UEFN to create the MI_.");
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
    await op("texture", { prompt: document.getElementById("prompt").value, material: state.matId, tileable: true, derive_maps: true });
    setStatus("Texture ready");
  };
  const check = document.getElementById("run-check");
  if (check) check.onclick = async () => {
    const result = await op("check", {});
    const box = document.getElementById("check");
    const issues = (result && result.issues) || [];
    box.innerHTML = issues.map((i) => `<div class="item" data-id="${i.object}"><span class="nm">${i.message}</span></div>`).join("") || "<span class='dim'>No issues.</span>";
    bindPanel();
  };
  const fix = document.getElementById("fix-all");
  if (fix) fix.onclick = () => op("fix", { issues: "all" });
  const importBlend = document.getElementById("import-blend");
  if (importBlend) importBlend.onclick = async () => {
    const path = document.getElementById("blend-path").value.trim();
    if (!path) { setStatus("Paste the path to a .blend file", true); return; }
    setStatus("Importing Blend…");
    const opened = await op("scene_import_blend", { path });
    if (!opened || opened.ok === false) return;
    const sceneName = opened.opened || opened.name;
    await refreshScenes(sceneName);
    state.name = sceneName;
    state.rev = -1;
    setStatus(`Imported ${opened.objects || 0} objects into ${sceneName}`);
  };
  const send = document.getElementById("send");
  if (send) send.onclick = async () => {
    setStatus("Sending to UEFN…");
    const result = await rpc("forge_send", { name: state.name, mode: document.getElementById("ex-mode").value, collision: "simple" });
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
  document.getElementById("del").onclick = () => op("mesh_op", { op: "delete", kind: "face" });
  document.getElementById("bool").onclick = () => pairBool("difference");
  document.getElementById("union").onclick = () => pairBool("union");
  document.getElementById("join").onclick = () => {
    const ids = objectIds();
    if (ids.length < 2) { setStatus("Shift-click two shapes to join", true); return; }
    op("join", { ids });
  };
  document.getElementById("new").onclick = async () => {
    const name = "Scene " + (document.getElementById("scene").options.length + 1);
    const made = await rpc("forge_call", { op: "scene_new", name: "", params: { name }, actor: "user" });
    if (!made || made.ok === false) { setStatus((made && made.error) || "Could not make a scene", true); return; }
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
        const opened = await op("scene_open", { path: file.path });
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
  document.getElementById("scene").onchange = (ev) => { state.name = ev.target.value; state.rev = -1; };
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
  for (const [id, label] of [["outliner", "Outliner"], ["props", "Object"], ["materials", "Material"], ["check", "Check"], ["activity", "Activity"], ["export", "UEFN"]]) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.tab = id;
    b.title = label;
    b.innerHTML = tabIcons[id];
    b.onclick = () => { state.tab = id; paintPanels(); };
    tabs.appendChild(b);
  }
  function focusSelection() {
    const sel = (state.payload && state.payload.selection && state.payload.selection.object) || "";
    const slots = state.slots.get(sel);
    if (!slots || !slots[0]) return;
    slots[0].mesh.getMatrixAt(slots[0].index, _m);
    const p = new THREE.Vector3().setFromMatrixPosition(_m);
    const offset = camera.position.clone().sub(orbit.target);
    if (offset.length() < 256) offset.set(1200, -1600, 640);
    orbit.target.copy(p);
    camera.position.copy(p).add(offset);
    orbit.update();
  }
  window.addEventListener("keydown", (ev) => {
    if (ev.target.matches("input, textarea, select")) return;
    const k = ev.key.toLowerCase();
    if (!ev.repeat) held.add(k);
    if (state.flying) return;
    if (k === "tab") { ev.preventDefault(); op("select", { mode: state.mode === "object" ? "edit" : "object" }); }
    else if (k === "q") setWidget("select");
    else if (k === "w") setWidget("translate");
    else if (k === "e") setWidget("rotate");
    else if (k === "r" && !ev.ctrlKey) setWidget("scale");
    else if (k === "f") focusSelection();
    else if (k === "i") op("mesh_op", { op: "inset", amount: 32 });
    else if (k === "x") op("mesh_op", { op: "delete", kind: "face" });
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
  window.addEventListener("blur", () => held.clear());
}

wire();
refreshScenes().then(() => { state.rev = -1; poll(); setInterval(poll, 250); }).catch((err) => setStatus(err.message || String(err), true));
