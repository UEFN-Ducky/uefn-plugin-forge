import * as THREE from "three";

const WHITE = new THREE.Color(1, 1, 1);

export function colorOf(matId, materials) {
  const mat = (materials || []).find((m) => m.id === matId) || (materials || [])[0];
  const c = (mat && mat.base_color) || [0.7, 0.7, 0.75];
  return new THREE.Color(c[0], c[1], c[2]);
}

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

// Blend materials: base plus three layers revealed by the blendMask attribute (vertex colour R, G, B
// in UEFN). Same maths as M_Forge_Blend; roughness and metal stay the base's in the dock.
function blendShader(mat, uniforms) {
  mat.onBeforeCompile = shader => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec3 blendMask;\nvarying vec3 vForgeBlend;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvForgeBlend = blendMask;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vForgeBlend;\nuniform vec3 forgeColor1;\nuniform vec3 forgeColor2;\nuniform vec3 forgeColor3;\nuniform sampler2D forgeMap1;\nuniform sampler2D forgeMap2;\nuniform sampler2D forgeMap3;")
      .replace("#include <map_fragment>", `#include <map_fragment>
{
  vec3 forgeL1 = forgeColor1 * texture2D(forgeMap1, vMapUv).rgb;
  vec3 forgeL2 = forgeColor2 * texture2D(forgeMap2, vMapUv).rgb;
  vec3 forgeL3 = forgeColor3 * texture2D(forgeMap3, vMapUv).rgb;
  diffuseColor.rgb = mix(mix(mix(diffuseColor.rgb, forgeL1, vForgeBlend.r), forgeL2, vForgeBlend.g), forgeL3, vForgeBlend.b);
}`);
  };
  mat.customProgramCacheKey = () => "forge-blend";
}

const isOutline = m => !!m?.toon_outline;

/** One cached Three material per authored material and draw need.
 * colored: the geometry carries a color attribute. Without one, vertexColors would multiply the
 * albedo by WebGL's unset attribute (0,0,0) and draw every material near-black grey.
 */
export function materialFor(matId, materials, { smooth = false, colored = false, mode = "rendered" } = {}, textureFor, cache) {
  const authored = (materials || []).find(m => m.id === matId);
  if (mode === "wireframe" || mode === "solid") {
    // One shared look per mode; toon hulls hide so they never double the edges or grey the body.
    const hull = isOutline(authored);
    const key = `${mode}|${smooth ? "s" : ""}|${hull ? "hull" : ""}`;
    let mat = cache.get(key);
    if (!mat) {
      mat = mode === "wireframe"
        ? new THREE.MeshBasicMaterial({ color: 0xffffff, wireframe: true })
        : new THREE.MeshStandardMaterial({ color: 0xb8b8b8, roughness: 0.85, metalness: 0, flatShading: !smooth });
      mat.visible = !hull;
      cache.set(key, mat);
    }
    return mat;
  }
  if (isOutline(authored)) {
    // Toon hulls are inverted shells: front faces are the shell's back, so they rim the silhouette.
    const key = `outline|${matId}`;
    let mat = cache.get(key);
    if (!mat) cache.set(key, (mat = new THREE.MeshBasicMaterial({ side: THREE.FrontSide })));
    mat.color.copy(colorOf(matId, materials)).multiplyScalar(0.6);
    mat.userData.outline = true;
    return mat;
  }
  const tex = authored?.textures?.base_color || "";
  const layers = (authored?.layers || []).map(id => (materials || []).find(m => m.id === id) || null);
  const blended = layers.some(Boolean);
  const flat = mode === "material";  // base colour and textures only: no normal, ORM or reflections
  const key = [matId || "", smooth ? "s" : "", colored ? "c" : "", flat ? "m" : "", tex, authored?.textures?.normal || "", authored?.textures?.orm || "", (authored?.opacity ?? authored?.base_color?.[3] ?? 1) < 1 ? "transparent" : "opaque", blended ? layers.map(l => l ? `${l.id}:${l.textures?.base_color || ""}` : "").join(",") : ""].join("|");
  let mat = cache.get(key);
  if (!mat) {
    mat = new (flat ? THREE.MeshLambertMaterial : THREE.MeshStandardMaterial)({ flatShading: !smooth, vertexColors: colored });
    if (tex || blended) mat.map = textureFor(tex);  // a white map still turns on UVs for the layers
    if (blended) {
      mat.userData.layers = [1, 2, 3].map(i => ({ [`forgeColor${i}`]: { value: new THREE.Color() }, [`forgeMap${i}`]: { value: textureFor(layers[i - 1]?.textures?.base_color || "") } }));
      blendShader(mat, Object.assign({}, ...mat.userData.layers));
    }
    cache.set(key, mat);
  }
  // A texture replaces the flat colour, as UseBaseColorTex does in UEFN.
  mat.color.copy(tex ? WHITE : colorOf(matId, materials));
  if (flat) {
    mat.opacity = authored?.opacity ?? authored?.base_color?.[3] ?? 1;
    mat.transparent = mat.opacity < 1;
    mat.depthWrite = !mat.transparent;
  } else applyMaterialDisplay(mat, authored, textureFor);
  if (blended) layers.forEach((layer, i) => {
    const target = mat.userData.layers[i][`forgeColor${i + 1}`].value;
    if (!layer) target.copy(colorOf(matId, materials));
    else if (layer.textures?.base_color) target.copy(WHITE);
    else target.setRGB(...(layer.base_color || [0.75, 0.75, 0.78]).slice(0, 3));
  });
  return mat;
}
