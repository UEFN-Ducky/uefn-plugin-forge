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
  payload: null,
  mode: "object",
  selectMode: "face",
  gizmo: "translate",
  tab: "outliner",
  follow: false,
  dragging: false,
  selected: new Set(),
};

const host = document.getElementById("view");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
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
    state.dragFrom = captureXform();
  } else {
    commitXform();
  }
});

const meshes = new Map();
let flashUntil = 0;

function resize() {
  const w = host.clientWidth || 400;
  const h = host.clientHeight || 300;
  renderer.setSize(w, h, false);
  camera.aspect = w / Math.max(1, h);
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);

function colorOf(matId, materials) {
  const mat = (materials || []).find((m) => m.id === matId) || (materials || [])[0];
  const c = (mat && mat.base_color) || [0.7, 0.7, 0.75];
  return new THREE.Color(c[0], c[1], c[2]);
}

function rebuild(payload) {
  state.payload = payload;
  state.mode = payload.mode || "object";
  state.selectMode = payload.select_mode || "face";
  const sel = (payload.selection && payload.selection.object) || "";
  for (const obj of content.children) obj.traverse((c) => c.geometry && c.geometry.dispose());
  content.clear();
  meshes.clear();
  const byId = new Map();
  for (const node of payload.nodes || []) {
    const group = new THREE.Group();
    group.name = node.id;
    const t = node.transform || { pos: [0, 0, 0], rot: [0, 0, 0], scale: [1, 1, 1] };
    group.position.set(t.pos[0], t.pos[1], t.pos[2]);
    group.rotation.set(
      THREE.MathUtils.degToRad(t.rot[0] || 0),
      THREE.MathUtils.degToRad(t.rot[1] || 0),
      THREE.MathUtils.degToRad(t.rot[2] || 0),
    );
    group.scale.set(t.scale[0] || 1, t.scale[1] || 1, t.scale[2] || 1);
    group.visible = node.visible !== false;
    group.userData.node = node;
    if (node.type === "object" && node.positions && node.positions.length) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(node.positions, 3));
      geo.setAttribute("normal", new THREE.Float32BufferAttribute(node.normals, 3));
      geo.setAttribute("uv", new THREE.Float32BufferAttribute(node.uvs, 2));
      geo.setIndex(node.indices);
      const matId = node.material;
      const mat = new THREE.MeshStandardMaterial({
        color: colorOf(matId, payload.materials),
        roughness: 0.55,
        metalness: 0.05,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.userData = { id: node.id, faceIds: node.face_ids || [] };
      group.add(mesh);
      meshes.set(node.id, mesh);
      const picked = new Set(((payload.selection && payload.selection.faces) || []));
      if (node.id === sel && picked.size) {
        const pts = [];
        const pos = node.positions;
        const ids = node.face_ids || [];
        for (let t = 0; t < ids.length; t++) {
          if (!picked.has(ids[t])) continue;
          const base = t * 9;
          for (let e = 0; e < 3; e++) {
            const i0 = base + e * 3;
            const i1 = base + ((e + 1) % 3) * 3;
            pts.push(pos[i0], pos[i0 + 1], pos[i0 + 2], pos[i1], pos[i1 + 1], pos[i1 + 2]);
          }
        }
        if (pts.length) {
          const lines = new THREE.BufferGeometry();
          lines.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
          group.add(new THREE.LineSegments(lines, new THREE.LineBasicMaterial({ color: 0x6c8cff })));
        }
      }
    }
    byId.set(node.id, group);
  }
  for (const node of payload.nodes || []) {
    const group = byId.get(node.id);
    const parent = node.parent && byId.get(node.parent);
    (parent || content).add(group);
  }
  const selected = byId.get(sel);
  if (selected && state.mode === "object") gizmo.attach(selected);
  else gizmo.detach();
  gizmo.setMode(state.gizmo);
  const snap = Number(document.getElementById("snap").value) || 0;
  gizmo.setTranslationSnap(snap || null);
  gizmo.setRotationSnap(snap ? THREE.MathUtils.degToRad(15) : null);
  if (payload.flash && payload.flash.ids) {
    flashUntil = performance.now() + 700;
    document.getElementById("ai").hidden = false;
    for (const id of payload.flash.ids) {
      const mesh = meshes.get(id);
      if (mesh) mesh.material.emissive = new THREE.Color(0x3355ff);
    }
    if (state.follow && payload.flash.ids[0] && byId.get(payload.flash.ids[0])) {
      const box = new THREE.Box3().setFromObject(byId.get(payload.flash.ids[0]));
      const center = box.getCenter(new THREE.Vector3());
      orbit.target.copy(center);
    }
  }
  paintPanels();
  document.getElementById("mode").textContent = state.mode === "edit" ? "Edit" : "Object";
}

function captureXform() {
  const obj = gizmo.object;
  if (!obj) return null;
  return { pos: obj.position.clone(), rot: obj.rotation.clone(), scale: obj.scale.clone() };
}

async function commitXform() {
  const obj = gizmo.object;
  const from = state.dragFrom;
  if (!obj || !from) return;
  const node = obj.userData.node;
  if (!node) return;
  await op("edit", {
    id: node.id,
    pos: [obj.position.x, obj.position.y, obj.position.z],
    rot: [THREE.MathUtils.radToDeg(obj.rotation.x), THREE.MathUtils.radToDeg(obj.rotation.y), THREE.MathUtils.radToDeg(obj.rotation.z)],
    scale: [obj.scale.x, obj.scale.y, obj.scale.z],
  });
}

function animate() {
  requestAnimationFrame(animate);
  if (flashUntil && performance.now() > flashUntil) {
    flashUntil = 0;
    document.getElementById("ai").hidden = true;
    for (const mesh of meshes.values()) mesh.material.emissive = new THREE.Color(0);
  }
  resize();
  renderer.render(scene3, camera);
}
animate();

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
renderer.domElement.addEventListener("pointerdown", (ev) => {
  if (ev.button !== 0 || state.dragging) return;
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects([...meshes.values()], false);
  if (!hits.length) return;
  const hit = hits[0];
  const id = hit.object.userData.id;
  const face = (hit.object.userData.faceIds || [])[hit.faceIndex] ;
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
  if (!state.name || state.dragging) return;
  try {
    const info = await rpc("forge_call", { op: "scene_get", name: state.name, params: {}, actor: "user" });
    if (!info || info.ok === false) return;
    if (info.rev !== state.rev) {
      const payload = await rpc("forge_call", { op: "scene_payload", name: state.name, params: {}, actor: "user" });
      if (payload && payload.ok !== false) {
        state.rev = payload.rev;
        rebuild(payload);
        const last = (payload.activity || []).slice(-1)[0];
        if (last) setStatus((last.actor === "agent" ? "AI: " : "") + last.summary);
      }
    }
  } catch (err) {
    setStatus(err.message || String(err), true);
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
  const rows = (p.nodes || []).map((n) => {
    const on = n.id === sel ? " on" : "";
    return `<div class="item${on}" data-id="${n.id}"><span class="nm">${n.type === "group" ? "▸ " : ""}${n.name || n.id}</span><span>${n.layer === "collision" ? "UCX" : ""}</span></div>`;
  }).join("");
  return `<div class="list">${rows || "<span class='dim'>Empty scene. Add a primitive.</span>"}</div>
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
