// The dock's brushes, checked before every UI build (scripts/build_ui.mjs imports this file).
import assert from "node:assert/strict";
import * as B from "./brush.js";

// A flat 3 x 3 grid, 10 cm apart, facing +Z. Vertex 4 is the centre (0, 0, 0).
const pos = new Float32Array(27);
for (let y = 0; y < 3; y++) for (let x = 0; x < 3; x++) pos.set([x * 10 - 10, y * 10 - 10, 0], (y * 3 + x) * 3);
const index = [];
for (let y = 0; y < 2; y++) for (let x = 0; x < 2; x++) { const i = y * 3 + x; index.push(i, i + 1, i + 4, i, i + 4, i + 3); }
const idx = Uint32Array.from(index), adj = B.adjacency(idx, 9);

// Draw lifts the centre by its strength along the normal; a corner outside the radius stays put.
const lifted = pos.slice(), changed = new Set();
B.sculptDab(lifted, B.vertexNormals(lifted, idx), adj, "draw", [0, 0, 0], 12, 2, changed);
assert.ok(Math.abs(lifted[14] - 2) < 1e-6 && lifted[2] === 0 && changed.has(4) && !changed.has(0), "draw");
assert.deepEqual(B.mirrored([5, 0, 0], 0), [[5, 0, 0], [-5, 0, 0]]);
assert.equal(B.mirrored([0, 0, 0], 0).length, 1, "a dab on the mirror plane is not doubled");

// Weights add up and clamp at 1; RGBA painting touches only its channel.
const weights = new Float32Array(9);
for (let k = 0; k < 3; k++) B.paintDab(pos, weights, 1, 0, adj, "add", [0, 0, 0], 12, 0.5, 1, new Set());
assert.ok(weights[4] === 1 && weights[1] > 0 && weights[1] < 1 && weights[0] === 0, "add clamps");
const rgba = new Float32Array(36);
B.paintDab(pos, rgba, 4, 2, adj, "set", [0, 0, 0], 12, 1, 1, new Set());
assert.ok(rgba[18] === 1 && rgba[16] === 0 && rgba[17] === 0, "set paints one channel");

// Two squares side by side in 3D, laid out swapped in UV: the 3D seam at x = 0 lands on opposite
// texture edges. A dab on the seam must paint both edges and leave the texture's middle alone.
const quads = new Float32Array([-10, 0, 0, 0, 0, 0, 0, 10, 0, -10, 10, 0, 0, 0, 0, 10, 0, 0, 10, 10, 0, 0, 10, 0]);
const qi = Uint32Array.from([0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]);
const uv = [[0.5, 0], [1, 0], [1, 1], [0.5, 1], [0, 0], [0.5, 0], [0.5, 1], [0, 1]];
const image = { width: 16, height: 16, data: new Uint8ClampedArray(16 * 16 * 4) };
B.textureDab(image, B.paintTriangles(quads, qi, Float32Array.from([...qi].flatMap(v => uv[v]))), [0, 5, 0], 3, [255, 0, 0], 1);
const red = (x, y) => image.data[(y * 16 + x) * 4];
assert.ok(red(15, 8) > 200 && red(0, 8) > 200 && red(8, 8) === 0, "texture dabs cross UV seams");
