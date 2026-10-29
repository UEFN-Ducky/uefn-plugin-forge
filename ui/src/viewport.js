// Viewport shading and overlays, like Blender's header: pure state, no Three objects.

export const SHADING = ["wireframe", "solid", "material", "rendered"];
export const SHADING_LABEL = { wireframe: "Wireframe", solid: "Solid", material: "Material", rendered: "Rendered" };
export const OVERLAYS = ["wire", "faces", "normals", "grid", "stats", "collision"];
export const OVERLAY_LABEL = { wire: "Wireframe on top", faces: "Face orientation", normals: "Normals", grid: "Grid", stats: "Stats", collision: "Collision" };
const KEY = "forge.view";
const DEFAULT = { shading: "rendered", wire: false, faces: false, normals: false, grid: true, stats: false, collision: false };

/** Unreal collision hulls (UCX_/UBX_/USP_/UCP_ or the collision layer): hidden unless the Collision overlay is on. */
export function isCollision(node) {
  return node?.layer === "collision" || /^(UCX|UBX|USP|UCP)_/i.test(String(node?.name || ""));
}

/** Saved choices with every field checked; bad or missing storage gives the defaults. */
export function loadView(storage = globalThis.localStorage) {
  let saved = {};
  try { saved = JSON.parse(storage?.getItem(KEY) || "{}") || {}; } catch { saved = {}; }
  const view = { ...DEFAULT };
  if (SHADING.includes(saved.shading)) view.shading = saved.shading;
  for (const k of OVERLAYS) if (typeof saved[k] === "boolean") view[k] = saved[k];
  return view;
}

export function saveView(view, storage = globalThis.localStorage) {
  try { storage?.setItem(KEY, JSON.stringify(view)); } catch { /* no storage */ }
}

/** What a shading mode asks of the renderer. materials: which builder materialFor uses. */
export function shadingSettings(mode) {
  return {
    wireframe: { materials: "wireframe", background: "black", lights: false },
    solid: { materials: "solid", background: "theme", lights: true },
    material: { materials: "material", background: "theme", lights: true },
    rendered: { materials: "rendered", background: "theme", lights: true },
  }[mode] || shadingSettings("rendered");
}

/** Z then 1-4 picks a mode; Shift+Z flips wireframe and back. Returns {mode, armed}. */
export function shadingKey(key, { shift = false, armed = false, current = "rendered", last = "rendered" } = {}) {
  if (key === "z" && shift) return { mode: current === "wireframe" ? (last === "wireframe" ? "rendered" : last) : "wireframe", armed: false };
  if (key === "z") return { mode: null, armed: true };
  if (armed && /^[1-4]$/.test(key)) return { mode: SHADING[Number(key) - 1], armed: false };
  return { mode: null, armed: false };
}

/** Snap ▾ button summary: the active snaps, or "off". */
export function snapLabel(grid = 0, rot = 0, scale = 0) {
  const parts = [grid && String(grid), rot && `${rot}°`, scale && `×${scale}`].filter(Boolean);
  return parts.length ? parts.join(" · ") : "off";
}

export function statsText({ tris = 0, verts = 0, objects = 0 } = {}) {
  const n = v => Number(v || 0).toLocaleString("en-US");
  return `${n(tris)} tris · ${n(verts)} verts · ${n(objects)} object${objects === 1 ? "" : "s"}`;
}
