import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { applyMaterialDisplay, materialFor } from "../ui/src/material-display.js";

test("two-material mesh: lit colour, textured map, no vertex colours without a color attribute", () => {
  const materials = [
    { id: "fur", name: "Fur", base_color: [0.96, 0.62, 0.16, 1], textures: { base_color: "T_Lion_D.png" } },
    { id: "nose", name: "Nose", base_color: [0.05, 0.04, 0.04, 1], textures: {} },
    { id: "hull", name: "ToonOutline", base_color: [0.3, 0.13, 0.05, 1], toon_outline: true },
  ];
  const cache = new Map(), asked = [];
  const tex = file => { asked.push(file); return new THREE.Texture(); };
  const [fur, nose] = ["fur", "nose"].map(id => materialFor(id, materials, { smooth: true }, tex, cache));
  assert.notEqual(fur, nose);
  assert.ok(fur.map && !nose.map);
  assert.deepEqual(asked, ["T_Lion_D.png"]);
  assert.deepEqual(fur.color.toArray(), [1, 1, 1]);
  assert.deepEqual(nose.color.toArray(), [0.05, 0.04, 0.04]);
  assert.equal(fur.vertexColors, false);
  assert.equal(materialFor("fur", materials, { smooth: true, colored: true }, tex, cache).vertexColors, true);
  assert.equal(materialFor("fur", materials, { smooth: true }, tex, cache), fur);
  const hull = materialFor("hull", materials, {}, tex, cache);
  assert.ok(hull.isMeshBasicMaterial && hull.side === THREE.FrontSide && hull.userData.outline);
  assert.ok(hull.color.r < 0.3);
});

test("authored neon, glass and PBR textures reach Three materials", () => {
  const mat = new THREE.MeshStandardMaterial();
  const requests = [];
  const textures = new Map();
  const resolve = (file, space) => {
    requests.push([file, space]);
    if (!textures.has(file)) textures.set(file, new THREE.Texture());
    return textures.get(file);
  };
  applyMaterialDisplay(mat, {
    emissive: [0.1, 0.6, 1], emissive_strength: 2.5,
    metallic: 0.8, roughness: 0.25, opacity: 0.6,
    textures: { normal: "surface-normal.png", orm: "surface-orm.png" },
  }, resolve);
  assert.deepEqual(mat.emissive.toArray(), [0.1, 0.6, 1]);
  assert.equal(mat.emissiveIntensity, 2.5);
  assert.equal(mat.metalness, 0.8);
  assert.equal(mat.roughness, 0.25);
  assert.equal(mat.transparent, true);
  assert.equal(mat.depthWrite, false);
  assert.equal(mat.normalMap, textures.get("surface-normal.png"));
  assert.equal(mat.aoMap, mat.roughnessMap);
  assert.equal(mat.roughnessMap, mat.metalnessMap);
  assert.ok(requests.every(([, space]) => space === THREE.NoColorSpace));
  applyMaterialDisplay(mat, {}, resolve);
  assert.equal(mat.emissiveIntensity, 0);
  assert.equal(mat.transparent, false);
  assert.equal(mat.depthWrite, true);
  assert.equal(mat.normalMap, null);
  assert.equal(mat.roughnessMap, null);
});

test("base color alpha is respected when explicit opacity is absent", () => {
  const mat = new THREE.MeshStandardMaterial();
  applyMaterialDisplay(mat, { base_color: [1, 1, 1, 0.4] }, () => null);
  assert.equal(mat.opacity, 0.4);
});
