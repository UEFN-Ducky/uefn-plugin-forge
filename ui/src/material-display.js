import * as THREE from "three";

// Keep authored display channels consistent for both selected and instanced meshes.
export function applyMaterialDisplay(mat, authored = {}, textureFor) {
  const textures = authored.textures || {};
  mat.roughness = authored.roughness ?? 0.5;
  mat.metalness = authored.metallic ?? 0;
  mat.emissive.setRGB(...(authored.emissive || [0, 0, 0]).slice(0, 3));
  mat.emissiveIntensity = authored.emissive_strength ?? 0;
  mat.opacity = authored.opacity ?? authored.base_color?.[3] ?? 1;
  mat.transparent = mat.opacity < 1;
  mat.depthWrite = !mat.transparent;
  mat.normalMap = textures.normal ? textureFor(textures.normal, THREE.NoColorSpace) : null;
  const orm = textures.orm ? textureFor(textures.orm, THREE.NoColorSpace) : null;
  mat.aoMap = orm;
  mat.roughnessMap = orm;
  mat.metalnessMap = orm;
}
