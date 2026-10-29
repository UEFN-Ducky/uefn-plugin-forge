import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { materialFor } from "../ui/src/material-display.js";
import { isCollision, loadView, saveView, shadingKey, shadingSettings, snapLabel, statsText } from "../ui/src/viewport.js";

const memory = () => { const m = new Map(); return { getItem: k => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)) }; };

test("view prefs round-trip through forge.view and reject bad values", () => {
  const store = memory();
  assert.deepEqual(loadView(store), { shading: "rendered", wire: false, faces: false, normals: false, grid: true, stats: false, collision: false });
  saveView({ ...loadView(store), shading: "wireframe", stats: true, grid: false }, store);
  assert.deepEqual(loadView(store), { shading: "wireframe", wire: false, faces: false, normals: false, grid: false, stats: true, collision: false });
  store.setItem("forge.view", JSON.stringify({ shading: "toon", wire: "yes" }));
  assert.equal(loadView(store).shading, "rendered");
  assert.equal(loadView(store).wire, false);
  store.setItem("forge.view", "{not json");
  assert.equal(loadView(store).shading, "rendered");
});

test("Z then 1-4 picks a mode, Shift+Z flips wireframe", () => {
  const armed = shadingKey("z");
  assert.deepEqual(armed, { mode: null, armed: true });
  assert.equal(shadingKey("1", { armed: true }).mode, "wireframe");
  assert.equal(shadingKey("2", { armed: true }).mode, "solid");
  assert.equal(shadingKey("4", { armed: true }).mode, "rendered");
  assert.equal(shadingKey("2").mode, null);
  assert.equal(shadingKey("z", { shift: true, current: "material" }).mode, "wireframe");
  assert.equal(shadingKey("z", { shift: true, current: "wireframe", last: "solid" }).mode, "solid");
});

test("modes map to materials: wireframe on black, solid grey, material without PBR maps", () => {
  assert.deepEqual(shadingSettings("wireframe"), { materials: "wireframe", background: "black", lights: false });
  assert.equal(shadingSettings("nope").materials, "rendered");
  const mats = [
    { id: "fur", base_color: [0.9, 0.6, 0.2, 1], textures: { base_color: "T_D.png", normal: "T_N.png" } },
    { id: "hull", base_color: [0.3, 0.1, 0.05, 1], toon_outline: true },
  ];
  const cache = new Map(), tex = () => new THREE.Texture();
  const wire = materialFor("fur", mats, { mode: "wireframe" }, tex, cache);
  assert.ok(wire.wireframe && wire.isMeshBasicMaterial);
  assert.equal(materialFor("hull", mats, { mode: "wireframe" }, tex, cache).visible, false);
  const solid = materialFor("fur", mats, { mode: "solid", smooth: true }, tex, cache);
  assert.ok(solid.isMeshStandardMaterial && !solid.map && !solid.flatShading);
  const flat = materialFor("fur", mats, { mode: "material" }, tex, cache);
  assert.ok(flat.isMeshLambertMaterial && flat.map && !flat.normalMap);
  const lit = materialFor("fur", mats, { mode: "rendered" }, tex, cache);
  assert.ok(lit.isMeshStandardMaterial && lit.map && lit.normalMap);
});

test("collision hulls are recognised by prefix or layer", () => {
  assert.ok(isCollision({ name: "UCX_Hood_00" }) && isCollision({ name: "usp_ball" }) && isCollision({ name: "Box", layer: "collision" }));
  assert.ok(!isCollision({ name: "Hood", layer: "default" }) && !isCollision({ name: "MyUCX_thing" }) && !isCollision(null));
});

test("Snap button sums up the active snaps", () => {
  assert.equal(snapLabel(512, 90, 0), "512 · 90°");
  assert.equal(snapLabel(0, 0, 0.25), "×0.25");
  assert.equal(snapLabel(0, 0, 0), "off");
});

test("stats read like Blender's overlay", () => {
  assert.equal(statsText({ tris: 22041, verts: 11200, objects: 3 }), "22,041 tris · 11,200 verts · 3 objects");
  assert.equal(statsText({ tris: 12, verts: 8, objects: 1 }), "12 tris · 8 verts · 1 object");
});
