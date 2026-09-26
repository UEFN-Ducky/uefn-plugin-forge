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
  polling: false,
  batches: [],
  slots: new Map(),
  parentOf: new Map(),
  pick: null,
  faceLines: null,
  batchEdit: false,
  editId: "",
  flashIds: [],
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
camera.position.set(180, -220, 140);
const orbit = new OrbitControls(camera, renderer.domElement);
orbit.target.set(0, 0, 40);
orbit.update();
scene3.add(new THREE.AmbientLight(0xffffff, 0.55));
const sun = new THREE.DirectionalLight(0xffffff, 1.1);
sun.position.set(80, -120, 200);
scene3.add(sun);
const grid = new THREE.GridHelper(800, 16, 0x3a3a44, 0x2a2a32);
grid.rotation.x = -Math.PI / 2;
scene3.add(grid);
const content = new THREE.Group();
scene3.add(content);
const gizmo = new TransformControls(camera, renderer.domElement);
const gizmoHelper = gizmo.getHelper ? gizmo.getHelper() : gizmo;
scene3.add(gizmoHelper);
gizmo.addEventListener("dragging-changed", (ev) => {
  state.dragging = ev.value;
  orbit.enabled = !ev.value;
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

function geometryFor(proto) {
  let geo = geoCache.get(proto.id);
  if (geo) return geo;
  geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(proto.positions, 3));
  geo.setAttribute("normal", new THREE.Float32BufferAttribute(proto.normals, 3));
  if (proto.uvs && proto.uvs.length) geo.setAttribute("uv", new THREE.Float32BufferAttribute(proto.uvs, 2));
  geo.setIndex(proto.indices);
  geoCache.set(proto.id, geo);
  return geo;
}

function materialFor(matId, materials) {
  const color = colorOf(matId, materials);
  let mat = matCache.get(matId || "");
  if (!mat) {
    mat = new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.05 });
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
  let pulled = null;
  const groups = new Map();
  for (const row of payload.instances || []) {
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
      mesh.userData = { id: pulled[0], faceIds: proto.face_ids || [], matId: pulled[12] || "" };
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

function attachGizmo() {
  const sel = (state.payload && state.payload.selection && state.payload.selection.object) || "";
  const slots = state.slots.get(sel);
  if (state.mode !== "object" || !slots || !slots.length) {
    gizmo.detach();
    return;
  }
  slots[0].mesh.getMatrixAt(slots[0].index, _m);
  _m.decompose(pivot.position, pivot.quaternion, pivot.scale);
  pivot.updateMatrix();
  pivot.userData.id = sel;
  gizmo.attach(pivot);
  gizmo.setMode(state.gizmo);
  const snap = Number(document.getElementById("snap").value) || 0;
  gizmo.setTranslationSnap(snap || null);
  gizmo.setRotationSnap(snap ? THREE.MathUtils.degToRad(15) : null);
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
  document.getElementById("mode").textContent = editing ? "Edit" : "Object";
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
  document.getElementById("mode").textContent = state.mode === "edit" ? "Edit" : "Object";
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

function animate() {
  requestAnimationFrame(animate);
  if (flashUntil && performance.now() > flashUntil) {
    flashUntil = 0;
    state.flashIds = [];
    document.getElementById("ai").hidden = true;
    paintColors();
  }
  renderer.render(scene3, camera);
}
animate();

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
renderer.domElement.addEventListener("pointerdown", (ev) => {
  if (ev.button !== 0 || state.dragging || gizmo.axis) return;
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
  op("select", { object: id, faces: state.mode === "edit" && state.selectMode === "face" && face != null ? [face] : undefined });
});

async function op(name, params) {
  setStatus("");
  const result = await rpc("forge_call", { op: name, name: state.name, params: params || {}, actor: "user" });
  if (result && result.ok === false) {
    setStatus(result.error || "Failed", true);
    return result;
  }
  state.rev = -1;
  return result;
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

function paintPanels() {
  const p = state.payload;
  if (!p) return;
  const panel = document.getElementById("panel");
  if (state.tab === "outliner") panel.innerHTML = outliner(p);
  else if (state.tab === "props") panel.innerHTML = props(p);
  else if (state.tab === "materials") panel.innerHTML = materials(p);
  else if (state.tab === "check") panel.innerHTML = `<div id="check"></div><div class="row"><button id="run-check" type="button">Check</button><button id="fix-all" type="button">Fix all</button></div>`;
  else if (state.tab === "activity") panel.innerHTML = activity(p);
  else panel.innerHTML = exportPanel();
  bindPanel();
}

function outliner(p) {
  const sel = (p.selection && p.selection.object) || "";
  let nodes = p.nodes || [];
  let more = "";
  if (nodes.length > 400) {
    const picked = nodes.find((n) => n.id === sel);
    nodes = nodes.slice(0, 400);
    if (picked && !nodes.some((n) => n.id === picked.id)) nodes.unshift(picked);
    more = `<div class="item dim"><span class="nm">${p.nodes.length - nodes.length} more instances</span></div>`;
  }
  const rows = nodes.map((n) => {
    const on = n.id === sel ? " on" : "";
    return `<div class="item${on}" data-id="${n.id}"><span class="nm">${n.type === "group" ? "▸ " : ""}${n.name || n.id}</span><span>${n.layer === "collision" ? "UCX" : ""}</span></div>`;
  }).join("");
  return `<div class="list">${rows || "<span class='dim'>Empty scene. Add a primitive.</span>"}${more}</div>
    <div class="row"><button id="group" type="button">Group</button><button id="join" type="button">Join</button></div>`;
}

function props(p) {
  const id = p.selection && p.selection.object;
  const n = (p.nodes || []).find((x) => x.id === id);
  if (!n) return "<span class='dim'>Select an object.</span>";
  const t = n.transform || { pos: [0, 0, 0], rot: [0, 0, 0], scale: [1, 1, 1] };
  const prim = n.primitive ? `<h2>${n.primitive.kind}</h2>` + Object.entries(n.primitive.params || {}).map(([k, v]) =>
    `<div class="row"><label>${k}</label><input data-param="${k}" value="${v}" /></div>`).join("") : "<span class='dim'>Converted to a mesh.</span>";
  return `<h2>${n.name}</h2>
    <div class="row"><label>X</label><input id="px" value="${t.pos[0]}" /></div>
    <div class="row"><label>Y</label><input id="py" value="${t.pos[1]}" /></div>
    <div class="row"><label>Z</label><input id="pz" value="${t.pos[2]}" /></div>
    <div class="row"><span class="dim">${n.tris || 0} tris</span></div>
    ${prim}
    <div class="row"><button id="apply-props" type="button">Apply</button>
    <button id="to-col" type="button">Collision layer</button></div>`;
}

function materials(p) {
  const rows = (p.materials || []).map((m) => {
    const c = m.base_color || [0.7, 0.7, 0.7];
    const css = `rgb(${c.map((x) => Math.round(x * 255)).join(",")})`;
    return `<div class="item" data-mat="${m.id}"><span class="swatch" style="background:${css}"></span><span class="nm">${m.name}</span></div>`;
  }).join("");
  return `<div class="list">${rows}</div>
    <div class="row"><input id="mat-name" placeholder="Name" /><input id="mat-color" type="color" value="#cccccc" /></div>
    <div class="row"><label>Rough</label><input id="rough" type="range" min="0" max="1" step="0.01" value="0.5" /></div>
    <div class="row"><label>Metal</label><input id="metal" type="range" min="0" max="1" step="0.01" value="0" /></div>
    <div class="row"><button id="mat-save" type="button">Save material</button><button id="mat-assign" type="button">Assign</button></div>
    <h2>AI texture</h2>
    <div class="row"><input id="prompt" placeholder="worn painted metal, tileable" style="flex:1" /></div>
    <div class="row"><button id="gen" type="button">Generate</button></div>
    <div id="thumbs"></div>`;
}

function activity(p) {
  const rows = [...(p.activity || [])].reverse().map((a) => {
    const dim = a.kind === "look" ? " dim" : "";
    return `<div class="item${dim}" data-rev="${a.rev}" data-id="${(a.ids || [])[0] || ""}"><span class="nm">${a.actor === "agent" ? "AI" : "You"}: ${a.summary}</span><button data-undo="${a.rev}" type="button">Undo</button></div>`;
  }).join("");
  return `<div class="list">${rows || "<span class='dim'>No edits yet.</span>"}</div>`;
}

function exportPanel() {
  return `<div class="row"><label>Mode</label><select id="ex-mode"><option value="each">Each object</option><option value="group">Groups combined</option><option value="all">Everything</option></select></div>
    <div class="row"><button id="send" type="button">Send to UEFN</button></div>
    <p class="dim">Writes SM_ meshes, T_ textures, and MI_ materials on M_Forge_Master. UEFN needs to be open.</p>`;
}

function bindPanel() {
  document.querySelectorAll("[data-id]").forEach((el) => {
    el.onclick = () => op("select", { object: el.dataset.id });
  });
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
    op("edit", {
      id,
      pos: [Number(document.getElementById("px").value), Number(document.getElementById("py").value), Number(document.getElementById("pz").value)],
      params,
    });
  };
  const col = document.getElementById("to-col");
  if (col) col.onclick = () => op("edit", { id: state.payload.selection.object, layer: "collision" });
  const save = document.getElementById("mat-save");
  if (save) save.onclick = () => {
    const hex = document.getElementById("mat-color").value;
    const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    op("material_set", {
      name: document.getElementById("mat-name").value || "Material",
      base_color: rgb,
      roughness: Number(document.getElementById("rough").value),
      metallic: Number(document.getElementById("metal").value),
    });
  };
  const assign = document.getElementById("mat-assign");
  if (assign) assign.onclick = () => {
    const mat = document.querySelector(".item.on[data-mat]") || document.querySelector("[data-mat]");
    if (mat) op("material_assign", { material: mat.dataset.mat });
  };
  document.querySelectorAll("[data-mat]").forEach((el) => {
    el.onclick = () => {
      document.querySelectorAll("[data-mat]").forEach((x) => x.classList.remove("on"));
      el.classList.add("on");
    };
  });
  const gen = document.getElementById("gen");
  if (gen) gen.onclick = async () => {
    setStatus("Generating texture…");
    const mat = (document.querySelector(".item.on[data-mat]") || {}).dataset;
    await op("texture", { prompt: document.getElementById("prompt").value, material: mat && mat.mat, tileable: true, derive_maps: true });
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
  const send = document.getElementById("send");
  if (send) send.onclick = async () => {
    setStatus("Sending to UEFN…");
    const result = await rpc("forge_send", { name: state.name, mode: document.getElementById("ex-mode").value, collision: "simple" });
    if (!result || result.ok === false) setStatus((result && result.error) || "Send failed", true);
    else setStatus(`Imported ${result.count} mesh${result.count === 1 ? "" : "es"}`);
  };
  const group = document.getElementById("group");
  if (group) group.onclick = () => {
    const id = state.payload.selection && state.payload.selection.object;
    if (id) op("group", { ids: [id], name: "Group" });
  };
  const join = document.getElementById("join");
  if (join) join.onclick = () => {
    const ids = (state.payload.nodes || []).filter((n) => n.type === "object").map((n) => n.id);
    op("join", { ids });
  };
}

function wire() {
  const prims = document.getElementById("prims");
  for (const kind of PRIMS) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = kind.replace("_", " ");
    b.onclick = () => op("add", { kind, name: kind });
    prims.appendChild(b);
  }
  document.getElementById("mode").onclick = () => op("select", { mode: state.mode === "object" ? "edit" : "object" });
  for (const [id, mode] of [["sm-face", "face"], ["sm-edge", "edge"], ["sm-vert", "vert"]]) {
    document.getElementById(id).onclick = () => op("select", { select_mode: mode, mode: "edit" });
  }
  document.getElementById("g-move").onclick = () => { state.gizmo = "translate"; gizmo.setMode("translate"); };
  document.getElementById("g-rot").onclick = () => { state.gizmo = "rotate"; gizmo.setMode("rotate"); };
  document.getElementById("g-scale").onclick = () => { state.gizmo = "scale"; gizmo.setMode("scale"); };
  document.getElementById("undo").onclick = () => op("undo", {});
  document.getElementById("redo").onclick = () => op("redo", {});
  document.getElementById("extrude").onclick = () => op("mesh_op", { op: "extrude", distance: 10 });
  document.getElementById("inset").onclick = () => op("mesh_op", { op: "inset", amount: 5 });
  document.getElementById("bevel").onclick = () => op("mesh_op", { op: "bevel", width: 4 });
  document.getElementById("loop").onclick = () => op("mesh_op", { op: "loopcut" });
  document.getElementById("sub").onclick = () => op("mesh_op", { op: "subdivide" });
  document.getElementById("fill").onclick = () => op("mesh_op", { op: "fill" });
  document.getElementById("del").onclick = () => op("mesh_op", { op: "delete", kind: "face" });
  document.getElementById("bool").onclick = () => {
    const ids = (state.payload.nodes || []).filter((n) => n.type === "object").map((n) => n.id);
    if (ids.length >= 2) op("boolean", { a: ids[0], b: ids[1], op: "difference" });
    else setStatus("Boolean needs two objects", true);
  };
  document.getElementById("new").onclick = async () => {
    const name = prompt("Scene name", "Prop");
    if (!name) return;
    await rpc("forge_call", { op: "scene_new", name: "", params: { name }, actor: "user" });
    await refreshScenes(name);
    state.rev = -1;
  };
  document.getElementById("scene").onchange = (ev) => { state.name = ev.target.value; state.rev = -1; };
  document.getElementById("follow").onchange = (ev) => { state.follow = ev.target.checked; };
  const tabs = document.getElementById("tabs");
  for (const [id, label] of [["outliner", "Outliner"], ["props", "Props"], ["materials", "Materials"], ["check", "Check"], ["activity", "Activity"], ["export", "Export"]]) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.onclick = () => { state.tab = id; paintPanels(); };
    tabs.appendChild(b);
  }
  window.addEventListener("keydown", (ev) => {
    if (ev.target.matches("input, textarea")) return;
    const k = ev.key.toLowerCase();
    if (k === "tab") { ev.preventDefault(); op("select", { mode: state.mode === "object" ? "edit" : "object" }); }
    else if (k === "g") state.gizmo = "translate";
    else if (k === "r") state.gizmo = "rotate";
    else if (k === "s" && !ev.ctrlKey) state.gizmo = "scale";
    else if (k === "e") op("mesh_op", { op: "extrude", distance: 10 });
    else if (k === "i") op("mesh_op", { op: "inset", amount: 5 });
    else if (k === "f") op("mesh_op", { op: "fill" });
    else if (k === "x") op("mesh_op", { op: "delete", kind: "face" });
    else if (k === "z" && (ev.ctrlKey || ev.metaKey)) op(ev.shiftKey ? "redo" : "undo", {});
    else if (k === "d" && ev.shiftKey) op("duplicate", {});
    else if (ev.ctrlKey && k === "b") op("mesh_op", { op: "bevel", width: 4 });
    else if (ev.ctrlKey && k === "r") op("mesh_op", { op: "loopcut" });
    else if (ev.ctrlKey && k === "j") {
      const ids = (state.payload.nodes || []).filter((n) => n.type === "object").map((n) => n.id);
      op("join", { ids });
    }
    gizmo.setMode(state.gizmo);
  });
}

wire();
refreshScenes().then(() => { state.rev = -1; poll(); setInterval(poll, 250); }).catch((err) => setStatus(err.message || String(err), true));
