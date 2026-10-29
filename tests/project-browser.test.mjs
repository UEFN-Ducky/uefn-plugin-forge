import test from "node:test";
import assert from "node:assert/strict";
import { ago, buildTree, cardFacts, drawGroups, gridMove, makeBrowser, makeThrottle, nextSceneName, pickerCards, pushRecent, sceneTris } from "../ui/src/project-browser.js";

test("100 linked copies of one part payload rows make 1 instanced group, and tris sum for auto-simplify", () => {
  const protos = new Map([["p1", { id: "p1", proto: "lib_wheel", tris: 4000 }], ["p2", { id: "p2", proto: "lib_wheel", tris: 4000 }]]);
  const rows = Array.from({ length: 100 }, (_, i) => [`o${i}`, i % 2 ? "p1" : "p2", "", i, 0, 0, 0, 0, 0, 1, 1, 1, "m"]);
  const groups = drawGroups(rows, protos);
  assert.equal(groups.size, 1);
  assert.equal([...groups.values()][0].length, 100);
  assert.equal(sceneTris(rows, protos), 400000);
});

const calls = [];
const rpc = async (method, params) => {
  calls.push([method, params]);
  if (method === "forge_projects") return { ok: true, active: "LimoRide", projects: [{ name: "Zoo" }, { name: "LimoRide" }, { name: "Cars" }] };
  if (params.action === "list") return { ok: true, items: [{ id: "m1", name: "Gold", kind: "Materials" }] };
  return { ok: true };
};

test("tree is Project → Scenes then Library kinds, from mock RPC data", async () => {
  const b = makeBrowser(rpc);
  const { projects } = await b.projects();
  assert.equal(projects.length, 3);
  const items = await b.library("Materials");
  const tree = buildTree("LimoRide", ["Limo_Body", "Track_A"], { Materials: items }, "Track_A");
  assert.equal(tree.children[0].children.length, 2);
  assert.equal(tree.children[0].children[1].current, true);
  assert.deepEqual(tree.children[1].children.map(c => c.lib), ["Materials", "Textures", "Parts", "Rigs"]);
  assert.equal(tree.children[1].children[0].children[0].label, "Gold");
});

test("switching scene tabs calls open once, not for the open scene", () => {
  const opened = [];
  const b = makeBrowser(rpc, { onOpen: n => opened.push(n) });
  b.setCurrent("Limo_Body");
  assert.equal(b.openScene("Limo_Body"), false);
  assert.equal(b.openScene("Track_A"), true);
  assert.deepEqual(opened, ["Track_A"]);
});

test("dragging a library item into the viewport links it", async () => {
  calls.length = 0;
  await makeBrowser(rpc).link({ id: "m1", kind: "Materials" }, "Limo_Body");
  assert.deepEqual(calls[0], ["forge_library", { action: "link", id: "m1", kind: "materials", scene: "Limo_Body" }]);
});

test("picker cards: search filters, newest first, names don't collide", () => {
  const cards = [{ name: "Zoo", modified: 100 }, { name: "LimoRide", modified: 300 }, { name: "Cars", modified: 300 }, { name: "Old" }];
  assert.deepEqual(pickerCards(cards, "").map(c => c.name), ["Cars", "LimoRide", "Zoo", "Old"]);
  assert.deepEqual(pickerCards(cards, " LIM ").map(c => c.name), ["LimoRide"]);
  assert.deepEqual(pickerCards(cards, "nope"), []);
  assert.deepEqual(pushRecent(["Zoo", "Cars"], "Cars"), ["Cars", "Zoo"]);
  assert.equal(nextSceneName(["Scene_1", "Scene_2"]), "Scene_3");
});

test("relative time and card facts", () => {
  const now = 1_800_000_000;
  assert.equal(ago(now - 20, now), "just now");
  assert.equal(ago(now - 5 * 60, now), "5 min ago");
  assert.equal(ago(now - 3 * 3600, now), "3 h ago");
  assert.equal(ago(now - 2 * 86400, now), "2 d ago");
  assert.equal(ago(now - 70 * 86400, now), "2 mo ago");
  assert.equal(ago(now - 400 * 86400, now), "1 y ago");
  assert.equal(ago(null, now), "");
  assert.equal(cardFacts({ modified: now - 300, objects: 1 }, now), "Edited 5 min ago · 1 object");
  assert.equal(cardFacts({ modified: now - 300, scenes: 4 }, now), "Edited 5 min ago · 4 scenes");
  assert.equal(cardFacts({ objects: null }, now), "");
});

test("thumbnail throttle: once per scene per 30 s", () => {
  const due = makeThrottle(30000);
  assert.equal(due("A", 0), true);
  assert.equal(due("A", 10000), false);
  assert.equal(due("B", 10000), true);
  assert.equal(due("A", 29999), false);
  assert.equal(due("A", 30000), true);
});

test("arrow keys move through the grid and stop at the edges", () => {
  assert.equal(gridMove(0, "ArrowRight", 10, 4), 1);
  assert.equal(gridMove(1, "ArrowDown", 10, 4), 5);
  assert.equal(gridMove(7, "ArrowDown", 10, 4), 7);
  assert.equal(gridMove(0, "ArrowLeft", 10, 4), 0);
  assert.equal(gridMove(5, "Home", 10, 4), 0);
  assert.equal(gridMove(5, "End", 10, 4), 9);
  assert.equal(gridMove(5, "Enter", 10, 4), 5);
});

test("picker RPCs: cards, delete with confirm, thumb upload", async () => {
  calls.length = 0;
  const b = makeBrowser(rpc);
  await b.projectCards();
  await b.deleteScene("LimoRide", "DbgLion2");
  await b.deleteProject("Zoo");
  await b.setThumb("Limo_Body", "data:image/jpeg;base64,AA==");
  assert.deepEqual(calls, [
    ["forge_projects", { action: "list", cards: true }],
    ["forge_projects", { action: "delete_scene", scene: "LimoRide/DbgLion2", confirm: true }],
    ["forge_projects", { action: "delete", name: "Zoo", confirm: true }],
    ["scene_thumb_set", { name: "Limo_Body", data_url: "data:image/jpeg;base64,AA==" }],
  ]);
});
