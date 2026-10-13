import * as THREE from "three";
import * as Brush from "./brush.js";
import { runMeshTask as compatibleMeshTask } from "./mesh-engine.js";
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

let panelVisible = true;
window.addEventListener("message", ev => {
  const event = ev.data?.event;
  if (ev.source === parent && ev.data?.channel === CHANNEL && event?.type === "panel.visibility") panelVisible = event.visible !== false;
});
let importJob = "";
let importReport = "";
let importPrefs = {};
call("prefs.get", {id: "mesh-import"}).then(result => { const value = typeof result?.value === "string" ? JSON.parse(result.value) : result?.value; importPrefs = value && typeof value === "object" ? value : {}; }).catch(() => {});

async function rpc(method, params) {
  if (!["forge_call", "forge_texture"].includes(method)) return call("plugin.call", { method, params: params || {} });
  let reply = await call("plugin.call", { method: "forge_submit", params: { method, params: params || {} } });
  const job = reply.job;
  const importing = ["scene_import_preview", "scene_import_blend", "import_mesh"].includes(params?.op);
  if (importing) { importJob = job || ""; const cancel = document.getElementById("cancel-import"); if (cancel) cancel.disabled = !job; }
  while (reply.ok && !reply.done) {
    await new Promise(resolve => setTimeout(resolve, (!panelVisible || document.hidden) ? 1000 : 100));
    reply = await call("plugin.call", { method: "forge_job", params: { job } });
    if (reply.elapsed >= 1 && state.pending) setStatus(`${reply.stage || "Processing"} · ${reply.elapsed}s`);
  }
  if (importing) { importJob = ""; const cancel = document.getElementById("cancel-import"); if (cancel) cancel.disabled = true; }
  if (!reply.ok) throw new Error(reply.error || "Forge operation failed");
  return reply.result;
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
  loadFailed: "",
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
  rigId: "",
  clipId: "",
  bone: "",
  frame: 0,
  playing: false,
  rigView: null,
};
const KINDS = ["idle", "walk", "wave", "sway", "spin", "bob", "swing", "open"];

const CHUNK = 16384;
const WHITE = new THREE.Color(1, 1, 1);
const TINT = new THREE.Color();
const FLASH = new THREE.Color();
const BONE_IDLE = new THREE.Color(0.6, 0.6, 0.6);
const BONE_ON = new THREE.Color(1, 0.7, 0.2);
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
  if (gizmo.object === bonePivot) { boneDrag(ev.value); return; }
  state.dragging = ev.value;
  orbit.enabled = !ev.value && !state.flying;
  if (ev.value) {
    state.epoch++; // Discard polls/selection replies started before this drag.
    const slots = state.slots.get(pivot.userData.id) || [];
    state.dragSlots = slots;
    state.dragBase = slots.map((slot) => {
      const m = new THREE.Matrix4();
      slot.mesh.getMatrixAt(slot.index, m);
      return m;
    });
    pivot.updateMatrix();
    state.dragStart = pivot.matrix.clone();
    state.dragOrigin = pivot.position.clone();
    gizmo.setTranslationSnap(null);
    for (const slot of slots) slot.mesh.frustumCulled = false;
  } else {
    for (const mesh of new Set((state.dragSlots || []).map(slot => slot.mesh))) {
      mesh.frustumCulled = true;
      mesh.computeBoundingBox();
      mesh.computeBoundingSphere();
    }
    commitXform();
    applySnap();
  }
});
gizmo.addEventListener("objectChange", () => {
  if (gizmo.object === bonePivot) { boneDragMove(); return; }
  if (!state.dragging || !state.dragBase) return;
  if (state.gizmo === "translate") {
    const grid = Number(document.getElementById("snap").value);
    if (grid) for (const axis of ["x", "y", "z"]) {
      pivot.position[axis] = state.dragOrigin[axis] + Math.round((pivot.position[axis] - state.dragOrigin[axis]) / grid) * grid;
    }
  }
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
const bonePivot = new THREE.Object3D();
content.add(bonePivot);

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

let meshWorker = null;
const meshRequests = new Map(), meshSources = new Map(), compatiblePrepared = new Map();
let meshSequence = 0, workerFailed = false, workerReadyResolve;
const workerReady = new Promise(resolve => { workerReadyResolve = resolve; });
let startupTimer, workerUrl;
function disableMeshWorker(reason) {
  if (workerFailed) return;
  workerFailed = true;
  clearTimeout(startupTimer);
  if (workerUrl) { URL.revokeObjectURL(workerUrl); workerUrl = null; }
  meshWorker?.terminate();
  workerReadyResolve(false);
  for (const request of meshRequests.values()) { clearTimeout(request.timer); request.reject(new Error(reason)); }
  meshRequests.clear();
  loadStep("smooth", "Preparing meshes in compatibility mode; the panel remains responsive.");
}
try {
  const url = workerUrl = URL.createObjectURL(new Blob([FORGE_MESH_WORKER], {type:"text/javascript"}));
  meshWorker = new Worker(url);
  meshWorker.onmessage = ({data}) => {
    if (data.ready) { clearTimeout(startupTimer); URL.revokeObjectURL(workerUrl); workerUrl = null; workerReadyResolve(true); return; }
    const request = meshRequests.get(data.id);
    if (!request) return;
    meshRequests.delete(data.id);
    clearTimeout(request.timer);
    if (data.error) request.reject(new Error(data.error)); else request.resolve(data.result);
  };
  meshWorker.onerror = event => { event.preventDefault(); disableMeshWorker(event.message || "Mesh worker failed"); };
  meshWorker.onmessageerror = () => disableMeshWorker("Mesh worker response could not be read");
  startupTimer = setTimeout(() => disableMeshWorker("Mesh worker did not start"), 2000);
} catch(error) { disableMeshWorker(error.message || "Mesh worker unavailable"); }
async function compatibilityTask(op, params) {
  if (["pick", "lines"].includes(op) && !compatiblePrepared.has(params.key)) {
    const source = meshSources.get(params.key);
    if (!source) throw new Error("Mesh is no longer loaded");
    compatiblePrepared.set(params.key, compatibleMeshTask("prepare", source));
  }
  if (op === "prepare") {
    const key = params.id + ":" + params.revision;
    const task = compatibleMeshTask(op, params);
    compatiblePrepared.set(key, task);
    return task;
  }
  if (params.key && compatiblePrepared.has(params.key)) await compatiblePrepared.get(params.key);
  return compatibleMeshTask(op, params);
}
async function meshTask(op, params) {
  if (op === "prepare") meshSources.set(params.id + ":" + params.revision, params);
  if (op === "retain") {
    const keep = new Set(params.keys);
    for (const key of meshSources.keys()) if (!keep.has(key)) { meshSources.delete(key); compatiblePrepared.delete(key); }
  }
  if (await workerReady && !workerFailed) {
    try {
      return await new Promise((resolve, reject) => {
        const id = ++meshSequence;
        const timer = setTimeout(() => disableMeshWorker("Mesh worker stopped responding"), 15000);
        meshRequests.set(id, {resolve, reject, timer});
        try { meshWorker.postMessage({id, op, params}); }
        catch(error) { disableMeshWorker(error.message); }
      });
    } catch(error) {
      if (!workerFailed) throw error;
    }
  }
  return compatibilityTask(op, params);
}
function cachedGeometry(proto) {
  const geo = geoCache.get(proto.id);
  return geo && geo.userData.source.revision === proto.revision && geo.userData.source.pb === proto.pb && geo.userData.source.ib === proto.ib ? geo : null;
}
async function prepareProto(proto) {
  if (cachedGeometry(proto)) return;
  const packed = await meshTask("prepare", proto);
  if (proto.nb) packed.normals = new Float32Array(b64buf(proto.nb));
  const geo = new THREE.BufferGeometry();
  if (proto.ub) {
    // Textured scenes draw per-corner UVs, so vertices split at seams. Picking keeps the worker's indexed copy.
    const uv = new Float32Array(b64buf(proto.ub));
    const blend = proto.cb ? new Float32Array(b64buf(proto.cb)) : null;
    const idx = packed.indices, src = packed.positions, nrm = packed.normals;
    const pos = new Float32Array(idx.length * 3), nor = nrm ? new Float32Array(idx.length * 3) : null, mask = blend ? new Float32Array(idx.length * 3) : null;
    for (let k = 0; k < idx.length; k++) {
      const v = idx[k] * 3;
      pos[k * 3] = src[v]; pos[k * 3 + 1] = src[v + 1]; pos[k * 3 + 2] = src[v + 2];
      if (nor) { nor[k * 3] = nrm[v]; nor[k * 3 + 1] = nrm[v + 1]; nor[k * 3 + 2] = nrm[v + 2]; }
      if (mask) { mask[k * 3] = blend[v]; mask[k * 3 + 1] = blend[v + 1]; mask[k * 3 + 2] = blend[v + 2]; }
    }
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
    if (nor) geo.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
    if (mask) geo.setAttribute("blendMask", new THREE.BufferAttribute(mask, 3));
  } else {
    geo.setAttribute("position", new THREE.BufferAttribute(packed.positions, 3));
    geo.setIndex(new THREE.BufferAttribute(packed.indices, 1));
    if (packed.normals) geo.setAttribute("normal", new THREE.BufferAttribute(packed.normals, 3));
  }
  if (proto.vcb) {
    const colors = new Float32Array(b64buf(proto.vcb));
    const expanded = proto.ub ? new Float32Array(packed.indices.length * 3) : colors;
    if (proto.ub) packed.indices.forEach((v, i) => expanded.set(colors.subarray(v * 3, v * 3 + 3), i * 3));
    geo.setAttribute("color", new THREE.BufferAttribute(expanded, 3));
  }
  geo.boundingBox = new THREE.Box3(new THREE.Vector3(...packed.box[0]), new THREE.Vector3(...packed.box[1]));
  geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(...packed.sphere[0]), packed.sphere[1]);
  for (const [start, count, slot] of proto.groups || []) geo.addGroup(start, count, slot);
  geo.userData = {source: proto, faceIds: packed.faces, workerKey: packed.key, materialSets: new Map()};
  geoCache.get(proto.id)?.dispose();
  geoCache.set(proto.id, geo);
}

function geometryFor(proto) {
  const geo = cachedGeometry(proto);
  if (!geo) throw new Error("Geometry must be prepared before viewport update");
  return geo;
}

const texCache = new Map();
const WHITE_PIXEL = Object.assign(document.createElement("canvas"), { width: 1, height: 1 });
Object.assign(WHITE_PIXEL.getContext("2d"), { fillStyle: "#fff" }).fillRect(0, 0, 1, 1);

// One THREE texture per stored file; file names change on every save, so a name never goes stale.
function textureFor(fname) {
  const key = fname || "";
  let tex = texCache.get(key);
  if (tex) return tex;
  tex = new THREE.Texture(WHITE_PIXEL);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;  // box-projected tiling UVs run past 0..1
  tex.needsUpdate = true;
  texCache.set(key, tex);
  if (fname) rpc("forge_texture", { name: state.name, file: fname }).then(res => {
    if (!res?.ok) return;
    const img = new Image();
    // The GPU copy was sized for the 1x1 placeholder; dispose so three reallocates at the real size.
    img.onload = () => { tex.dispose(); tex.image = img; tex.needsUpdate = true; };
    img.src = `data:${res.mime || "image/png"};base64,${res.base64}`;
  }).catch(() => {});
  return tex;
}

// Blend materials: base plus three layers revealed by the blendMask attribute (vertex colour R, G, B
// in UEFN). Same maths as M_Forge_Blend; roughness and metal stay the base's in the dock.
function blendShader(mat, uniforms) {
  mat.onBeforeCompile = shader => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec3 blendMask;\nvarying vec3 vForgeBlend;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvForgeBlend = blendMask;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vForgeBlend;\nuniform vec3 forgeColor1;\nuniform vec3 forgeColor2;\nuniform vec3 forgeColor3;\nuniform sampler2D forgeMap1;\nuniform sampler2D forgeMap2;\nuniform sampler2D forgeMap3;")
      .replace("#include <map_fragment>", `#include <map_fragment>
{
  vec3 forgeL1 = forgeColor1 * texture2D(forgeMap1, vMapUv).rgb;
  vec3 forgeL2 = forgeColor2 * texture2D(forgeMap2, vMapUv).rgb;
  vec3 forgeL3 = forgeColor3 * texture2D(forgeMap3, vMapUv).rgb;
  diffuseColor.rgb = mix(mix(mix(diffuseColor.rgb, forgeL1, vForgeBlend.r), forgeL2, vForgeBlend.g), forgeL3, vForgeBlend.b);
}`);
  };
  mat.customProgramCacheKey = () => "forge-blend";
}

function materialsForProto(proto, fallback, materials) {
  if (!proto.material_slots?.length) return materialFor(fallback, materials, !!proto.smooth);
  const geo = geometryFor(proto);
  const next = proto.material_slots.map(id => materialFor(id && id !== "default" ? id : fallback, materials, !!proto.smooth));
  const previous = geo.userData.materialSets.get(fallback);
  if (previous && previous.length === next.length && previous.every((m, i) => m === next[i])) return previous;
  geo.userData.materialSets.set(fallback, next);
  return next;
}

function materialFor(matId, materials, smooth) {
  const authored = (materials || []).find(m => m.id === matId);
  const tex = authored?.textures?.base_color || "";
  const layers = (authored?.layers || []).map(id => (materials || []).find(m => m.id === id) || null);
  const blended = layers.some(Boolean);
  const key = [matId || "", smooth ? "s" : "", tex, blended ? layers.map(l => l ? `${l.id}:${l.textures?.base_color || ""}` : "").join(",") : ""].join("|");
  let mat = matCache.get(key);
  if (!mat) {
    mat = new THREE.MeshStandardMaterial({ roughness: 0.55, metalness: 0.05, flatShading: !smooth, vertexColors: true });
    if (tex || blended) mat.map = textureFor(tex);  // a white map still turns on UVs for the layers
    if (blended) {
      mat.userData.layers = [1, 2, 3].map(i => ({ [`forgeColor${i}`]: { value: new THREE.Color() }, [`forgeMap${i}`]: { value: textureFor(layers[i - 1]?.textures?.base_color || "") } }));
      blendShader(mat, Object.assign({}, ...mat.userData.layers));
    }
    matCache.set(key, mat);
  }
  // A texture replaces the flat colour, as UseBaseColorTex does in UEFN.
  mat.color.copy(tex ? WHITE : colorOf(matId, materials));
  mat.roughness = authored?.roughness ?? 0.5;
  mat.metalness = authored?.metallic ?? 0;
  if (blended) layers.forEach((layer, i) => {
    const target = mat.userData.layers[i][`forgeColor${i + 1}`].value;
    if (!layer) target.copy(colorOf(matId, materials));
    else if (layer.textures?.base_color) target.copy(WHITE);
    else target.setRGB(...(layer.base_color || [0.75, 0.75, 0.78]).slice(0, 3));
  });
  return mat;
}

function clearView(retainBatches = false) {
  gizmo.detach();
  if (!retainBatches) for (const mesh of state.batches) { mesh.removeFromParent(); mesh.dispose(); }
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

let faceLineRequest = 0;
async function paintFaceLines() {
  const request = ++faceLineRequest;
  if (state.faceLines) {
    state.faceLines.geometry.dispose();
    state.faceLines.material.dispose();
    state.faceLines.removeFromParent();
    state.faceLines = null;
  }
  const pick = state.pick;
  if (!pick || state.mode !== "edit") return;
  const selection = state.payload.selection || {};
  if (!["faces", "edges", "verts"].some(key => selection[key]?.length)) return;
  try {
    const result = await meshTask("lines", {key: pick.geometry.userData.workerKey, selection});
    if (request !== faceLineRequest || state.pick !== pick || state.mode !== "edit" || !result.positions?.length) return;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(result.positions, 3));
    state.faceLines = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({color:TINT}));
    state.faceLines.matrix.copy(pick.matrix);
    state.faceLines.matrixAutoUpdate = false;
    content.add(state.faceLines);
    if (result.dense) setStatus("Dense selection shown with bounds; all selected elements remain editable");
  } catch (error) { setStatus(error.message, true); }
}

// ---- Rig view: bound objects swap to CPU-skinned copies driven by the server's baked
// per-frame skin matrices, so the dock plays exactly what forge_render and Send use.
// ponytail: CPU skinning every frame; move to THREE.SkinnedMesh if characters pass ~50k verts.
const ZERO = new THREE.Matrix4().makeScale(0, 0, 0);
const _boneM = new THREE.Matrix4(), _parentM = new THREE.Matrix4(), _tmpV = new THREE.Vector3(), _tmpS = new THREE.Vector3();
let rigSequence = 0;

function rigOf(p) {
  const rigs = p?.rigs || [];
  return rigs.find(r => r.id === state.rigId) || rigs[0] || null;
}

function clipOf(rig) {
  const clips = rig?.actions || [];
  return clips.find(c => c.id === state.clipId) || clips[0] || null;
}

function setSlots(ids, hide) {
  const touched = new Set();
  for (const id of ids) for (const slot of state.slots.get(id) || []) {
    if (slot.row[0] !== id) continue;
    if (hide) _m.copy(ZERO);
    else { composeRow(slot.row, _m); _m.premultiply(state.world.get(slot.row[2]) || _ident); }
    slot.mesh.setMatrixAt(slot.index, _m);
    touched.add(slot.mesh);
  }
  for (const mesh of touched) mesh.instanceMatrix.needsUpdate = true;
}

function dropRigView() {
  const view = state.rigView;
  if (!view) return;
  state.rigView = null;
  if (gizmo.object === bonePivot) gizmo.detach();
  for (const mesh of view.meshes) { mesh.geometry.dispose(); mesh.removeFromParent(); }
  view.lines.geometry.dispose();
  view.lines.material.dispose();
  view.lines.removeFromParent();
  setSlots(view.ids, false);
}

function buildRigView(data, key) {
  const meshes = [];
  for (const o of data.objects) {
    const rest = new Float32Array(b64buf(o.pb));
    const index = new Uint32Array(b64buf(o.ib));
    const geo = new THREE.BufferGeometry();
    if (o.ub) {
      // Skin in shared-vertex space, then copy out to per-corner positions that carry the UVs.
      geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(index.length * 3), 3));
      geo.setAttribute("uv", new THREE.BufferAttribute(new Float32Array(b64buf(o.ub)), 2));
      if (o.cb) {
        const cb = new Float32Array(b64buf(o.cb)), mask = new Float32Array(index.length * 3);
        index.forEach((v, k) => mask.set(cb.subarray(v * 3, v * 3 + 3), k * 3));
        geo.setAttribute("blendMask", new THREE.BufferAttribute(mask, 3));
      }
    } else {
      geo.setAttribute("position", new THREE.BufferAttribute(rest.slice(), 3));
      geo.setIndex(new THREE.BufferAttribute(index, 1));
    }
    const mesh = new THREE.Mesh(geo, materialFor(o.material, state.payload.materials, o.smooth));
    mesh.userData = { id: o.id, rest, index, corners: !!o.ub, posed: new Float32Array(rest.length),
      joints: new Uint16Array(b64buf(o.jb)), weights: new Float32Array(b64buf(o.wb)), smooth: o.smooth,
      morphs: o.mb ? new Float32Array(b64buf(o.mb)) : new Float32Array() };
    mesh.frustumCulled = false;
    content.add(mesh);
    meshes.push(mesh);
  }
  const count = data.rig.bones.length;
  const lines = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ vertexColors: true, depthTest: false, transparent: true }));
  lines.geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(count * 6), 3));
  lines.geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(count * 6), 3));
  lines.renderOrder = 10;
  lines.frustumCulled = false;
  content.add(lines);
  const ids = data.objects.map(o => o.id);
  setSlots(ids, true);
  state.rigView = { key, bones: data.rig.bones, mats: new Float32Array(b64buf(data.matrices)), meshes, lines, ids,
    clip: data.clip, frames: data.clip?.frames || 0, fps: data.clip?.fps || 30, live: null,
    morphs: data.morphs || [], morphWeights: new Float32Array(b64buf(data.morph_weights || "")), morphOverride: {} };
}

// Shape-key weights at the current frame; a slider being dragged overrides the clip.
function morphWeightsNow(view) {
  const count = view.morphs.length;
  if (!count) return null;
  const f = Math.min(view.frames, Math.max(0, Math.floor(state.frame)));
  return view.morphs.map((name, m) => view.morphOverride[name] ?? view.morphWeights[f * count + m] ?? 0);
}

async function syncRigView(force) {
  const rig = state.tab === "rig" && state.mode === "object" ? rigOf(state.payload) : null;
  const clip = rig ? clipOf(rig) : null;
  const key = rig ? `${state.name}|${rig.id}|${clip?.id || ""}|${state.geom}` : "";
  if (!force && key === (state.rigView?.key || "")) return;
  const sequence = ++rigSequence;
  if (!rig) { dropRigView(); return; }
  let data;
  try { data = await rpc("forge_call", { op: "rig_preview", name: state.name, params: { rig: rig.id, clip: clip?.id || "" }, actor: "user" }); }
  catch (err) { setStatus(err.message || String(err), true); return; }
  if (sequence !== rigSequence) return;
  if (!data || data.ok === false) { setStatus(data?.error || "Could not load the rig", true); return; }
  dropRigView();
  buildRigView(data, key);
  state.frame = Math.min(state.frame, state.rigView.frames);
  applyPose();
  attachGizmo();
  if (state.tab === "rig") paintPanels(false);  // the shape-key rows come from this view
}

function boneMatrix(view, j, target) {
  const f = Math.min(view.frames, Math.max(0, Math.floor(state.frame)));
  target.fromArray(view.mats, (f * view.bones.length + j) * 16);
  if (view.live?.set.has(j)) target.premultiply(view.live.delta);
  return target;
}

function applyPose() {
  const view = state.rigView;
  if (!view) return;
  const mats = view.bones.map((_, j) => boneMatrix(view, j, new THREE.Matrix4()).elements);
  const shapes = morphWeightsNow(view);
  for (const mesh of view.meshes) {
    const { rest, joints, weights, morphs, index, corners } = mesh.userData;
    const n = rest.length / 3;
    const out = corners ? mesh.userData.posed : mesh.geometry.attributes.position.array;
    for (let i = 0; i < n; i++) {
      let x = rest[i * 3], y = rest[i * 3 + 1], z = rest[i * 3 + 2];
      if (shapes) for (let m = 0; m < shapes.length; m++) {  // shape keys first, then bones, as UEFN does
        const w = shapes[m];
        if (!w) continue;
        const o = (m * n + i) * 3;
        x += morphs[o] * w; y += morphs[o + 1] * w; z += morphs[o + 2] * w;
      }
      let ox = 0, oy = 0, oz = 0;
      for (let k = 0; k < 4; k++) {
        const w = weights[i * 4 + k];
        if (!w) continue;
        const e = mats[joints[i * 4 + k]];
        ox += w * (e[0] * x + e[4] * y + e[8] * z + e[12]);
        oy += w * (e[1] * x + e[5] * y + e[9] * z + e[13]);
        oz += w * (e[2] * x + e[6] * y + e[10] * z + e[14]);
      }
      out[i * 3] = ox; out[i * 3 + 1] = oy; out[i * 3 + 2] = oz;
    }
    if (corners) {
      const pos = mesh.geometry.attributes.position.array;
      for (let k = 0; k < index.length; k++) { const v = index[k] * 3; pos[k * 3] = out[v]; pos[k * 3 + 1] = out[v + 1]; pos[k * 3 + 2] = out[v + 2]; }
    }
    mesh.geometry.attributes.position.needsUpdate = true;
    if (mesh.userData.smooth || corners) mesh.geometry.computeVertexNormals();
  }
  const lp = view.lines.geometry.attributes.position.array;
  const lc = view.lines.geometry.attributes.color.array;
  view.bones.forEach((bone, j) => {
    _boneM.fromArray(mats[j]);
    _tmpV.fromArray(bone.head).applyMatrix4(_boneM).toArray(lp, j * 6);
    _tmpV.fromArray(bone.tail).applyMatrix4(_boneM).toArray(lp, j * 6 + 3);
    const color = bone.name === state.bone ? BONE_ON : BONE_IDLE;
    color.toArray(lc, j * 6);
    color.toArray(lc, j * 6 + 3);
  });
  view.lines.geometry.attributes.position.needsUpdate = true;
  view.lines.geometry.attributes.color.needsUpdate = true;
  if (gizmo.object === bonePivot && !state.dragging) placeBonePivot();
  const label = document.getElementById("rig-frame");
  if (label) label.textContent = `${Math.round(state.frame)} / ${view.frames}`;
  const scrub = document.getElementById("scrub");
  if (scrub && document.activeElement !== scrub) scrub.value = String(Math.round(state.frame));
  const weights = morphWeightsNow(view);
  if (weights) document.querySelectorAll("[data-morph]").forEach((slider, m) => { if (document.activeElement !== slider) slider.value = String(weights[m]); });
}

function placeBonePivot() {
  const view = state.rigView;
  const j = view ? view.bones.findIndex(b => b.name === state.bone) : -1;
  if (j < 0) return false;
  boneMatrix(view, j, _boneM).decompose(_tmpV, bonePivot.quaternion, _tmpS);
  bonePivot.position.fromArray(view.bones[j].head).applyMatrix4(_boneM);
  bonePivot.scale.set(1, 1, 1);
  bonePivot.updateMatrixWorld(true);
  return true;
}

function boneFamily(view, j) {
  const out = new Set([j]);
  let grew = true;
  while (grew) {
    grew = false;
    view.bones.forEach((b, i) => {
      const parent = view.bones.findIndex(p => p.name === b.parent);
      if (!out.has(i) && out.has(parent)) { out.add(i); grew = true; }
    });
  }
  return out;
}

function boneDrag(active) {
  state.dragging = active;
  orbit.enabled = !active && !state.flying;
  const view = state.rigView;
  const j = view ? view.bones.findIndex(b => b.name === state.bone) : -1;
  if (!view || j < 0 || !view.clip) return;
  if (active) {
    state.epoch++;
    state.playing = false;
    view.dragFrom = bonePivot.quaternion.clone();
    view.dragAt = bonePivot.position.clone();
    view.dragSet = boneFamily(view, j);
    return;
  }
  if (state.ik) {
    view.dragFrom = null;
    op("anim", { action: "ik", rig: rigOf(state.payload)?.id, clip: view.clip.id, bone: view.bones[j].name,
      target: bonePivot.position.toArray(), frame: Math.round(state.frame), chain: state.ikChain || 2 });
    return;
  }
  // The key is the bone's rotation relative to its parent's posed frame (the server's convention).
  const parent = view.bones.findIndex(b => b.name === view.bones[j].parent);
  const parentQ = new THREE.Quaternion();
  if (parent >= 0) boneMatrix(view, parent, _parentM).decompose(_tmpV, parentQ, _tmpS);
  const local = parentQ.invert().multiply(bonePivot.quaternion);
  const euler = new THREE.Euler().setFromQuaternion(local, "ZYX");
  const rot = [euler.x, euler.y, euler.z].map(v => Math.round(THREE.MathUtils.radToDeg(v) * 100) / 100);
  view.dragFrom = null;
  op("anim", { action: "key", rig: rigOf(state.payload)?.id, clip: view.clip.id, bone: view.bones[j].name, frame: Math.round(state.frame), rot })
    .then(result => { if (result?.ok === false && state.rigView === view) { view.live = null; applyPose(); } });
}

function boneDragMove() {
  const view = state.rigView;
  if (!view?.dragFrom || state.ik) return;  // IK solves on release, on the server
  // Every descendant turns rigidly about the grabbed bone's posed head.
  const turn = new THREE.Matrix4().makeRotationFromQuaternion(bonePivot.quaternion.clone().multiply(view.dragFrom.clone().invert()));
  const at = view.dragAt;
  const delta = new THREE.Matrix4().makeTranslation(at.x, at.y, at.z).multiply(turn).multiply(new THREE.Matrix4().makeTranslation(-at.x, -at.y, -at.z));
  view.live = { set: view.dragSet, delta };
  applyPose();
}

function togglePlay() {
  const view = state.rigView;
  if (!view?.frames) return;
  if (!state.playing && state.frame >= view.frames && view.clip?.loop === false) state.frame = 0;
  state.playing = !state.playing;
  if (state.playing) view.morphOverride = {};
  const button = document.getElementById("play");
  if (button) { button.textContent = state.playing ? "Pause" : "Play"; button.title = button.textContent; }
}

// ---- Brush modes: sculpt, weights, blend paint, texture paint -----------------------------------
// The selected object swaps to a "work mesh" (its instance hides, like the rig view). A stroke edits
// the work mesh live with brush.js and, on release, ships the exact values it produced as one undo step.
const BRUSH_MODES = new Set(["sculpt", "weight", "vpaint", "tpaint"]);
const BRUSH_KINDS = { sculpt: Brush.SCULPT, weight: Brush.PAINT, vpaint: Brush.PAINT, tpaint: ["paint"] };
const brushRing = new THREE.Mesh(new THREE.RingGeometry(0.93, 1, 48), new THREE.MeshBasicMaterial({ color: WHITE, transparent: true, opacity: 0.85, depthTest: false, side: THREE.DoubleSide }));
brushRing.renderOrder = 12;
brushRing.visible = false;
scene3.add(brushRing);
let workSequence = 0;

function toB64(typed) {
  const bytes = new Uint8Array(typed.buffer, typed.byteOffset, typed.byteLength);
  let out = "";
  for (let i = 0; i < bytes.length; i += 32768) out += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return btoa(out);
}

function brush() {
  const value = id => document.getElementById(id);
  return { kind: value("brush-kind").value, target: value("brush-target").value, radius: Math.max(0.5, Number(value("brush-radius").value) || 20),
    strength: Number(value("brush-strength").value) || 0.5, mirror: value("brush-mirror").checked, invert: value("brush-invert").checked,
    color: value("brush-color").value };
}

// The vertex attribute a mode reads and writes: "" base positions, sk:<key>, wt:<bone>, col:blend.
function brushAttr(mode = state.mode) {
  const target = document.getElementById("brush-target").value;
  if (mode === "sculpt") return target ? `sk:${target}` : "";
  if (mode === "weight") return target ? `wt:${target}` : "";
  if (mode === "vpaint") return "col:blend";
  return "";
}

function fillBrushBar() {
  const bar = document.getElementById("brush-bar");
  bar.hidden = !BRUSH_MODES.has(state.mode);
  if (bar.hidden) return;
  const kind = document.getElementById("brush-kind");
  const kinds = BRUSH_KINDS[state.mode];
  if ([...kind.options].map(o => o.value).join() !== kinds.join()) kind.innerHTML = kinds.map(k => `<option>${k}</option>`).join("");
  const target = document.getElementById("brush-target");
  const node = nodeById(state.payload?.selection?.object);
  const materials = state.payload?.materials || [];
  let options = [];
  if (state.mode === "sculpt") options = [["", "Base mesh"], ...(state.work?.shapes || []).map(k => [k, `Shape: ${k}`])];
  else if (state.mode === "weight") {
    const rig = (state.payload?.rigs || []).find(r => r.id === node?.skin?.rig) || rigOf(state.payload);
    options = (rig?.bones || []).filter(b => !/^(forge_)?root$/i.test(b.name)).map(b => [b.name, b.name]);
  } else if (state.mode === "vpaint") {
    const mat = materials.find(m => m.id === node?.material);
    options = ["r", "g", "b"].map((ch, i) => [ch, `${ch.toUpperCase()}: ${materials.find(m => m.id === mat?.layers?.[i])?.name || "no layer"}`]);
  }
  const html = options.map(([v, label]) => `<option value="${esc(v)}">${esc(label)}</option>`).join("");
  if (target.innerHTML !== html || state.pendingTarget) {
    const keep = state.pendingTarget || target.value || (state.mode === "weight" ? state.bone : "");
    target.innerHTML = html;
    if ([...target.options].some(o => o.value === keep)) {
      target.value = keep;
      if (keep === state.pendingTarget) state.pendingTarget = "";
    }
  }
  target.hidden = !options.length;
  document.getElementById("brush-color").hidden = state.mode !== "tpaint";
  for (const id of ["brush-add", "brush-sub", "brush-dec"]) document.getElementById(id).hidden = state.mode !== "sculpt";
  document.getElementById("brush-invert").parentElement.hidden = state.mode !== "sculpt";
}

function dropWork() {
  const work = state.work;
  if (!work) return;
  state.work = null;
  brushRing.visible = false;
  work.mesh.geometry.dispose();
  work.mesh.material.dispose();
  work.texture?.dispose();
  work.mesh.removeFromParent();
  setSlots([work.id], false);
}

async function syncWork(force) {
  const id = state.payload?.selection?.object;
  const node = nodeById(id);
  const active = BRUSH_MODES.has(state.mode) && node?.type === "object";
  fillBrushBar();
  const key = active ? `${state.name}|${id}|${state.mode}|${brushAttr()}` : "";
  const work = state.work;
  const current = work ? key === work.key && work.geom >= state.geom : !key;
  if (work?.stroking || (!force && current)) return;
  const sequence = ++workSequence;
  if (!active) { dropWork(); if (BRUSH_MODES.has(state.mode)) setStatus("Select an object to paint or sculpt"); return; }
  if (state.mode === "weight" && !node.skin?.paint) {
    if (!(state.payload.rigs || []).length) { setStatus("Weights need a rig: add one in the Rig tab, then bind this object", true); return; }
    await op("rig", { action: "weights", ids: [id], rig: node.skin?.rig || rigOf(state.payload).id });  // seed from its binding
    return;  // the refreshed payload calls back in with a painted skin
  }
  const mat = (state.payload.materials || []).find(m => m.id === node.material);
  if (state.mode === "tpaint" && !mat?.textures?.base_color) {
    await op("paint", { id, mode: "texture", material: node.material, fill: "all", color: mat?.base_color || [0.75, 0.75, 0.78], size: 1024 });
    return;
  }
  const attr = brushAttr();
  let data;
  try { data = await rpc("forge_call", { op: "mesh_data", name: state.name, params: { id, attrs: attr ? [attr] : [] }, actor: "user" }); }
  catch (err) { setStatus(err.message || String(err), true); return; }
  // A stroke that began meanwhile keeps the mesh under the brush; its commit (or its end) reloads.
  if (sequence !== workSequence || state.work?.stroking) return;
  if (!data || data.ok === false) { setStatus(data?.error || "Could not open the mesh", true); return; }
  if (data.name !== state.name) return;  // the user switched scenes while this loaded
  if (data.modifiers) { await op("modifier", { action: "apply" }); return; }  // brush on the real vertices
  dropWork();
  await buildWork(data, key, attr, mat);
  fillBrushBar();
}

async function buildWork(data, key, attr, mat) {
  const id = data.id;
  const positions = new Float32Array(b64buf(data.pb));
  const index = new Uint32Array(b64buf(data.ib));
  const count = data.count;
  const slot = (state.slots.get(id) || []).find(s => s.row[0] === id);
  const matrix = new THREE.Matrix4();
  if (slot) { composeRow(slot.row, matrix); matrix.premultiply(state.world.get(slot.row[2]) || _ident); }
  const work = { key, geom: data.geom, id, attr, positions, index, count, matrix, inverse: matrix.clone().invert(), changed: new Set(), stroking: false,
    shapes: (data.names || []).filter(n => n.startsWith("sk:")).map(n => n.slice(3)), matId: mat?.id || "" };
  work.scale = new THREE.Vector3().setFromMatrixScale(matrix).toArray().reduce((a, b) => a + b, 0) / 3 || 1;
  const stored = attr && data.attrs?.[attr] ? new Float32Array(b64buf(data.attrs[attr])) : null;
  if (attr.startsWith("sk:")) {
    work.basis = positions.slice();
    if (stored) for (let i = 0; i < positions.length; i++) positions[i] += stored[i];
  } else if (attr.startsWith("wt:")) {
    work.width = 1;
    work.values = stored || new Float32Array(count);
  } else if (attr === "col:blend") {
    work.width = 4;
    work.values = stored || Float32Array.from({ length: count * 4 }, (_, i) => (i % 4 === 3 ? 1 : 0));
  }
  const geo = new THREE.BufferGeometry();
  let material;
  if (state.mode === "tpaint") {
    const uv = new Float32Array(b64buf(data.ub));
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(index.length * 3), 3));
    geo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
    work.tris = Brush.paintTriangles(positions, index, uv);
    const res = await rpc("forge_texture", { name: state.name, file: mat.textures.base_color });
    const img = await new Promise((resolve, reject) => { const i = new Image(); i.onload = () => resolve(i); i.onerror = reject; i.src = `data:${res.mime || "image/png"};base64,${res.base64}`; });
    work.canvas = Object.assign(document.createElement("canvas"), { width: img.width, height: img.height });
    work.ctx = work.canvas.getContext("2d", { willReadFrequently: true });
    work.ctx.drawImage(img, 0, 0);
    work.image = work.ctx.getImageData(0, 0, img.width, img.height);
    work.texture = new THREE.CanvasTexture(work.canvas);
    work.texture.colorSpace = THREE.SRGBColorSpace;
    material = new THREE.MeshStandardMaterial({ map: work.texture, roughness: mat.roughness ?? 0.5, metalness: mat.metallic ?? 0 });
  } else {
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geo.setIndex(new THREE.BufferAttribute(index, 1));
    if (work.values) {
      geo.setAttribute("color", new THREE.BufferAttribute(new Float32Array(count * 3), 3));
      material = new THREE.MeshLambertMaterial({ vertexColors: true });
    } else {
      material = new THREE.MeshStandardMaterial({ color: colorOf(mat?.id, state.payload.materials), roughness: 0.6, metalness: 0.05 });
    }
  }
  work.mesh = new THREE.Mesh(geo, material);
  work.mesh.matrixAutoUpdate = false;
  work.mesh.matrix.copy(matrix);
  work.mesh.frustumCulled = false;
  content.add(work.mesh);
  setSlots([id], true);
  state.work = work;
  refreshWork(true);
}

// Push the work mesh's arrays to the GPU: positions, normals, weight heat or blend colours, or the canvas.
function refreshWork(all) {
  const work = state.work;
  if (!work) return;
  const geo = work.mesh.geometry;
  if (state.mode === "tpaint") {
    const pos = geo.attributes.position.array, P = work.positions, idx = work.index;
    if (all) for (let k = 0; k < idx.length; k++) { const v = idx[k] * 3; pos[k * 3] = P[v]; pos[k * 3 + 1] = P[v + 1]; pos[k * 3 + 2] = P[v + 2]; }
    geo.attributes.position.needsUpdate = true;
    if (all) geo.computeVertexNormals();
    work.ctx.putImageData(work.image, 0, 0);
    work.texture.needsUpdate = true;
    return;
  }
  geo.attributes.position.needsUpdate = true;
  geo.computeVertexNormals();
  if (!work.values) return;
  const colors = geo.attributes.color.array;
  if (state.mode === "weight") {
    for (let i = 0; i < work.count; i++) colors.set(Brush.heat(work.values[i]), i * 3);
  } else {
    const materials = state.payload.materials || [];
    const mat = materials.find(m => m.id === work.matId);
    const base = colorOf(mat?.id, materials).toArray();
    const layers = [0, 1, 2].map(i => { const l = materials.find(m => m.id === mat?.layers?.[i]); return l ? (l.base_color || [0.75, 0.75, 0.78]) : base; });
    for (let i = 0; i < work.count; i++) {
      let c = base;
      for (let ch = 0; ch < 3; ch++) {
        const t = work.values[i * 4 + ch];
        if (t) c = [0, 1, 2].map(a => c[a] + (layers[ch][a] - c[a]) * t);
      }
      colors.set(c, i * 3);
    }
  }
  geo.attributes.color.needsUpdate = true;
}

function workHit(ev) {
  const work = state.work;
  if (!work) return null;
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.set(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  work.mesh.updateMatrixWorld(true);
  const hit = raycaster.intersectObject(work.mesh, false)[0];
  if (!hit) return null;
  return { world: hit.point.clone(), local: hit.point.clone().applyMatrix4(work.inverse).toArray(), normal: hit.face ? hit.face.normal.clone().transformDirection(work.mesh.matrixWorld) : new THREE.Vector3(0, 0, 1) };
}

function showRing(hit) {
  if (!hit) { brushRing.visible = false; return; }
  brushRing.visible = true;
  brushRing.position.copy(hit.world);
  brushRing.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), hit.normal);
  brushRing.scale.setScalar(brush().radius);
}

function dab(local) {
  const work = state.work, b = brush();
  const r = b.radius / work.scale;
  for (const c of Brush.mirrored(local, b.mirror ? 0 : -1)) {
    if (state.mode === "sculpt") {
      const cm = ["draw", "clay", "inflate", "crease"].includes(b.kind);
      const s = (cm ? b.strength * r * 0.25 : b.strength) * (b.invert ? -1 : 1);
      work.normals = work.normals || Brush.vertexNormals(work.positions, work.index);
      work.adj = work.adj || Brush.adjacency(work.index, work.count);
      Brush.sculptDab(work.positions, work.normals, work.adj, b.kind, c, r, s, work.changed);
      work.normals = null;
    } else if (state.mode === "tpaint") {
      const hex = b.color.replace("#", "");
      Brush.textureDab(work.image, work.tris, c, r, [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16)), b.strength);
      work.changed.add(0);
    } else {
      work.adj = work.adj || Brush.adjacency(work.index, work.count);
      const channel = state.mode === "vpaint" ? "rgb".indexOf(b.target || "r") : 0;
      Brush.paintDab(work.positions, work.values, work.width, channel, work.adj, b.kind, c, r, b.strength, 1, work.changed);
    }
  }
  refreshWork(false);
}

function startStroke(ev) {
  const work = state.work, hit = workHit(ev);
  if (!work || !hit) return false;
  if (state.mode === "weight" && !work.attr) { setStatus("Pick a bone to paint in the brush bar", true); return false; }
  work.stroking = true;
  state.dragging = true;  // polls wait until the stroke is committed
  work.last = hit.local;
  if (state.mode === "sculpt" && brush().kind === "grab") {
    work.grab = { caught: Brush.grabStart(work.positions, hit.local, brush().radius / work.scale), start: hit.world.clone(),
      plane: new THREE.Plane().setFromNormalAndCoplanarPoint(camera.getWorldDirection(new THREE.Vector3()).negate(), hit.world) };
  } else dab(hit.local);
  try { renderer.domElement.setPointerCapture(ev.pointerId); } catch { /* already captured */ }
  return true;
}

function moveStroke(ev) {
  const work = state.work;
  if (work?.grab) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const at = raycaster.ray.intersectPlane(work.grab.plane, new THREE.Vector3());
    if (!at) return;
    // World drag to mesh space: the inverse's linear part, which also undoes the object's scale.
    const delta = at.sub(work.grab.start).applyMatrix3(new THREE.Matrix3().setFromMatrix4(work.inverse));
    Brush.grabMove(work.positions, work.grab.caught, delta.toArray(), work.changed);
    refreshWork(false);
    return;
  }
  const hit = workHit(ev);
  showRing(hit);
  if (!hit) return;
  const step = brush().radius / work.scale * 0.25;
  const from = work.last, to = hit.local;
  const dist = Math.hypot(to[0] - from[0], to[1] - from[1], to[2] - from[2]);
  if (dist < step) return;
  const n = Math.ceil(dist / step);
  for (let k = 1; k <= n; k++) dab([0, 1, 2].map(a => from[a] + (to[a] - from[a]) * k / n));
  work.last = to;
}

async function endStroke(ev) {
  const work = state.work;
  if (!work?.stroking) return;
  work.stroking = false;
  work.grab = null;
  state.dragging = false;
  try { renderer.domElement.releasePointerCapture(ev.pointerId); } catch { /* released */ }
  if (!work.changed.size) { syncWork(); return; }
  refreshWork(true);
  const changed = [...work.changed].sort((a, b) => a - b);
  work.changed = new Set();
  let result;
  if (state.mode === "tpaint") {
    result = await op("texture", { png_base64: work.canvas.toDataURL("image/png"), material: work.matId, tileable: false, derive_maps: false });
  } else {
    const idx = Uint32Array.from(changed);
    let values;
    if (state.mode === "sculpt") {
      values = new Float32Array(idx.length * 3);
      idx.forEach((i, k) => { for (let a = 0; a < 3; a++) values[k * 3 + a] = work.positions[i * 3 + a] - (work.basis ? work.basis[i * 3 + a] : 0); });
    } else {
      values = new Float32Array(idx.length * work.width);
      idx.forEach((i, k) => values.set(work.values.subarray(i * work.width, i * work.width + work.width), k * work.width));
    }
    result = await op("values", { id: work.id, target: work.attr || "pos", count: work.count, idx: toB64(idx), values: toB64(values) });
  }
  // Nothing else changed: the server now holds exactly this mesh, so relabel it rather than reload it.
  if (result?.geom === work.geom + 1) work.geom = result.geom;
}

function buildBatches() {
  const payload = state.payload;
  const oldBatches = new Map(state.batches.map(batch => [batch.userData.batchKey, batch]));
  const oldWorld = state.world || new Map();
  clearView(true);
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
  state.children = new Map();
  for (const node of byId.values()) {
    if (!state.children.has(node.parent)) state.children.set(node.parent, []);
    state.children.get(node.parent).push(node.id);
  }
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
    const mat = materialsForProto(proto, rows[0][12], payload.materials);
    for (let c = 0; c < rows.length; c += CHUNK) {
      const slice = rows.slice(c, c + CHUNK);
      const batchKey = `${rows[0][1]}\0${rows[0][12] || ""}\0${c}`;
      const candidate = oldBatches.get(batchKey);
      const reuse = candidate && candidate.geometry === geo && candidate.material === mat && candidate.count === slice.length;
      const batch = reuse ? candidate : new THREE.InstancedMesh(geo, mat, slice.length);
      if (reuse) oldBatches.delete(batchKey);
      else { batch.instanceMatrix.setUsage(THREE.DynamicDrawUsage); batch.userData.ids = new Array(slice.length); }
      const previousRows = batch.userData.rows || [];
      let changed = !reuse;
      for (let i = 0; i < slice.length; i++) {
        const row = slice[i];
        const previous = previousRows[i];
        const parentWorld = world(row[2]);
        if (!reuse || !previous || previous.some((value, j) => value !== row[j]) || !(oldWorld.get(row[2]) || _ident).equals(parentWorld)) {
          composeRow(row, local);
          _m.multiplyMatrices(parentWorld, local);
          batch.setMatrixAt(i, _m);
          changed = true;
          if (batch.instanceColor) batch.setColorAt(i, WHITE);
        }
        batch.userData.ids[i] = row[0];
        if (!state.parentOf.has(row[0])) state.parentOf.set(row[0], world(row[2]));
        let slots = state.slots.get(row[0]);
        if (!slots) state.slots.set(row[0], (slots = []));
        slots.push({ mesh: batch, index: i, row });
      }
      batch.userData.batchKey = batchKey;
      batch.userData.rows = slice;
      if (changed) {
        batch.instanceMatrix.needsUpdate = true;
        if (batch.instanceColor) batch.instanceColor.needsUpdate = true;
        batch.computeBoundingBox();
        batch.computeBoundingSphere();
      }
      if (!reuse) content.add(batch);
      state.batches.push(batch);
    }
  }
  for (const batch of oldBatches.values()) { batch.removeFromParent(); batch.dispose(); }
  if (pulled) {
    const proto = protos.get(pulled[1]);
    if (proto) {
      const mesh = new THREE.Mesh(geometryFor(proto), materialsForProto(proto, pulled[12], payload.materials));
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
  // Objects the rig view or a brush session draws stay hidden until those views rebuild.
  setSlots([...(state.rigView?.ids || []), ...(state.work ? [state.work.id] : [])], true);
  state.batchEdit = state.mode === "edit";
  state.editId = sel;
  for (const id of state.tinted) tintSlot(id, WHITE);
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
  if (state.rigView && state.tab === "rig") {
    // Rig tab: the gizmo poses the picked bone at the current frame instead of moving objects.
    if (state.bone && state.rigView.clip && state.gizmo !== "select" && placeBonePivot()) {
      gizmo.attach(bonePivot);
      gizmo.setMode(state.ik ? "translate" : "rotate");  // IK: move the hand or foot; the chain above solves
      if (state.ik) gizmo.setTranslationSnap(null); else applySnap();
    } else gizmo.detach();
    return;
  }
  if (gizmo.object === bonePivot) gizmo.detach();
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

function applyTransforms(transforms) {
  const touched = new Set();
  for (const [id, transform] of Object.entries(transforms)) {
    const node = nodeById(id);
    if (!node) continue;
    const before = node.transform;
    const slots = state.slots.get(id) || [];
    // Array placements retain their authored offsets; the shared geometry is untouched.
    for (const slot of slots) if (slot.row[0] === id) {
      for (let axis = 0; axis < 3; axis++) {
        slot.row[3+axis] += transform.pos[axis] - before.pos[axis];
        slot.row[6+axis] = transform.rot[axis];
        slot.row[9+axis] = transform.scale[axis];
      }
    }
    node.transform = structuredClone(transform);
    const descendants = [], pending = [id];
    while (pending.length) {
      const child = pending.pop();
      descendants.push(child);
      state.world.delete(child);
      pending.push(...(state.children.get(child) || []));
    }
    function world(nid) {
      if (!nid) return _ident;
      if (state.world.has(nid)) return state.world.get(nid);
      const n = nodeById(nid);
      if (!n) return _ident;
      const t = n.transform;
      const local = composeRow([0,0,0,...t.pos,...t.rot,...t.scale], new THREE.Matrix4());
      const matrix = new THREE.Matrix4().multiplyMatrices(world(n.parent), local);
      state.world.set(nid, matrix);
      return matrix;
    }
    for (const child of descendants) world(child);
    for (const slot of slots) {
      composeRow(slot.row, _m);
      _m.premultiply(world(slot.row[2]));
      slot.mesh.setMatrixAt(slot.index, _m);
      touched.add(slot.mesh);
    }
  }
  for (const mesh of touched) {
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingBox();
    mesh.computeBoundingSphere();
  }
  attachGizmo();
  paintPanels(false);
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
  const transform = {
    pos: [_pos.x, _pos.y, _pos.z],
    rot: [THREE.MathUtils.radToDeg(_euler.x), THREE.MathUtils.radToDeg(_euler.y), THREE.MathUtils.radToDeg(_euler.z)],
    scale: [_scl.x, _scl.y, _scl.z],
  };
  applyTransforms({ [id]: transform });
  await op("edit", { id, ...transform });
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
  if (!panelVisible || document.hidden) { lastFrame = performance.now(); return; }
  const now = performance.now();
  const dt = Math.min(0.05, (now - lastFrame) / 1000);
  stepFly(dt);
  lastFrame = now;
  const view = state.rigView;
  if (view && state.playing && view.frames && !state.dragging) {
    state.frame += dt * view.fps;
    if (state.frame > view.frames) {
      if (view.clip?.loop === false) { state.frame = view.frames; togglePlay(); }
      else state.frame %= view.frames;
    }
    applyPose();
  }
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
  if (!state.flying) {
    if (state.work?.stroking) moveStroke(ev);
    else if (state.work) showRing(workHit(ev));
    return;
  }
  const dx = ev.movementX || 0;
  const dy = ev.movementY || 0;
  if (!dx && !dy) return;
  look(dx, dy);
});
renderer.domElement.addEventListener("pointerleave", () => { if (!state.work?.stroking) brushRing.visible = false; });
renderer.domElement.addEventListener("pointerup", (ev) => {
  if (ev.button === 2) endFly(ev);
  if (ev.button === 0) endStroke(ev);
});
renderer.domElement.addEventListener("pointercancel", (ev) => endFly(ev));
renderer.domElement.addEventListener("lostpointercapture", (ev) => {
  if (state.flying) endFly(ev);
  endStroke(ev);  // a cancelled pointer still commits and ends the stroke
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

renderer.domElement.addEventListener("pointerdown", async (ev) => {
  if (ev.button === 2) { beginFly(ev); return; }
  if (ev.button !== 0 || state.dragging || state.flying || gizmo.axis) return;
  if (BRUSH_MODES.has(state.mode) && startStroke(ev)) return;  // a miss falls through to picking another object
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const additive = ev.shiftKey || ev.ctrlKey || ev.metaKey;
  if (state.mode === "edit" && state.pick) {
    const pickedMesh = state.pick, epoch = state.epoch, selectMode = state.selectMode;
    const sequence = state.pickSequence = (state.pickSequence || 0) + 1;
    try {
      const hit = await meshTask("pick", {key: pickedMesh.geometry.userData.workerKey,
        matrix: pickedMesh.matrixWorld.toArray(), origin: raycaster.ray.origin.toArray(),
        direction: raycaster.ray.direction.toArray(), mode: selectMode});
      if (sequence !== state.pickSequence || epoch !== state.epoch || state.pick !== pickedMesh || selectMode !== state.selectMode) return;
      if (!hit) choose(null, false);
      else if (selectMode === "face") choose(pickedMesh.userData.id, additive, [hit.face]);
      else choose(pickedMesh.userData.id, additive, undefined, hit);
    } catch (error) { setStatus(error.message, true); }
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
      } else if (result.transforms && state.mode === "object") {
        if (epoch === state.epoch) applyTransforms(result.transforms);
        state.rev = state.payload.rev = result.rev;
        state.geom = state.payload.geom = result.geom;
        setStatus(result.summary || "Ready");
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
      state.geom = -1; // Failed optimistic movement must reload authoritative transforms.
      return { ok: false, error: err.message || String(err) };
    } finally { state.pending--; }
  };
  const task = operations.then(run);
  operations = task.catch(() => {});
  return task;
}

function resetScene() {
  state.loadFailed = "";
  saveSession();
  dropRigView();
  dropWork();
  for (const tex of texCache.values()) tex.dispose();
  texCache.clear();
  state.playing = false;
  state.frame = 0;
  state.bone = "";
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
    ? `Building normals for ${smoothN.toLocaleString()} smooth meshes so circles match Blender. Flat pieces skip this. Dense meshes are prepared in the background.`
    : "Nothing in this scene is smooth-shaded, so this step is quick.");
  const changed = protos.filter(p => !cachedGeometry(p));
  if (changed.length) await frame();
  for (let i = 0; i < changed.length; i++) {
    await prepareProto(changed[i]);
    if (i % 12 === 11) {
      loadStep("smooth", `Mesh ${(i + 1).toLocaleString()} of ${protos.length.toLocaleString()}. Smooth meshes take longer because their normals are built here.`);
      await frame();
    }
  }
  await meshTask("retain", { keys: protos.map(p => p.id + ":" + p.revision) });
}

async function attachParts(name, payload, stale) {
  for (const proto of payload.prototypes || []) {
    if (!proto.parts) continue;
    const size = proto.part_size;
    for (const [field, length] of Object.entries(proto.parts)) {
      const count = Math.ceil(length / size);
      let acc = "";
      for (let i = 0; i < count; i++) {
        if (stale()) return;
        loadStep("pack", `Sending ${Number(proto.tris || 0).toLocaleString()} triangles, piece ${i + 1} of ${count}. Every triangle is included.`);
        const part = await rpc("forge_call", { op: "scene_geom_part", name, params: { id: proto.id, field, index: i }, actor: "user" });
        if (stale()) return;
        if (!part || part.ok === false || typeof part.data !== "string") throw new Error(part?.error || "Could not load mesh");
        acc += part.data;
      }
      if (acc.length !== length) throw new Error("Mesh arrived short");
      proto[field] = acc;
    }
    delete proto.parts;
    delete proto.part_size;
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

// Small host-owned preferences survive sandbox iframe destruction and app restarts.
let sessionReady = false, sessionSaved = "", sessionRestore = null;
function saveSession() {
  if (!sessionReady || !state.name || state.payload?.name !== state.name) return;
  const value = JSON.stringify({name:state.name, camera:camera.position.toArray(), target:orbit.target.toArray(),
    tab:state.tab, gizmo:state.gizmo, collapsed:[...state.collapsed], locked:[...state.locked],
    picked:[...state.picked], search:state.search, follow:state.follow,
    snap:["snap","rot-snap","scale-snap"].map(id=>document.getElementById(id).value)});
  if (value === sessionSaved) return;
  sessionSaved = value;
  call("prefs.set", {id:"forgeSession", value}).catch(() => { if(sessionSaved===value)sessionSaved=""; });
}
async function restoreSession() {
  try {
    const saved = await call("prefs.get", {id:"forgeSession"});
    const value = JSON.parse(saved.value || "null");
    if(value && typeof value.name === "string") sessionRestore=value;
  } catch { /* Missing or malformed preferences must not prevent opening Forge. */ }
  sessionReady = true;
}
function restoreView() {
  const value = sessionRestore;
  if(!value || value.name!==state.name)return;
  sessionRestore=null;
  const vector=v=>Array.isArray(v)&&v.length===3&&v.every(Number.isFinite);
  if(vector(value.camera)&&vector(value.target)){camera.position.fromArray(value.camera);orbit.target.fromArray(value.target);orbit.update();}
  if(["props","layers","materials","rig","check","activity","export"].includes(value.tab))state.tab=value.tab;
  if(["select","translate","rotate","scale"].includes(value.gizmo))state.gizmo=value.gizmo;
  const ids=new Set((state.payload.nodes||[]).map(n=>n.id));
  for(const field of ["collapsed","locked","picked"])if(Array.isArray(value[field]))state[field]=new Set(value[field].filter(id=>ids.has(id)));
  state.search=typeof value.search==="string"?value.search:"";
  state.follow=value.follow===true;document.getElementById("follow").checked=state.follow;
  ["snap","rot-snap","scale-snap"].forEach((id,i)=>{const v=value.snap?.[i];if(v!==undefined&&Number.isFinite(Number(v))&&Number(v)>=0)document.getElementById(id).value=v;});
  applySnap();attachGizmo();markSelection(true);paintPanels();syncChrome();
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
  const preferred = select || state.name || sessionRestore?.name;
  state.name = scenes.some(s => s.name === preferred) ? preferred : scenes[0].name;
  sel.value = state.name;
}

async function poll() {
  if (!panelVisible || document.hidden || !state.name || state.dragging || state.polling || state.pending) return;
  const sceneName = state.name;
  const epoch = state.epoch;
  const stale = () => state.name !== sceneName || epoch !== state.epoch || state.pending || state.dragging;
  state.polling = true;
  try {
    const info = await rpc("forge_call", { op: "scene_get", name: state.name, params: { brief: true }, actor: "user" });
    if (stale()) return;
    if (!info || info.ok === false) throw new Error(info?.error || "Could not read scene");
    if (info.rev === state.rev && state.payload?.name === sceneName) return;
    if (!state.payload || state.payload.name !== sceneName || info.geom !== state.geom) {
      if (state.loadFailed === sceneName) return;
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
      const known = state.payload?.name === state.name ? Object.fromEntries((state.payload.prototypes || []).map(p => [p.id, p.revision])) : {};
      const payload = await rpc("forge_call", { op: "scene_payload", name: state.name, params: { known, since_geom: state.payload?.name === state.name ? state.geom : undefined }, actor: "user" });
      if (stale()) return;
      if (!payload || payload.ok === false) throw new Error(payload?.error || "Could not load scene");
      if (payload.node_patch) {
        if (payload.base_geom !== state.geom) throw new Error("Scene changed during update; refresh required");
        function patch(items, change, key) {
          const map = new Map(items.map(item => [key(item), item]));
          for (const id of change.remove) map.delete(id);
          for (const item of change.upsert) map.set(key(item), item);
          return change.order ? change.order.map(id => map.get(id)) : [...map.values()];
        }
        const groups = new Map();
        for (const row of state.payload.instances) {
          if (!groups.has(row[0])) groups.set(row[0], []);
          groups.get(row[0]).push(row);
        }
        payload.nodes = patch(state.payload.nodes, payload.node_patch, n => n.id);
        payload.instances = patch([...groups.values()], payload.instance_patch, rows => rows[0][0]).flat();
      }
      const previous = new Map((state.payload?.prototypes || []).map(p => [p.id, p]));
      payload.prototypes = (payload.prototypes || []).map(p => {
        if (!p.cached) return p;
        const cached = previous.get(p.id);
        if (!cached || cached.revision !== p.revision) throw new Error("Mesh cache changed; reopen this scene");
        return cached;
      });
      await attachParts(sceneName, payload, stale);
      if (stale()) return;
      await prepareGeometry(payload);
      if (switching) {
        const pieces = (payload.instances || []).length;
        const rows = (payload.nodes || []).length;
        loadStep("place", `Placing ${pieces.toLocaleString()} pieces and indexing ${rows.toLocaleString()} Outliner entries. The panel stays here until the last piece is in, which is why a stadium pauses at the end.`);
        await frame();
      }
      if (stale()) return;
      state.loadFailed = "";
      state.rev = payload.rev;
      rebuild(payload);
      if (switching) { restoreView(); saveSession(); }
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
    state.loadFailed = sceneName;
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
  if (mode && document.activeElement !== mode) mode.value = state.mode;
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
  else if (state.tab === "rig") panel.innerHTML = rigPanel(p);
  else panel.innerHTML = exportPanel();
  bindPanel();
  bindRigPanel();
  syncChrome();
  syncRigView();
  syncWork();
  if (state.tab === "materials") paintUvView();
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
  // List params (lathe profile, extrude points) stay agent-edited; a number box would overwrite them with 0.
  const primitive = n.primitive ? `<h2>Primitive · ${esc(n.primitive.kind)}</h2>` + Object.entries(n.primitive.params || {}).filter(([, v]) => typeof v === "number").map(([k,v]) => `<div class="row"><label>${esc(k)}</label><input type="number" step="any" data-param="${esc(k)}" value="${esc(v)}" /></div>`).join("") : "";
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
    <div class="row"><input id="tex-file" type="file" accept="image/*" aria-label="Texture image" /><button id="tex-import" type="button">Import</button></div>
    <div class="row"><input id="prompt" placeholder="worn painted metal, tileable" aria-label="Texture prompt" style="flex:1" /></div>
    <div class="row"><button id="gen" type="button">Generate maps</button><button id="tex-paint" type="button" title="Paint this material's texture on the selected object">Paint it</button></div>
    <h2>Blend layers</h2>
    ${["r", "g", "b"].map((ch, i) => `<div class="row"><label for="layer-${ch}">${ch.toUpperCase()} layer</label><select id="layer-${ch}" data-layer="${i}"><option value="">None</option>${(p.materials || []).filter(m => m.id !== current?.id).map(m => `<option value="${esc(m.id)}"${current?.layers?.[i] === m.id ? " selected" : ""}>${esc(m.name)}</option>`).join("")}</select></div>`).join("")}
    <div class="row"><button id="blend-paint" type="button" title="Paint where the layers show on the selected object">Paint layers</button></div>
    <p class="dim">A blend material shows up to three other materials over itself wherever Blend paint puts R, G, or B. Send builds it on M_Forge_Blend.</p>
    <h2>UVs · selected object</h2>
    <canvas id="uv-view" class="uv-canvas" width="256" height="256" aria-label="UV layout"></canvas>
    <div class="row"><button id="uv-smart" type="button" title="Unwrap into non-overlapping charts for painting">Unwrap</button><button id="uv-box" type="button">Box</button><button id="uv-planar" type="button">Planar</button><button id="uv-cyl" type="button">Cylinder</button></div>
    <div class="row"><button id="uv-rot" type="button" title="Rotate the selected faces' UVs (all if none) by 90 degrees">Rotate 90</button><button id="uv-grow" type="button">Scale +</button><button id="uv-shrink" type="button">Scale −</button></div>
    <p class="dim">Unwrap before painting textures. Box, Planar, and Cylinder make tiling UVs for repeating textures.</p>
    <p class="dim">Send to UEFN builds MI_ on M_Forge_Master and imports these as T_ textures.</p>`;
}

// UV layout of the selected object over its material's texture. Fetched once per object, geometry, and texture.
let uvSequence = 0, uvCache = null;
async function paintUvView() {
  const canvas = document.getElementById("uv-view");
  const node = nodeById(state.payload?.selection?.object);
  if (!canvas) return;
  const g = canvas.getContext("2d");
  g.clearRect(0, 0, canvas.width, canvas.height);
  if (node?.type !== "object") { g.fillStyle = "#888"; g.fillText("Select an object", 8, 16); return; }
  const mat = (state.payload.materials || []).find(m => m.id === node.material);
  const file = mat?.textures?.base_color || "";
  const key = `${state.name}|${node.id}|${state.geom}|${file}`;
  if (uvCache?.key !== key) {
    const sequence = ++uvSequence;
    const data = await rpc("forge_call", { op: "mesh_data", name: state.name, params: { id: node.id }, actor: "user" }).catch(() => null);
    if (sequence !== uvSequence || !data || data.ok === false) return;
    let img = null;
    if (file) {
      const res = await rpc("forge_texture", { name: state.name, file }).catch(() => null);
      if (res?.ok) img = await new Promise(resolve => { const i = new Image(); i.onload = () => resolve(i); i.onerror = () => resolve(null); i.src = `data:${res.mime};base64,${res.base64}`; });
    }
    if (sequence !== uvSequence) return;
    uvCache = { key, uv: new Float32Array(b64buf(data.ub)), faces: new Uint32Array(b64buf(data.fb)), img };
  }
  if (!document.body.contains(canvas)) return;
  if (uvCache.img) g.drawImage(uvCache.img, 0, 0, canvas.width, canvas.height);
  const { uv, faces } = uvCache;
  const picked = new Set(state.payload.selection?.faces || []);
  const w = canvas.width, h = canvas.height;
  for (const selectedPass of [false, true]) {
    g.strokeStyle = selectedPass ? getComputedStyle(document.documentElement).getPropertyValue("--accent") || "#6c8cff" : "rgba(255,255,255,0.6)";
    g.lineWidth = selectedPass ? 1.5 : 0.75;
    g.beginPath();
    for (let t = 0; t < faces.length; t++) {
      if (picked.has(faces[t]) !== selectedPass) continue;
      const o = t * 6;
      g.moveTo(uv[o] * w, (1 - uv[o + 1]) * h);
      g.lineTo(uv[o + 2] * w, (1 - uv[o + 3]) * h);
      g.lineTo(uv[o + 4] * w, (1 - uv[o + 5]) * h);
      g.closePath();
    }
    g.stroke();
  }
}

function activity(p) {
  const rows = [...(p.activity || [])].reverse().map((a) => {
    const dim = a.kind === "look" ? " dim" : "";
    return `<div class="item${dim}" data-rev="${a.rev}" data-id="${(a.ids || [])[0] || ""}"><span class="nm">${a.actor === "agent" ? "AI" : "You"}: ${esc(a.summary)}</span><button data-undo="${a.rev}" type="button">Undo</button></div>`;
  }).join("");
  return `<div class="list">${rows || "<span class='dim'>No edits yet.</span>"}</div>`;
}

function exportPanel() {
  return `<div class="row"><label for="ex-mode">Mode</label><select id="ex-mode"><option value="each">Unique meshes</option><option value="group">Groups combined</option><option value="all">Everything</option></select></div>
    <div class="row"><label for="ex-place">Place</label><input id="ex-place" type="checkbox" /><span class="dim">Put the assets in the level in front of the camera</span></div>
    <div class="row"><button id="send" type="button">Send to UEFN</button></div>
    <h2>Import mesh</h2>
    <div class="row"><input id="blend-path" value="${esc(importPrefs.path || "")}" placeholder="Choose a Blend, FBX, GLB or glTF file" aria-label="Mesh file path" style="flex:1" /><button id="pick-mesh" type="button">Browse…</button></div>
    <div class="row"><label for="import-target">Destination</label><select id="import-target"><option value="new">New scene</option><option value="current" ${importPrefs.target === "current" ? "selected" : ""}>Current scene (one undo)</option></select></div>
    <div class="row"><label for="import-name">New scene name</label><input id="import-name" value="${esc(importPrefs.name || "")}" placeholder="Use filename" /></div>
    <div class="row"><label for="import-scale">Scale multiplier</label><input id="import-scale" type="number" min="0.0001" max="10000" step="any" value="${Number(importPrefs.scale) || 1}" /></div>
    <p class="dim">File units and axes are converted automatically to centimetres, Z-up. Scale 1 preserves the source size. Requires local Blender. Binary FBX only.</p>
    <div class="row"><button id="preview-mesh" type="button">Inspect dimensions</button><button id="import-blend" type="button">Import mesh</button><button id="cancel-import" type="button" ${importJob ? "" : "disabled"}>Cancel</button></div>
    <div id="import-result" role="status" aria-live="polite" class="dim">${esc(importReport)}</div>
    <p class="dim">Static meshes, hierarchy, UVs, material slots and direct base-color images. Rigs, animations and procedural shaders are not transferred. Keep texture sidecars beside the source file.</p>`;
}


function xyz(prefix, values, label) {
  return ["x", "y", "z"].map((axis, i) => `<label>${axis.toUpperCase()}<input id="${prefix + axis}" type="number" step="any" aria-label="${label} ${axis.toUpperCase()}" value="${Number((+values[i] || 0).toFixed(3))}" /></label>`).join("");
}

function readXyz(prefix) {
  const values = ["x", "y", "z"].map(axis => Number(document.getElementById(prefix + axis)?.value));
  return values.every(Number.isFinite) ? values : null;
}

function keyAt(clip, bone, channel) {
  const key = (clip?.tracks?.[bone]?.[channel] || []).find(k => Math.abs(k[0] - Math.round(state.frame)) < 1e-6);
  return key ? key[1] : null;
}

function rigPanel(p) {
  const rig = rigOf(p);
  const picked = chosen().length;
  if (!rig) return `<h2>Skeleton</h2><p class="dim">No rig yet. Select the pieces of a character or prop (or nothing, for the whole scene), then fit a skeleton to them.</p>
    <div class="row"><button id="rig-biped" type="button">Biped</button><button id="rig-chain" type="button">Chain</button><button id="rig-new" type="button">Empty rig</button></div>
    <p class="dim">Biped: UE mannequin bone names in a T-pose, facing the front view. Chain: four bones up the selection, for tails, ropes, antennas, and plants.</p>`;
  state.rigId = rig.id;
  const clips = rig.actions || [];
  const clip = clipOf(rig);
  state.clipId = clip?.id || "";
  const bone = rig.bones.find(b => b.name === state.bone);
  if (!bone) state.bone = "";
  const bound = (p.nodes || []).filter(n => n.skin?.rig === rig.id).length;
  const depth = name => { let d = 0, b = rig.bones.find(x => x.name === name); const seen = new Set(); while (b?.parent && !seen.has(b.name)) { seen.add(b.name); d++; b = rig.bones.find(x => x.name === b.parent); } return d; };
  const pickRig = p.rigs.length > 1 ? `<div class="row"><label for="rig-pick">Rig</label><select id="rig-pick">${p.rigs.map(r => `<option value="${esc(r.id)}"${r.id === rig.id ? " selected" : ""}>${esc(r.name)}</option>`).join("")}</select></div>` : "";
  const rows = rig.bones.map(b => `<div class="item${b.name === state.bone ? " on" : ""}" data-bone="${esc(b.name)}" role="button" tabindex="0" aria-pressed="${b.name === state.bone}" style="padding-left:${6 + Math.min(depth(b.name), 10) * 10}px"><span class="nm">${esc(b.name)}</span></div>`).join("");
  const boneEdit = bone ? `<h2>Bone · ${esc(bone.name)}</h2>
    <div class="row"><label for="bone-name">Name</label><input id="bone-name" value="${esc(bone.name)}" /></div>
    <div class="row"><label for="bone-parent">Parent</label><select id="bone-parent"><option value="">None</option>${rig.bones.filter(b => b.name !== bone.name).map(b => `<option${b.name === bone.parent ? " selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>
    <div class="transform-row"><span>Head</span>${xyz("bh", bone.head, "Head")}</div>
    <div class="transform-row"><span>Tail</span>${xyz("bt", bone.tail, "Tail")}</div>
    <div class="row"><button id="bone-apply" type="button">Apply</button><button id="bone-child" type="button">Add child</button><button id="bone-del" type="button">Delete</button></div>`
    : `<p class="dim">Pick a bone to edit it. With a clip open, the rotate gizmo poses it at the current frame.</p>`;
  const keys = clip && bone ? [...new Set(Object.values(clip.tracks?.[bone.name] || {}).flat().map(k => k[0]))].sort((a, b) => a - b) : [];
  const timeline = clip ? `<div class="row"><button id="play" type="button" title="${state.playing ? "Pause" : "Play"}">${state.playing ? "Pause" : "Play"}</button>
      <input id="scrub" type="range" min="0" max="${clip.frames}" step="1" value="${Math.round(Math.min(state.frame, clip.frames))}" aria-label="Frame" style="flex:1" />
      <span id="rig-frame" class="dim">${Math.round(Math.min(state.frame, clip.frames))} / ${clip.frames}</span></div>
    <div class="row"><label for="clip-frames">Frames</label><input id="clip-frames" type="number" min="1" step="1" value="${clip.frames}" /><label for="clip-fps">FPS</label><input id="clip-fps" type="number" min="1" step="1" value="${clip.fps}" /></div>
    <div class="row"><label for="clip-loop">Loop</label><input id="clip-loop" type="checkbox" ${clip.loop ? "checked" : ""} /></div>
    ${bone ? `<div class="transform-row"><span>Rotate</span>${xyz("kr", keyAt(clip, bone.name, "rot") || [0, 0, 0], "Key rotation")}</div>
    <div class="row"><button id="key-set" type="button">Key ${esc(bone.name)}</button><button id="key-del" type="button" ${keys.length ? "" : "disabled"}>Remove key</button></div>
    <p class="dim">Keys on ${esc(bone.name)}: ${keys.length ? keys.map(k => Number(k.toFixed(2))).join(", ") : "none"}. Degrees in the parent bone's frame.</p>` : ""}` : "";
  return `${pickRig}<h2>Skeleton · ${rig.bones.length} bones</h2>
    <div class="list" role="list" aria-label="Bones">${rows}</div>
    <div class="row"><button id="rig-biped" type="button">Add biped</button><button id="rig-chain" type="button">Add chain</button><button id="rig-del" type="button">Delete rig</button></div>
    ${boneEdit}
    <h2>Bind · ${bound} object${bound === 1 ? "" : "s"} on this rig</h2>
    <div class="row"><button id="bind-bone" type="button" ${bone && picked ? "" : "disabled"}>Bind to ${bone ? esc(bone.name) : "bone"}</button><button id="bind-auto" type="button" ${picked ? "" : "disabled"}>Auto weights</button><button id="unbind" type="button" ${picked ? "" : "disabled"}>Unbind</button></div>
    <p class="dim">Bind to a bone moves the whole piece with it (robots, doors, blocky characters). Auto weights bend one mesh across the nearby bones.</p>
    <h2>Animation</h2>
    <div class="row"><label for="clip-pick">Clip</label><select id="clip-pick">${clips.map(c => `<option value="${esc(c.id)}"${c.id === clip?.id ? " selected" : ""}>${esc(c.name)}</option>`).join("") || "<option value=''>No clips</option>"}</select><button id="clip-new" type="button">New</button><button id="clip-del" type="button" ${clip ? "" : "disabled"}>Delete</button></div>
    <div class="row"><label for="gen-kind">Motion</label><select id="gen-kind">${KINDS.map(k => `<option>${k}</option>`).join("")}</select><button id="gen-go" type="button">Generate</button></div>
    <p class="dim">idle, walk, and wave need a biped. sway runs down the picked bone's chain; spin, bob, swing, and open move the picked bone (or the root).</p>
    ${timeline}
    ${clip && bone ? `<div class="row"><label for="ik-on">IK</label><input id="ik-on" type="checkbox" ${state.ik ? "checked" : ""} /><span class="dim">Drag ${esc(bone.name)} to place it; the</span>
      <select id="ik-chain" aria-label="Bones the IK bends">${[2, 3, 4].map(n => `<option${(state.ikChain || 2) === n ? " selected" : ""}>${n}</option>`).join("")}</select><span class="dim">bones above bend</span></div>` : ""}
    ${clip && state.rigView?.morphs?.length ? `<h2>Shape keys · Key sets the value at the current frame</h2>` + state.rigView.morphs.map((name, m) => {
      const value = morphWeightsNow(state.rigView)?.[m] ?? 0;
      return `<div class="row"><label for="morph-${m}">${esc(name)}</label><input id="morph-${m}" data-morph="${esc(name)}" type="range" min="0" max="1" step="0.01" value="${value}" style="flex:1" /><button data-morph-key="${esc(name)}" type="button">Key</button></div>`;
    }).join("") : ""}`;
}

function bindRigPanel() {
  if (state.tab !== "rig") return;
  const rig = rigOf(state.payload);
  const clip = clipOf(rig);
  const click = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  const template = kind => op("rig", { action: "template", template: kind, rig: rig?.id, ids: chosen() });
  click("rig-biped", () => template("biped"));
  click("rig-chain", () => template("chain"));
  click("rig-new", () => op("rig", { action: "new", new_name: "Rig" }));
  click("rig-del", () => op("rig", { action: "delete", rig: rig.id }));
  const pickRig = document.getElementById("rig-pick");
  if (pickRig) pickRig.onchange = () => { state.rigId = pickRig.value; state.clipId = ""; state.bone = ""; paintPanels(); };
  document.querySelectorAll("[data-bone]").forEach(row => {
    const pick = () => { state.bone = state.bone === row.dataset.bone ? "" : row.dataset.bone; paintPanels(); applyPose(); attachGizmo(); };
    row.onclick = pick;
    row.onkeydown = ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } };
  });
  const bone = rig?.bones.find(b => b.name === state.bone);
  click("bone-apply", () => {
    const head = readXyz("bh"), tail = readXyz("bt");
    if (!head || !tail) { setStatus("Enter valid numbers for head and tail", true); return; }
    const name = document.getElementById("bone-name").value.trim();
    const changes = { action: "edit_bone", rig: rig.id, bone: bone.name, head, tail, parent: document.getElementById("bone-parent").value };
    if (name && name !== bone.name) { changes.new_name = name; state.bone = name.replace(/[^A-Za-z0-9_]/g, "_"); }
    op("rig", changes);
  });
  click("bone-child", () => {
    const names = new Set(rig.bones.map(b => b.name));
    let n = 1;
    while (names.has(`${bone.name}_${n}`)) n++;
    const tail = bone.tail.map((v, i) => v + (bone.tail[i] - bone.head[i]));
    state.bone = `${bone.name}_${n}`;
    op("rig", { action: "add_bone", rig: rig.id, bone: state.bone, parent: bone.name, head: bone.tail, tail });
  });
  click("bone-del", () => { op("rig", { action: "delete_bone", rig: rig.id, bone: bone.name }); state.bone = ""; });
  click("bind-bone", () => op("rig", { action: "bind", rig: rig.id, ids: chosen(), bone: state.bone }));
  click("bind-auto", () => op("rig", { action: "bind", rig: rig.id, ids: chosen(), auto: true }));
  click("unbind", () => op("rig", { action: "unbind", rig: rig.id, ids: chosen() }));
  const pickClip = document.getElementById("clip-pick");
  if (pickClip) pickClip.onchange = () => { state.clipId = pickClip.value; state.frame = 0; state.playing = false; paintPanels(); };
  click("clip-new", async () => {
    const made = await op("anim", { action: "new", rig: rig.id, new_name: "Clip" });
    if (made?.clip) state.clipId = made.clip;
  });
  click("clip-del", () => { op("anim", { action: "delete", rig: rig.id, clip: clip.id }); state.clipId = ""; });
  click("gen-go", async () => {
    const made = await op("anim", { action: "generate", rig: rig.id, kind: document.getElementById("gen-kind").value, bone: state.bone || undefined });
    if (made?.clip) { state.clipId = made.clip; state.frame = 0; }
  });
  click("play", togglePlay);
  const scrub = document.getElementById("scrub");
  if (scrub) scrub.oninput = () => {
    state.frame = Number(scrub.value); state.playing = false;
    if (state.rigView) state.rigView.morphOverride = {};  // like Blender: an unkeyed slider value lasts until the frame changes
    const play = document.getElementById("play"); if (play) play.textContent = "Play";
    applyPose();
  };
  for (const [id, key] of [["clip-frames", "frames"], ["clip-fps", "fps"]]) {
    const input = document.getElementById(id);
    if (input) input.onchange = () => { const value = Math.round(Number(input.value)); if (value >= 1) op("anim", { action: "edit", rig: rig.id, clip: clip.id, [key]: value }); };
  }
  const loop = document.getElementById("clip-loop");
  if (loop) loop.onchange = () => op("anim", { action: "edit", rig: rig.id, clip: clip.id, loop: loop.checked });
  click("key-set", () => {
    const rot = readXyz("kr");
    if (!rot) { setStatus("Enter valid rotation numbers", true); return; }
    op("anim", { action: "key", rig: rig.id, clip: clip.id, bone: state.bone, frame: Math.round(state.frame), rot });
  });
  click("key-del", () => op("anim", { action: "unkey", rig: rig.id, clip: clip.id, bone: state.bone, frame: Math.round(state.frame), channel: "rot" }));
  const ik = document.getElementById("ik-on");
  if (ik) ik.onchange = () => { state.ik = ik.checked; attachGizmo(); };
  const chain = document.getElementById("ik-chain");
  if (chain) chain.onchange = () => { state.ikChain = Number(chain.value); };
  document.querySelectorAll("[data-morph]").forEach(slider => slider.oninput = () => {
    if (!state.rigView) return;
    state.rigView.morphOverride[slider.dataset.morph] = Number(slider.value);  // preview until keyed
    applyPose();
  });
  document.querySelectorAll("[data-morph-key]").forEach(button => button.onclick = () => {
    const name = button.dataset.morphKey;
    const value = Number(document.querySelector(`[data-morph="${CSS.escape(name)}"]`)?.value || 0);
    if (state.rigView) delete state.rigView.morphOverride[name];
    op("anim", { action: "key", rig: rig.id, clip: clip.id, shape: name, frame: Math.round(state.frame), value });
  });
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
    const node = nodeById(id);
    const changes = { id };
    for (const [key, prefix] of [["pos", "p"], ["rot", "r"], ["scale", "s"]]) {
      const value = ["x", "y", "z"].map(axis => Number(document.getElementById(prefix + axis).value));
      if (value.some((v, i) => v !== node.transform[key][i])) changes[key] = value;
    }
    const name = document.getElementById("prop-name").value.trim() || node.name;
    const layer = document.getElementById("prop-layer").value;
    if (name !== node.name) changes.name = name;
    if (layer !== node.layer) changes.layer = layer;
    if (Object.entries(params).some(([key, value]) => value !== node.primitive?.params?.[key])) changes.params = params;
    op("edit", changes);
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
  for (const [id, mode] of [["uv-smart", "smart"], ["uv-box", "box"], ["uv-planar", "planar"], ["uv-cyl", "cylinder"]]) {
    const button = document.getElementById(id);
    if (button) button.onclick = () => op("uv", { mode });
  }
  const selectedFaces = () => (state.payload.selection?.faces || []).length ? state.payload.selection.faces : undefined;
  for (const [id, change] of [["uv-rot", { rotate: 90 }], ["uv-grow", { scale: [1.25, 1.25] }], ["uv-shrink", { scale: [0.8, 0.8] }]]) {
    const button = document.getElementById(id);
    if (button) button.onclick = () => op("uv_transform", { id: state.payload.selection?.object, faces: selectedFaces(), ...change });
  }
  document.querySelectorAll("[data-layer]").forEach(select => select.onchange = () => {
    const mat = (state.payload.materials || []).find(m => m.id === state.matId);
    const layers = [0, 1, 2].map(i => mat?.layers?.[i] || "");
    layers[Number(select.dataset.layer)] = select.value;
    op("material_set", { id: state.matId, layers });
  });
  const toMode = (mode, material) => async () => {
    const id = state.payload.selection?.object;
    if (!nodeById(id) || nodeById(id).type !== "object") { setStatus("Select an object first", true); return; }
    if (material && nodeById(id).material !== material) await op("material_assign", { material });
    op("select", { mode });
  };
  const texPaint = document.getElementById("tex-paint");
  if (texPaint) texPaint.onclick = toMode("tpaint", state.matId);
  const blendPaint = document.getElementById("blend-paint");
  if (blendPaint) blendPaint.onclick = toMode("vpaint", state.matId);
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
  const rememberImport = () => {
    importPrefs = {path: document.getElementById("blend-path").value.trim(),
      name: document.getElementById("import-name").value.trim(),
      target: document.getElementById("import-target").value,
      scale: Number(document.getElementById("import-scale").value)};
    call("prefs.set", {id: "mesh-import", value: JSON.stringify(importPrefs)}).catch(() => {});
  };
  for (const id of ["blend-path", "import-name", "import-target", "import-scale"]) {
    const input = document.getElementById(id);
    if (input) input.onchange = rememberImport;
  }
  const pickMesh = document.getElementById("pick-mesh");
  if (pickMesh) pickMesh.onclick = async () => {
    try {
      const picked = await call("files.pick", { accept: "any", multiple: false });
      const path = picked?.files?.[0]?.path;
      if (path) { document.getElementById("blend-path").value = path; rememberImport(); }
    } catch (err) { setStatus(err.message || String(err), true); }
  };
  const runImport = async (preview) => {
    if (state.pending) return;
    rememberImport();
    if (!importPrefs.path) { setStatus("Choose a mesh file first", true); return; }
    state.pending = true;
    try {
      const operation = preview ? "scene_import_preview" : importPrefs.target === "current" ? "import_mesh" : "scene_import_blend";
      const result = await rpc("forge_call", {name: state.name, op: operation, params: importPrefs});
      if (result.ok === false) throw new Error(result.error);
      importReport = `${result.objects} objects · ${result.triangles} triangles · ${result.materials} materials · ${result.dimensions_cm.map(v => (v / 100).toFixed(2)).join(" × ")} m. ${(result.warnings || []).join(" ")}`;
      if (!preview) {
        const sceneName = result.opened || result.name;
        resetScene();
        await refreshScenes(sceneName);
        state.name = sceneName;
        state.rev = -1;
      }
      const report = document.getElementById("import-result");
      if (report) report.textContent = importReport;
      setStatus(preview ? "Preview ready; no scene changed" : "Mesh imported");
    } catch (err) { setStatus(err.message || String(err), true); }
    finally { state.pending = false; importJob = ""; const cancel = document.getElementById("cancel-import"); if (cancel) cancel.disabled = true; }
  };
  const previewMesh = document.getElementById("preview-mesh");
  if (previewMesh) previewMesh.onclick = () => runImport(true);
  const importBlend = document.getElementById("import-blend");
  if (importBlend) importBlend.onclick = () => runImport(false);
  const cancelImport = document.getElementById("cancel-import");
  if (cancelImport) cancelImport.onclick = async () => {
    if (!importJob) return;
    cancelImport.disabled = true;
    try { await call("plugin.call", {method: "forge_cancel", params: {job: importJob}}); setStatus("Cancelling import…"); }
    catch (err) { setStatus(err.message, true); }
  };

  const send = document.getElementById("send");
  if (send) send.onclick = async () => {
    setStatus("Sending to UEFN…");
    send.disabled = true;
    const place = document.getElementById("ex-place").checked;
    const result = await rpc("forge_send", { name: state.name, mode: document.getElementById("ex-mode").value, collision: "simple", place }).catch(err => ({ ok: false, error: err.message }));
    send.disabled = false;
    if (!result || result.ok === false) setStatus((result && result.error) || "Send failed", true);
    else setStatus(`Imported ${result.count} mesh${result.count === 1 ? "" : "es"}${place ? `, placed ${(result.placed || []).length}` : ""}${(result.notes || []).length ? ` · ${result.notes[0]}` : ""}`);
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
  const modeSelect = document.getElementById("mode");
  modeSelect.onchange = () => { modeSelect.blur(); op("select", { mode: modeSelect.value }); };
  document.getElementById("brush-target").onchange = () => syncWork();
  document.getElementById("brush-done").onclick = () => op("select", { mode: "object" });
  document.getElementById("brush-add").onclick = async () => {
    const taken = new Set(state.work?.shapes || []);
    let n = 1;
    while (taken.has(`Shape_${n}`)) n++;
    const made = await op("shape", { id: state.work?.id, action: "add", key: `Shape_${n}` });
    if (made?.ok !== false) state.pendingTarget = `Shape_${n}`;
  };
  document.getElementById("brush-sub").onclick = () => op("mesh_op", { op: "subdivide", smooth: true, levels: 1 });
  document.getElementById("brush-dec").onclick = () => op("mesh_op", { op: "decimate", ratio: 0.5 });
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
    rig: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="4" r="2"/><path d="M12 6v8M12 9l-5 4M12 9l5 4M12 14l-4 6M12 14l4 6"/></svg>`,
  };
  for (const [id, label] of [["props", "Details"], ["layers", "Layers"], ["materials", "Materials"], ["rig", "Rig"], ["check", "Validate"], ["activity", "History"], ["export", "UEFN"]]) {
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
      if (Math.abs(_m.determinant()) < 1e-12) continue; // hidden behind its rig-view copy
      bounds.union(geo.boundingBox.clone().applyMatrix4(_m));
    }
    const picked = new Set(chosen());
    for (const mesh of state.rigView?.meshes || []) {
      let id = mesh.userData.id;
      while (id && !picked.has(id)) id = nodeById(id)?.parent; // a picked group frames its bound children
      if (!id) continue;
      mesh.geometry.computeBoundingBox();
      bounds.union(mesh.geometry.boundingBox);
    }
    if (state.work && picked.has(state.work.id)) {
      state.work.mesh.geometry.computeBoundingBox();
      bounds.union(state.work.mesh.geometry.boundingBox.clone().applyMatrix4(state.work.matrix));
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
    else if (k === " " && state.rigView && ev.target === document.body) { ev.preventDefault(); togglePlay(); }
    else if (k === "q") setWidget("select");
    else if (k === "w") setWidget("translate");
    else if (k === "e") setWidget("rotate");
    else if (k === "r" && !ev.ctrlKey) setWidget("scale");
    else if (k === "f") focusSelection();
    else if ((k === "[" || k === "]") && state.work) {
      const radius = document.getElementById("brush-radius");
      radius.value = String(Math.max(1, Math.round(Number(radius.value) * (k === "]" ? 1.25 : 0.8))));
      brushRing.scale.setScalar(Number(radius.value));
    }
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
  BONE_IDLE.copy(color("muted"));
  BONE_ON.copy(color("accent"));
  applyPose();
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
restoreSession().then(() => refreshScenes()).then(() => { state.rev = -1; poll(); setInterval(poll, 250); setInterval(saveSession, 500); }).catch((err) => setStatus(err.message || String(err), true));

// Capture interactions before a host tab click can remove the iframe.
for(const event of ["pointerup","keyup","change","input"])document.addEventListener(event,()=>queueMicrotask(saveSession));
orbit.addEventListener("end",saveSession);
window.addEventListener("blur",saveSession);
window.addEventListener("pagehide",saveSession);
