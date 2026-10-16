import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { applyMaterialDisplay } from "../ui/src/material-display.js";

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
