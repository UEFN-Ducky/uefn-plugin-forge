// Everyone in the scene, live: teammates and duckies fly the viewport with their names,
// drags and brush strokes show while they happen, pings mark a spot, and the panel draws
// the pictures and shot frames the agent's tools wait for (backend/live_view.py).
import * as THREE from "three";
import { evalPath } from "./shots.js";

export const SEND_MS = 100;        // my camera to the group, 10 a second
export const POLL_MS = 200;        // the backend's duckies, pings and render asks
export const DRAG_MS = 50;         // a drag or stroke in progress, 20 a second
export const FADE_MS = 10000;      // a ducky's flyer fades this long after its last call
export const PING_MS = 3000;
export const STROKE_FADE_MS = 1500;
export const HOLD_MS = 2000;       // a teammate's drag holds the object this long after its last move
export const TAG_MS = 8000;        // their preview stays until the synced change lands, or this long
const MODE_WORDS = { sculpt: "sculpting", tpaint: "painting", vpaint: "painting", weight: "painting weights" };

export function hueOf(name) {
  let h = 2166136261;
  for (const ch of String(name || "")) h = Math.imul(h ^ ch.codePointAt(0), 16777619) >>> 0;
  return (h % 360) / 360;
}

export function colorFor(name) {
  return new THREE.Color().setHSL(hueOf(name), 0.7, 0.55);
}

export const r1 = v => Math.round(v * 10) / 10;

/** True when `value` should go out now: it changed, and the last send is `interval` old. */
export function makeSender(interval) {
  let last = -Infinity, sent;
  return (now, value) => {
    const text = JSON.stringify(value);
    if (text === sent || now - last < interval) return false;
    last = now;
    sent = text;
    return true;
  };
}

export function makeThrottle(interval) {
  let last = -Infinity;
  return now => {
    if (now - last < interval) return false;
    last = now;
    return true;
  };
}

/** Where to draw an arrow for a point off screen (NDC x/y; behind = behind the camera), else null. */
export function edgeArrow(x, y, behind = false) {
  if (!behind && Math.abs(x) <= 1 && Math.abs(y) <= 1) return null;
  let dx = behind ? -x : x, dy = behind ? -y : y;
  if (!dx && !dy) dy = -1;
  const k = 0.9 / Math.max(Math.abs(dx), Math.abs(dy));
  return { x: dx * k, y: dy * k, angle: Math.atan2(dy, dx) };
}

/** A flyer's low-poly parts, forward +Z and up +Y (Object3D.lookAt aims +Z). */
export function flyerParts(kind) {
  if (kind === "ducky") return [
    { g: new THREE.SphereGeometry(26, 8, 6).scale(1, 0.8, 1.3), at: [0, 0, 0], tone: "body" },
    { g: new THREE.SphereGeometry(16, 8, 6), at: [0, 22, 20], tone: "body" },
    { g: new THREE.ConeGeometry(6, 16, 6).rotateX(Math.PI / 2), at: [0, 20, 40], tone: "beak" },
  ];
  return [
    { g: new THREE.CapsuleGeometry(20, 44, 2, 6), at: [0, -20, 0], tone: "body" },
    { g: new THREE.SphereGeometry(18, 8, 6), at: [0, 30, 0], tone: "body" },
    { g: new THREE.BoxGeometry(22, 8, 8), at: [0, 32, 16], tone: "dark" },
  ];
}

export function triangles(parts) {
  return parts.reduce((n, p) => n + (p.g.index ? p.g.index.count : p.g.attributes.position.count) / 3, 0);
}

function frustumGeometry() {
  const d = 70, w = 40, h = 26, o = [0, 0, 0];
  const c = [[-w, -h, d], [w, -h, d], [w, h, d], [-w, h, d]];
  const pts = [];
  for (const p of c) pts.push(o, p);
  for (let i = 0; i < 4; i++) pts.push(c[i], c[(i + 1) % 4]);
  pts.push([-10, h, d], [0, h + 14, d], [0, h + 14, d], [10, h, d]);  // the up tick
  return new THREE.BufferGeometry().setFromPoints(pts.map(p => new THREE.Vector3(...p)));
}

function makeLabel(text, color) {
  const canvas = document.createElement("canvas");
  const g = canvas.getContext("2d");
  const font = "600 28px system-ui, 'Segoe UI', sans-serif";
  g.font = font;
  const w = Math.ceil(g.measureText(text).width) + 28;
  canvas.width = w;
  canvas.height = 44;
  g.font = font;
  g.fillStyle = "#" + color.getHexString();
  g.beginPath();
  g.roundRect(0, 0, w, 44, 12);
  g.fill();
  g.fillStyle = "#fff";
  g.textBaseline = "middle";
  g.fillText(text, 14, 23);
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map, sizeAttenuation: false, depthTest: false, transparent: true }));
  const height = 0.04;
  sprite.scale.set(height * w / 44, height, 1);
  sprite.center.set(0.5, 0);
  sprite.renderOrder = 999;
  return sprite;
}

function dispose(obj) {
  obj?.traverse?.(o => {
    o.geometry?.dispose?.();
    for (const m of [].concat(o.material || [])) { m.map?.dispose?.(); m.dispose?.(); }
  });
  obj?.removeFromParent?.();
}

/**
 * ctx: { call, scene3, camera, orbit, renderer, content, host, state, sceneName(), boundsOf(ids),
 *        previewDelta(id, elements|null), gizmoId(), setGizmoEnabled(on), cleanHide(), setStatus() }
 */
export function createPresence(ctx) {
  const { call, scene3, camera, orbit, renderer } = ctx;
  const root = new THREE.Group();
  root.name = "forge-presence";
  scene3.add(root);
  const flyers = new Map();     // key → flyer
  const holds = new Map();      // object id → a teammate's drag in progress
  const strokes = new Map();    // device → a teammate's brush
  const pings = [];
  const cameraGizmos = [];
  let live = false, style = "avatar", following = "", drive = null;
  let polling = false, lastPoll = -Infinity, rendering = false, lastGeom = -1, lastScene = "";
  let peopleKey = "", renders = [], onRenders = null, aiMoves = new Map(), viewsDone = new Set();
  const sendMe = makeSender(SEND_MS);
  const dragGo = makeThrottle(DRAG_MS), strokeGo = makeThrottle(DRAG_MS), outline = makeThrottle(500);
  let lastDrag = null;

  const bar = document.createElement("div");
  bar.className = "people";
  bar.setAttribute("role", "toolbar");
  bar.setAttribute("aria-label", "People in this scene");
  ctx.host.appendChild(bar);
  const arrows = document.createElement("div");
  arrows.className = "ping-arrows";
  ctx.host.appendChild(arrows);

  const plugin = (method, params) => call("plugin.call", { method, params });
  const scene = () => ctx.sceneName();
  const livesend = (topic, data) => { if (live) call("live.send", { topic, data }).catch(() => {}); };

  // ------------------------------------------------------------------ flyers
  function makeFlyer(key, kind, name) {
    const color = colorFor(name);
    const group = new THREE.Group();
    group.up.set(0, 0, 1);
    const avatar = new THREE.Group();
    const tones = { body: color, dark: color.clone().multiplyScalar(0.25), beak: new THREE.Color(0.95, 0.62, 0.12) };
    for (const part of flyerParts(kind)) {
      const mesh = new THREE.Mesh(part.g, new THREE.MeshStandardMaterial({ color: tones[part.tone], roughness: 0.55 }));
      mesh.position.fromArray(part.at);
      avatar.add(mesh);
    }
    const lens = new THREE.LineSegments(frustumGeometry(), new THREE.LineBasicMaterial({ color }));
    group.add(avatar, lens);
    const box = new THREE.Box3Helper(new THREE.Box3(), color);
    box.visible = false;
    root.add(group, box);
    const f = { key, kind, name, color, group, avatar, lens, box, label: null, text: "", doing: "", picked: [],
      cur: null, goal: null, seen: performance.now(), scene: "", mode: "" };
    applyStyle(f);
    flyers.set(key, f);
    return f;
  }

  function applyStyle(f) {
    f.avatar.visible = style === "avatar";
    f.lens.visible = style !== "avatar";
  }

  function setLabel(f) {
    const verb = f.kind === "ducky" ? f.doing : MODE_WORDS[f.mode] || "";
    const text = (f.kind === "ducky" ? `${f.name} 🦆` : f.name) + (verb ? ` · ${verb}` : "");
    if (text === f.text) return;
    dispose(f.label);
    f.text = text;
    f.label = makeLabel(text, f.color);
    root.add(f.label);
  }

  function upsert(key, kind, name, pose, extra = {}) {
    if (!pose || !Array.isArray(pose.pos) || !Array.isArray(pose.target)) return null;
    const f = flyers.get(key) || makeFlyer(key, kind, name || "Someone");
    f.name = name || f.name;
    Object.assign(f, extra);
    f.goal = { pos: new THREE.Vector3().fromArray(pose.pos), target: new THREE.Vector3().fromArray(pose.target) };
    if (!f.cur) f.cur = { pos: f.goal.pos.clone(), target: f.goal.target.clone() };
    setLabel(f);
    return f;
  }

  function drop(key) {
    const f = flyers.get(key);
    if (!f) return;
    dispose(f.group); dispose(f.label); dispose(f.box);
    flyers.delete(key);
    if (following === key) following = "";
  }

  // ------------------------------------------------------------------ my camera
  function myPose() {
    return { pos: camera.position.toArray(), target: orbit.target.toArray(), fov: camera.fov };
  }

  function applyPose(p) {
    camera.position.fromArray(p.pos);
    orbit.target.fromArray(p.target);
    if (Number.isFinite(p.fov) && Math.abs(camera.fov - p.fov) > 1e-3) {
      camera.fov = p.fov;
      camera.updateProjectionMatrix();
    }
    orbit.update();
  }

  function glideTo(pose, seconds = 0.6, kind = "in_out") {
    following = "";
    drive = { keys: [{ t: 0, ...myPose() }, { t: Math.max(0.01, seconds), pos: pose.pos, target: pose.target, fov: pose.fov ?? camera.fov }],
      start: performance.now(), kind };
  }

  function play(keys, kind = "linear") {
    following = "";
    drive = { keys, start: performance.now(), kind };
  }

  function stop() { drive = null; following = ""; }
  renderer.domElement.addEventListener("pointerdown", stop, true);
  renderer.domElement.addEventListener("wheel", stop, { capture: true, passive: true });

  // ------------------------------------------------------------------ live events
  function fromKey(from) { return "p:" + String(from?.device || from?.name || "?"); }

  function memberState(from, st) {
    const key = fromKey(from);
    if (!st) { drop(key); return; }
    const f = upsert(key, "person", String(from?.name || "Teammate"), st, { scene: String(st.scene || ""), mode: String(st.mode || ""),
      picked: Array.isArray(st.picked) ? st.picked.slice(0, 20).map(String) : [] });
    if (f) f.seen = performance.now();
  }

  function sharedValue(key, value) {
    if (!String(key).startsWith("ai:")) return;
    if (!value) { drop(key); return; }
    aiRecord(value, performance.now(), false);
  }

  /** A ducky's camera. Only this PC's duckies (local) may drive this viewport; Follow AI follows any. */
  function aiRecord(rec, seenAt, local) {
    const f = upsert(rec.key, "ducky", String(rec.name || "AI"), rec, { scene: String(rec.scene || ""), doing: String(rec.doing || "") });
    if (!f) return;
    f.seen = Math.max(f.seen || 0, seenAt);
    setLabel(f);
    const v = Number(rec.v) || 0;
    if (aiMoves.get(rec.key) === v) return;
    aiMoves.set(rec.key, v);
    const fresh = performance.now() - seenAt < 3000;  // an old move found on opening the tab never yanks the view
    if (!fresh || rec.scene !== scene() || ctx.state.flying || ctx.state.dragging) return;
    if ((rec.drive && local) || ctx.state.follow) {
      if (Array.isArray(rec.path) && rec.path.length > 1) play(rec.path, rec.path.length > 2 ? "linear" : "in_out");
      else glideTo(rec, 0.8);
    }
  }

  function message(from, topic, data) {
    if (!data || data.scene !== scene()) return;
    const who = String(from?.name || "Teammate");
    const color = colorFor(who);
    const now = performance.now();
    if (topic === "forge.drag" && typeof data.id === "string") {
      const hold = holds.get(data.id) || { id: data.id };
      Object.assign(hold, { key: fromKey(from), name: who, color, at: now, ended: Boolean(data.end) });
      holds.set(data.id, hold);
      if (Array.isArray(data.d) && data.d.length === 16 && data.d.every(Number.isFinite)) ctx.previewDelta(data.id, data.d);
    } else if (topic === "forge.stroke" && Array.isArray(data.p)) {
      const key = fromKey(from);
      let s = strokes.get(key);
      if (!s) {
        const ring = new THREE.Mesh(new THREE.RingGeometry(0.85, 1, 32),
          new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, transparent: true, depthTest: false }));
        ring.renderOrder = 998;
        root.add(ring);
        s = { ring, label: null, text: "" };
        strokes.set(key, s);
      }
      s.ring.position.fromArray(data.p);
      s.ring.scale.setScalar(Math.max(1, Number(data.r) || 20));
      s.at = now;
      s.landed = false;
      const text = `${who} is ${MODE_WORDS[data.mode] || "brushing"}`;
      if (text !== s.text) { dispose(s.label); s.text = text; s.label = makeLabel(text, color); root.add(s.label); }
      s.label.position.fromArray(data.p).add(new THREE.Vector3(0, 0, (Number(data.r) || 20) + 20));
      s.ring.visible = s.label.visible = true;
    } else if (topic === "forge.ping" && Array.isArray(data.point)) {
      addPing(data.point, data.ai ? `${data.by || "AI"} 🦆` : who);
    }
  }

  window.addEventListener("message", ev => {
    if (ev.source !== parent) return;
    const e = ev.data?.event;
    if (!e || typeof e.type !== "string") return;
    if (e.type === "live.state") {
      live = Boolean(e.on);
      if (live) catchUp();
      else for (const key of [...flyers.keys()]) if (key.startsWith("p:")) drop(key);
    } else if (e.type === "live.memberState") memberState(e.from, e.state);
    else if (e.type === "live.shared") sharedValue(e.key, e.value);
    else if (e.type === "live.message") message(e.from, e.topic, e.data);
  });

  async function catchUp() {
    try {
      const states = await call("live.memberStates", {});
      for (const row of states?.states || []) memberState(row.from, row.state);
      const shared = await call("live.shared", {});
      for (const [key, row] of Object.entries(shared?.values || {})) sharedValue(key, row?.value);
    } catch { /* not on a team copy */ }
  }
  call("live.state", {}).then(s => { live = Boolean(s?.on); if (live) catchUp(); }).catch(() => {});

  // ------------------------------------------------------------------ live edits
  function sendDrag(id, delta) {
    if (!live || !id) return;
    lastDrag = { id, d: delta.elements.map(v => Math.round(v * 1e4) / 1e4) };
    if (dragGo(performance.now())) livesend("forge.drag", { scene: scene(), ...lastDrag });
  }

  function endDrag(id) {
    if (!live || !id) return;
    livesend("forge.drag", { scene: scene(), id, ...(lastDrag?.id === id ? { d: lastDrag.d } : {}), end: true });
    lastDrag = null;
  }

  function sendStroke(point, radius, mode) {
    if (!live || !point || !strokeGo(performance.now())) return;
    livesend("forge.stroke", { scene: scene(), p: point.toArray().map(r1), r: r1(radius), mode });
  }

  function heldBy(id) {
    const h = id && holds.get(id);
    return h && !h.ended && performance.now() - h.at < HOLD_MS ? h : null;
  }

  // ------------------------------------------------------------------ pings
  function addPing(point, name) {
    const color = colorFor(name.replace(/ 🦆$/, ""));
    const ring = new THREE.Mesh(new THREE.RingGeometry(24, 34, 32),
      new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, transparent: true, depthTest: false }));
    ring.renderOrder = 998;
    ring.position.fromArray(point);
    const label = makeLabel(name, color);
    label.position.fromArray(point).add(new THREE.Vector3(0, 0, 40));
    root.add(ring, label);
    const arrow = document.createElement("div");
    arrow.className = "ping-arrow";
    arrow.textContent = name;
    arrow.style.setProperty("--ping", "#" + color.getHexString());
    arrows.appendChild(arrow);
    pings.push({ point: new THREE.Vector3().fromArray(point), ring, label, arrow, at: performance.now() });
  }

  const _ray = new THREE.Raycaster(), _ndc = new THREE.Vector2(), _ground = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
  function pingAt(ev) {
    const rect = renderer.domElement.getBoundingClientRect();
    _ndc.set(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
    _ray.setFromCamera(_ndc, camera);
    const hit = _ray.intersectObject(ctx.content, true).find(h => h.object.visible && !h.object.isLine && !h.object.isSprite);
    const point = hit ? hit.point : _ray.ray.intersectPlane(_ground, new THREE.Vector3());
    if (!point) return;
    const at = point.toArray().map(r1);
    addPing(at, "You");
    livesend("forge.ping", { scene: scene(), point: at });
  }

  // ------------------------------------------------------------------ saved cameras
  function setCameras(list) {
    for (const g of cameraGizmos.splice(0)) dispose(g);
    for (const cam of list || []) {
      const g = new THREE.Group();
      g.up.set(0, 0, 1);
      const lines = new THREE.LineSegments(frustumGeometry(), new THREE.LineBasicMaterial({ color: 0xd8d8e0 }));
      lines.scale.setScalar(1.6);
      g.add(lines);
      g.position.fromArray(cam.pos);
      g.lookAt(new THREE.Vector3().fromArray(cam.target));
      const label = makeLabel("🎥 " + cam.name, new THREE.Color(0.25, 0.25, 0.3));
      label.position.fromArray(cam.pos).add(new THREE.Vector3(0, 0, 70));
      root.add(g, label);
      cameraGizmos.push(g, label);
    }
  }

  // ------------------------------------------------------------------ pictures and shot frames
  // A render target holds linear light (three converts to sRGB only on screen), so frames are
  // drawn in half floats and turned into sRGB bytes through one lookup table, like the viewport.
  let target = null, lut = null, half = false;
  const _pixels = { buf: null, rgba: null };
  function srgbLut() {
    const toByte = c => {
      c = Math.min(1, Math.max(0, c));
      return Math.round(255 * (c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055));
    };
    half = renderer.capabilities.textureTypeReadable?.(THREE.HalfFloatType) ?? false;
    lut = new Uint8Array(half ? 65536 : 256);
    for (let i = 0; i < lut.length; i++) lut[i] = toByte(half ? THREE.DataUtils.fromHalfFloat(i) : i / 255);
  }
  function renderPose(pose, width, height, { clean = false, type = "image/png", quality } = {}) {
    if (!lut) srgbLut();
    if (!target || target.width !== width || target.height !== height) {
      target?.dispose();
      target = new THREE.WebGLRenderTarget(width, height, { samples: 4, type: half ? THREE.HalfFloatType : THREE.UnsignedByteType });
    }
    const cam = new THREE.PerspectiveCamera(pose.fov || 45, width / height, 1, 200000);
    cam.up.set(0, 0, 1);
    cam.position.fromArray(pose.pos);
    cam.lookAt(new THREE.Vector3().fromArray(pose.target));
    // A clean frame shows only the scene; any picture leaves out the flyer standing at the lens.
    const near = [...flyers.values()].filter(f => f.cur && f.cur.pos.distanceTo(cam.position) < 150).flatMap(f => [f.group, f.label]);
    const hidden = (clean ? [root, ...ctx.cleanHide()] : near).filter(o => o?.visible);
    for (const o of hidden) o.visible = false;
    try {
      renderer.setRenderTarget(target);
      renderer.render(scene3, cam);
      const n = width * height * 4;
      if (!_pixels.buf || _pixels.buf.length !== n) _pixels.buf = half ? new Uint16Array(n) : new Uint8Array(n);
      renderer.readRenderTargetPixels(target, 0, 0, width, height, _pixels.buf);
    } finally {
      renderer.setRenderTarget(null);
      for (const o of hidden) o.visible = true;
    }
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const g = canvas.getContext("2d");
    const img = g.createImageData(width, height);
    const src = _pixels.buf, out = img.data, row = width * 4;
    for (let y = 0; y < height; y++) {  // rows come bottom-up
      let s = (height - 1 - y) * row, d = y * row;
      for (let x = 0; x < width; x++, s += 4, d += 4) {
        out[d] = lut[src[s]];
        out[d + 1] = lut[src[s + 1]];
        out[d + 2] = lut[src[s + 2]];
        out[d + 3] = 255;
      }
    }
    g.putImageData(img, 0, 0);
    return canvas.toDataURL(type, quality);
  }

  async function doView(req) {
    viewsDone.add(req.id);
    try {
      const b64 = renderPose(req.camera, req.width, req.height, { clean: req.clean });
      await plugin("forge_view_put", { id: req.id, b64 });
    } catch (err) {
      await plugin("forge_view_put", { id: req.id, error: err.message || String(err) }).catch(() => {});
    }
  }

  async function doFrames(job) {
    rendering = true;
    try {
      for (const index of job.frames) {
        if (scene() !== lastScene) return;
        const pose = evalPath(job.keys, index / job.fps, job.ease);
        const b64 = renderPose(pose, job.width, job.height, { clean: true, type: "image/jpeg", quality: 0.92 });
        const put = await plugin("forge_frame_put", { job: job.id, index, b64 });
        if (put?.ok === false) return;
      }
    } catch (err) {
      await plugin("forge_frame_put", { job: job.id, error: err.message || String(err) }).catch(() => {});
    } finally {
      rendering = false;
    }
  }

  async function poll(now) {
    if (polling || now - lastPoll < POLL_MS || !scene()) return;
    polling = true;
    lastPoll = now;
    try {
      const out = await plugin("forge_live", { scene: scene(), me: myPose() });
      if (!out || out.ok === false) return;
      const seenAt = performance.now();
      for (const rec of out.ai || []) aiRecord(rec, seenAt - Math.max(0, (out.now - rec.ts) * 1000), true);
      for (const p of out.pings || []) {
        const id = `${p.by}:${p.ts}`;
        if (viewsDone.has(id)) continue;
        viewsDone.add(id);
        addPing(p.point, p.ai ? `${p.by} 🦆` : p.by);
      }
      if (out.view && !viewsDone.has(out.view.id)) doView(out.view);
      if (out.job && !rendering) doFrames(out.job);
      renders = out.renders || [];
      onRenders?.(renders);
      if (viewsDone.size > 200) viewsDone = new Set([...viewsDone].slice(-100));
    } catch { /* the next poll tries again */ } finally {
      polling = false;
    }
  }

  // ------------------------------------------------------------------ people bar
  function paintPeople() {
    const rows = [...flyers.values()].filter(f => f.group.visible || f.key === following);
    const key = JSON.stringify([style, following, live, rows.map(f => [f.key, f.text])]);
    if (key === peopleKey) return;
    peopleKey = key;
    bar.replaceChildren();
    for (const f of rows) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip" + (f.key === following ? " on" : "");
      b.title = "Click: go to their view · Shift-click: follow them";
      b.style.setProperty("--who", "#" + f.color.getHexString());
      b.textContent = f.text;
      b.dataset.liveNolock = "";
      b.onclick = ev => {
        if (!f.cur) return;
        if (ev.shiftKey) { drive = null; following = following === f.key ? "" : f.key; }
        else glideTo({ pos: f.cur.pos.toArray(), target: f.cur.target.toArray(), fov: camera.fov });
        paintPeople();
      };
      bar.appendChild(b);
    }
    if (rows.length) {
      const t = document.createElement("button");
      t.type = "button";
      t.className = "chip style";
      t.textContent = style === "avatar" ? "Avatars" : "Cameras";
      t.title = "Show people as characters or as cameras";
      t.onclick = () => { style = style === "avatar" ? "camera" : "avatar"; for (const f of flyers.values()) applyStyle(f); paintPeople(); };
      bar.appendChild(t);
    }
    bar.hidden = !rows.length;
  }

  // ------------------------------------------------------------------ every frame
  const _v = new THREE.Vector3();
  function tick(now, dt) {
    const name = scene();
    if (name !== lastScene) {
      lastScene = name;
      for (const key of [...flyers.keys()]) if (key.startsWith("ai:")) drop(key);
      aiMoves = new Map();
      stop();
    }
    if (ctx.state.geom !== lastGeom) {  // the synced change landed: their previews are real now
      lastGeom = ctx.state.geom;
      holds.clear();
      for (const s of strokes.values()) if (now - s.at > STROKE_FADE_MS) s.landed = true;
    }
    if (live && name) {
      const st = { scene: name, pos: camera.position.toArray().map(r1), target: orbit.target.toArray().map(r1), fov: r1(camera.fov),
        mode: ctx.state.mode, tool: ctx.state.gizmo, picked: [...ctx.state.picked].slice(0, 20) };
      if (sendMe(now, st)) call("live.setMyState", { state: st }).catch(() => {});
    }
    poll(now);

    if (drive) {
      const t = (now - drive.start) / 1000, last = drive.keys[drive.keys.length - 1].t;
      applyPose(evalPath(drive.keys, Math.min(t, last), drive.kind));
      if (t >= last) drive = null;
    }
    const k = 1 - Math.exp(-10 * Math.max(0, dt));
    for (const f of [...flyers.values()]) {
      if (f.kind === "ducky" && now - f.seen > FADE_MS * 3) { drop(f.key); continue; }
      const show = f.scene === name && (f.kind !== "ducky" || now - f.seen < FADE_MS) && f.key !== following;
      f.group.visible = show;
      if (f.label) f.label.visible = show;
      if (!f.cur || !f.goal) continue;
      f.cur.pos.lerp(f.goal.pos, k);
      f.cur.target.lerp(f.goal.target, k);
      f.group.position.copy(f.cur.pos);
      f.group.lookAt(f.cur.target);
      if (f.label) f.label.position.copy(f.cur.pos).add(_v.set(0, 0, 60));
      if (f.kind === "person" && outline(now)) {
        const box = show && f.picked.length ? ctx.boundsOf(f.picked) : null;
        f.box.visible = Boolean(box && !box.isEmpty());
        if (f.box.visible) f.box.box.copy(box);
      } else if (!show) f.box.visible = false;
    }
    if (following) {
      const f = flyers.get(following);
      if (!f || !f.cur) following = "";
      else applyPose({ pos: f.cur.pos.toArray(), target: f.cur.target.toArray(), fov: camera.fov });
    }
    for (const [id, h] of holds) {
      if (now - h.at > TAG_MS) { ctx.previewDelta(id, null); holds.delete(id); continue; }
      const fresh = !h.ended && now - h.at < HOLD_MS;
      if (fresh && !h.label) {
        h.label = makeLabel(`${h.name} is moving this`, h.color);
        root.add(h.label);
      }
      if (h.label) {
        h.label.visible = fresh;
        const box = ctx.boundsOf([id]);
        if (box && !box.isEmpty()) h.label.position.set((box.min.x + box.max.x) / 2, (box.min.y + box.max.y) / 2, box.max.z + 20);
      }
    }
    for (const h of holds.values()) if (h.label && !h.label.visible && h.ended) { dispose(h.label); h.label = null; }
    const gid = ctx.gizmoId();
    ctx.setGizmoEnabled(!heldBy(gid));
    for (const s of strokes.values()) {
      const on = now - s.at < STROKE_FADE_MS;
      s.ring.visible = on;
      if (s.label) s.label.visible = now - s.at < TAG_MS && !s.landed;
      if (on) s.ring.lookAt(camera.position);
    }
    const W = ctx.host.clientWidth, H = ctx.host.clientHeight;
    for (let i = pings.length - 1; i >= 0; i--) {
      const p = pings[i], age = now - p.at;
      if (age > PING_MS) { dispose(p.ring); dispose(p.label); p.arrow.remove(); pings.splice(i, 1); continue; }
      p.ring.lookAt(camera.position);
      p.ring.scale.setScalar(1 + 0.35 * Math.sin(age / 110));
      p.ring.material.opacity = p.label.material.opacity = 1 - Math.max(0, age - PING_MS + 600) / 600;
      _v.copy(p.point).project(camera);
      const arrow = edgeArrow(_v.x, _v.y, _v.z > 1);
      p.arrow.hidden = !arrow;
      if (arrow) {
        p.arrow.style.left = `${(arrow.x + 1) / 2 * W}px`;
        p.arrow.style.top = `${(1 - arrow.y) / 2 * H}px`;
        p.arrow.style.setProperty("--angle", `${-arrow.angle}rad`);
      }
    }
    paintPeople();
  }

  return {
    tick, sendDrag, endDrag, sendStroke, heldBy, pingAt, setCameras, glideTo, play, stop, myPose, renderPose,
    get live() { return live; },
    get renders() { return renders; },
    set onRenders(fn) { onRenders = fn; },
  };
}
