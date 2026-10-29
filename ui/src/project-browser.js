// Project browser: header project picker, scene tabs, and the left Project/Library panel.
// Logic here takes an `rpc` function so it tests without a DOM; app.js draws the DOM from these results.

export const LIBRARY_KINDS = ["Materials", "Textures", "Parts", "Rigs"];
const RECENT_MAX = 6;

// Instance rows [id, protoId, parent, ..., material@12] -> Map("shared proto\0material" -> rows).
// Linked copies share one prototype (payload `proto` key), so each group is one InstancedMesh draw.
export function drawGroups(rows, protos = new Map()) {
  const out = new Map();
  for (const row of rows) {
    const k = `${protos.get(row[1])?.proto || row[1]}\0${row[12] || ""}`;
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(row);
  }
  return out;
}

// Above this many drawn triangles the viewport turns Simplify on by itself.
export const SIMPLIFY_TRIS = 300000;
export function sceneTris(rows, protos) {
  let total = 0;
  for (const row of rows) { const p = protos.get(row[1]); if (p) total += p.full_tris || p.tris || 0; }
  return total;
}

/** Most recent first, no duplicates, capped. */
export function pushRecent(list, name) {
  return [name, ...(list || []).filter(n => n !== name)].slice(0, RECENT_MAX);
}

/** "just now", "5 min ago", "3 h ago", "2 d ago", "4 mo ago", "1 y ago" for epoch seconds; "" when unknown. */
export function ago(seconds, now = Date.now() / 1000) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  const s = Math.max(0, now - seconds);
  if (s < 60) return "just now";
  for (const [size, unit] of [[31536000, "y"], [2592000, "mo"], [86400, "d"], [3600, "h"], [60, "min"]]) {
    if (s >= size) return `${Math.floor(s / size)} ${unit} ago`;
  }
  return "just now";
}

/** Picker cards: search filters by name, newest first, then by name. */
export function pickerCards(items, query) {
  const q = (query || "").trim().toLowerCase();
  return (items || []).filter(i => !q || i.name.toLowerCase().includes(q))
    .sort((a, b) => (b.modified || 0) - (a.modified || 0) || a.name.localeCompare(b.name));
}

/** The muted line under a card name: "Edited 5 min ago · 3 objects" (scenes) or "· 4 scenes" (projects). */
export function cardFacts(item, now) {
  const when = ago(item.modified, now);
  const count = item.objects != null ? `${item.objects} object${item.objects === 1 ? "" : "s"}`
    : item.scenes != null ? `${item.scenes} scene${item.scenes === 1 ? "" : "s"}` : "";
  return [when && `Edited ${when}`, count].filter(Boolean).join(" · ");
}

/** True when `key` may run now; then blocks it for `ms`. One per scene keeps thumbnail uploads rare. */
export function makeThrottle(ms = 30000) {
  const last = new Map();
  return (key, now = Date.now()) => {
    if (now - (last.get(key) ?? -Infinity) < ms) return false;
    last.set(key, now);
    return true;
  };
}

/** Arrow-key move in a grid of `count` cards laid out `cols` wide; stays put at the edges. */
export function gridMove(index, key, count, cols) {
  const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -cols, ArrowDown: cols, Home: -Infinity, End: Infinity }[key];
  if (step === undefined || !count) return index;
  const next = index + step;
  return next < 0 ? (step === -Infinity ? 0 : index) : next >= count ? (step === Infinity ? count - 1 : index) : next;
}

/** Project → Scenes, then Library groups. `library` maps kind → items; missing kinds stay empty. */
export function buildTree(project, scenes, library, current) {
  return {
    label: project,
    children: [
      { label: "Scenes", kind: "scenes", children: (scenes || []).map(name => ({ label: name, kind: "scene", name, current: name === current })) },
      { label: "Library", kind: "library", children: LIBRARY_KINDS.map(kind => ({ label: kind, kind: "lib", lib: kind, children: (library?.[kind] || []).map(item => ({ label: item.name, kind: "item", item })) })) },
    ],
  };
}

export function nextSceneName(names, base = "Scene") {
  const taken = new Set(names);
  let n = 1;
  while (taken.has(`${base}_${n}`)) n++;
  return `${base}_${n}`;
}

export function makeBrowser(rpc, { onOpen } = {}) {
  let current = "";
  const call = async (method, params) => {
    const res = await rpc(method, params);
    if (!res || res.ok === false) throw new Error((res && res.error) || `${method} failed`);
    return res;
  };
  return {
    async projects() { return call("forge_projects", { action: "list" }); },
    async openProject(name) { return call("forge_projects", { action: "open", name }); },
    async newProject(name) { return call("forge_projects", { action: "new", name }); },
    /** Picker grids: cards carry modified, thumb (data URL or null), scenes / objects. */
    async projectCards() { return call("forge_projects", { action: "list", cards: true }); },
    async sceneCards(project) {
      return (await call("forge_call", { op: "scene_list", name: "", params: { project, cards: true }, actor: "user" })).scenes || [];
    },
    async renameProject(name, newName) { return call("forge_projects", { action: "rename", name, new_name: newName }); },
    async deleteProject(name) { return call("forge_projects", { action: "delete", name, confirm: true }); },
    async deleteScene(project, name) { return call("forge_projects", { action: "delete_scene", scene: `${project}/${name}`, confirm: true }); },
    async setThumb(name, dataUrl) { return call("scene_thumb_set", { name, data_url: dataUrl }); },
    /** Switching to the scene that is already open does nothing. */
    openScene(name) {
      if (!name || name === current) return false;
      current = name;
      onOpen?.(name);
      return true;
    },
    setCurrent(name) { current = name; },
    async library(kind, q = "") {
      const res = await call("forge_library", { action: "list", kind: kind.toLowerCase(), q });
      return res.items || [];
    },
    /** Drop of a library item on the viewport: link it into the open scene. */
    async link(item, scene) {
      return call("forge_library", { action: "link", id: item.id, kind: (item.kind || "").toLowerCase(), scene });
    },
  };
}
