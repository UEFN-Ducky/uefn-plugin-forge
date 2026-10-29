// Project browser: header project picker, scene tabs, and the left Project/Library panel.
// Logic here takes an `rpc` function so it tests without a DOM; app.js draws the DOM from these results.

export const LIBRARY_KINDS = ["Materials", "Textures", "Parts", "Rigs"];
const RECENT_MAX = 6;

/** Most recent first, no duplicates, capped. */
export function pushRecent(list, name) {
  return [name, ...(list || []).filter(n => n !== name)].slice(0, RECENT_MAX);
}

/** Picker rows: search text filters, recent projects float to the top. */
export function pickerRows(projects, query, recent) {
  const q = (query || "").trim().toLowerCase();
  const rank = n => { const i = (recent || []).indexOf(n); return i < 0 ? 99 : i; };
  return (projects || []).map(p => p.name).filter(n => !q || n.toLowerCase().includes(q))
    .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
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
