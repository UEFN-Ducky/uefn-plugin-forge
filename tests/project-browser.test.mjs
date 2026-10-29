import test from "node:test";
import assert from "node:assert/strict";
import { buildTree, drawGroups, makeBrowser, nextSceneName, pickerRows, pushRecent, sceneTris } from "../ui/src/project-browser.js";

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

test("picker searches, recent floats first, names don't collide", () => {
  const ps = [{ name: "Zoo" }, { name: "LimoRide" }, { name: "Cars" }];
  assert.deepEqual(pickerRows(ps, "", pushRecent(["Zoo"], "Cars")), ["Cars", "Zoo", "LimoRide"]);
  assert.deepEqual(pickerRows(ps, "lim", []), ["LimoRide"]);
  assert.equal(nextSceneName(["Scene_1", "Scene_2"]), "Scene_3");
});
