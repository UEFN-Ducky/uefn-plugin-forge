// The dock's brushes: pure functions over typed arrays, mirroring backend/kernel/sculpt.py.
// Each stroke runs here for the user's mouse and ships its finished values; the AI's
// strokes run in Python. Positions are the mesh's local centimetres.

export const SCULPT = ["draw", "clay", "inflate", "smooth", "flatten", "grab", "pinch", "crease"];
export const PAINT = ["add", "subtract", "set", "smooth"];

export function falloff(d, r) {
  const t = Math.max(0, 1 - d / r);
  return t * t * (3 - 2 * t);
}

export function adjacency(index, count) {
  const sets = Array.from({ length: count }, () => new Set());
  for (let i = 0; i < index.length; i += 3) {
    const a = index[i], b = index[i + 1], c = index[i + 2];
    sets[a].add(b); sets[a].add(c); sets[b].add(a); sets[b].add(c); sets[c].add(a); sets[c].add(b);
  }
  return sets.map(s => Int32Array.from(s));
}

export function vertexNormals(positions, index) {
  const n = new Float32Array(positions.length);
  for (let i = 0; i < index.length; i += 3) {
    const a = index[i] * 3, b = index[i + 1] * 3, c = index[i + 2] * 3;
    const ux = positions[b] - positions[a], uy = positions[b + 1] - positions[a + 1], uz = positions[b + 2] - positions[a + 2];
    const vx = positions[c] - positions[a], vy = positions[c + 1] - positions[a + 1], vz = positions[c + 2] - positions[a + 2];
    const x = uy * vz - uz * vy, y = uz * vx - ux * vz, z = ux * vy - uy * vx;
    for (const k of [a, b, c]) { n[k] += x; n[k + 1] += y; n[k + 2] += z; }
  }
  for (let i = 0; i < n.length; i += 3) {
    const l = Math.hypot(n[i], n[i + 1], n[i + 2]) || 1;
    n[i] /= l; n[i + 1] /= l; n[i + 2] /= l;
  }
  return n;
}

// [index, falloff] for every vertex within radius of c. ponytail: linear scan; a grid when meshes pass ~200k verts.
export function near(positions, c, r) {
  const out = [];
  const r2 = r * r;
  for (let i = 0, v = 0; v < positions.length; i++, v += 3) {
    const dx = positions[v] - c[0], dy = positions[v + 1] - c[1], dz = positions[v + 2] - c[2];
    const d2 = dx * dx + dy * dy + dz * dz;
    if (d2 < r2) out.push([i, falloff(Math.sqrt(d2), r)]);
  }
  return out;
}

export function mirrored(c, axis) {
  if (axis < 0) return [c];
  const m = c.slice();
  m[axis] = -m[axis];
  return Math.abs(c[axis]) > 1e-3 ? [c, m] : [c];
}

function area(positions, normals, hits) {
  const n = [0, 0, 0], c = [0, 0, 0];
  let total = 0;
  for (const [i, f] of hits) {
    for (let a = 0; a < 3; a++) { n[a] += normals[i * 3 + a] * f; c[a] += positions[i * 3 + a] * f; }
    total += f;
  }
  const l = Math.hypot(n[0], n[1], n[2]) || 1;
  return [n.map(v => v / l), c.map(v => v / (total || 1))];
}

// One sculpt dab. strength: cm for draw/clay/inflate/crease, 0..1 for smooth/flatten/pinch.
export function sculptDab(positions, normals, adj, kind, centre, radius, strength, changed) {
  const hits = near(positions, centre, radius);
  if (!hits.length) return;
  for (const [i] of hits) changed.add(i);
  const k = Math.max(0, Math.min(1, Math.abs(strength)));
  if (kind === "smooth") {
    const next = hits.map(([i]) => {
      const nb = adj[i], out = [0, 0, 0];
      if (!nb.length) return null;
      for (const j of nb) for (let a = 0; a < 3; a++) out[a] += positions[j * 3 + a] / nb.length;
      return out;
    });
    hits.forEach(([i, f], h) => {
      if (next[h]) for (let a = 0; a < 3; a++) positions[i * 3 + a] += (next[h][a] - positions[i * 3 + a]) * k * f;
    });
    return;
  }
  const [n, c] = area(positions, normals, hits);
  for (const [i, f] of hits) {
    const p = [positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]];
    let d = [0, 0, 0];
    if (kind === "draw") d = n.map(v => v * strength * f);
    else if (kind === "inflate") d = [0, 1, 2].map(a => normals[i * 3 + a] * strength * f);
    else if (kind === "clay") {
      const h = [0, 1, 2].reduce((s, a) => s + (c[a] + n[a] * strength - p[a]) * n[a], 0);
      if (h * Math.sign(strength || 1) > 0) d = n.map(v => v * h * f * 0.5);
    } else if (kind === "flatten") {
      const h = [0, 1, 2].reduce((s, a) => s + (p[a] - c[a]) * n[a], 0);
      d = n.map(v => -v * h * k * f);
    } else if (kind === "pinch" || kind === "crease") {
      const to = [0, 1, 2].map(a => centre[a] - p[a]);
      const along = to[0] * n[0] + to[1] * n[1] + to[2] * n[2];
      const pull = kind === "pinch" ? k : 0.3;
      d = to.map((v, a) => (v - n[a] * along) * pull * f);
      if (kind === "crease") d = d.map((v, a) => v - n[a] * strength * f);
    }
    for (let a = 0; a < 3; a++) positions[i * 3 + a] = p[a] + d[a];
  }
}

// Grab: vertices caught at stroke start follow the drag by their falloff.
export function grabStart(positions, centre, radius) {
  return near(positions, centre, radius).map(([i, f]) => [i, f, positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]]);
}

export function grabMove(positions, caught, delta, changed) {
  for (const [i, f, x, y, z] of caught) {
    positions[i * 3] = x + delta[0] * f;
    positions[i * 3 + 1] = y + delta[1] * f;
    positions[i * 3 + 2] = z + delta[2] * f;
    changed.add(i);
  }
}

// One paint dab on a per-vertex value array (width 1 for weights, 4 for RGBA; channel picks the component).
export function paintDab(positions, values, width, channel, adj, kind, centre, radius, strength, value, changed) {
  const hits = near(positions, centre, radius);
  const k = Math.max(0, Math.min(1, strength));
  const at = i => i * width + (width > 1 ? channel : 0);
  const avg = kind === "smooth" ? hits.map(([i]) => {
    const nb = adj[i];
    return nb.length ? nb.reduce((s, j) => s + values[at(j)], 0) / nb.length : null;
  }) : null;
  hits.forEach(([i, f], h) => {
    const v = values[at(i)];
    let out = v;
    if (kind === "add") out = v + k * f;
    else if (kind === "subtract") out = v - k * f;
    else if (kind === "set") out = v + (value - v) * k * f;
    else if (avg[h] !== null) out = v + (avg[h] - v) * k * f;
    values[at(i)] = Math.max(0, Math.min(1, out));
    changed.add(i);
  });
}

// Texture dab: shade every triangle within reach by 3D distance, so strokes cross UV seams cleanly.
// tri = {a, b, c} local positions and {ua, ub, uc} UVs; image = ImageData covering the whole texture.
export function textureDab(image, tris, centre, radius, rgb, strength) {
  const { width: w, height: h, data } = image;
  let painted = 0;
  for (const t of tris) {
    const dx = t.cx - centre[0], dy = t.cy - centre[1], dz = t.cz - centre[2];
    if (Math.sqrt(dx * dx + dy * dy + dz * dz) > t.r + radius) continue;
    const xs = [t.ua[0] * w, t.ub[0] * w, t.uc[0] * w];
    const ys = [(1 - t.ua[1]) * h, (1 - t.ub[1]) * h, (1 - t.uc[1]) * h];
    const den = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2]);
    if (Math.abs(den) < 1e-12) continue;
    const x0 = Math.max(0, Math.floor(Math.min(...xs))), x1 = Math.min(w - 1, Math.ceil(Math.max(...xs)));
    const y0 = Math.max(0, Math.floor(Math.min(...ys))), y1 = Math.min(h - 1, Math.ceil(Math.max(...ys)));
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const px = x + 0.5, py = y + 0.5;
        const l0 = ((ys[1] - ys[2]) * (px - xs[2]) + (xs[2] - xs[1]) * (py - ys[2])) / den;
        const l1 = ((ys[2] - ys[0]) * (px - xs[2]) + (xs[0] - xs[2]) * (py - ys[2])) / den;
        const l2 = 1 - l0 - l1;
        if (l0 < -0.01 || l1 < -0.01 || l2 < -0.01) continue;
        const p0 = t.a[0] * l0 + t.b[0] * l1 + t.c[0] * l2 - centre[0];
        const p1 = t.a[1] * l0 + t.b[1] * l1 + t.c[1] * l2 - centre[1];
        const p2 = t.a[2] * l0 + t.b[2] * l1 + t.c[2] * l2 - centre[2];
        const f = falloff(Math.sqrt(p0 * p0 + p1 * p1 + p2 * p2), radius) * strength;
        if (f <= 0) continue;
        const o = (y * w + x) * 4;
        for (let ch = 0; ch < 3; ch++) data[o + ch] += (rgb[ch] - data[o + ch]) * f;
        data[o + 3] = 255;
        painted++;
      }
    }
  }
  return painted;
}

// Per-triangle records for textureDab: positions + corner UVs + a bounding sphere.
export function paintTriangles(positions, index, cornerUv) {
  const out = [];
  for (let t = 0; t < index.length / 3; t++) {
    const pt = k => [positions[index[t * 3 + k] * 3], positions[index[t * 3 + k] * 3 + 1], positions[index[t * 3 + k] * 3 + 2]];
    const a = pt(0), b = pt(1), c = pt(2);
    const cx = (a[0] + b[0] + c[0]) / 3, cy = (a[1] + b[1] + c[1]) / 3, cz = (a[2] + b[2] + c[2]) / 3;
    const r = Math.max(...[a, b, c].map(p => Math.hypot(p[0] - cx, p[1] - cy, p[2] - cz)));
    out.push({ a, b, c, cx, cy, cz, r, ua: [cornerUv[t * 6], cornerUv[t * 6 + 1]], ub: [cornerUv[t * 6 + 2], cornerUv[t * 6 + 3]], uc: [cornerUv[t * 6 + 4], cornerUv[t * 6 + 5]] });
  }
  return out;
}

// Weight heatmap: blue (0) through green to red (1).
export function heat(v) {
  const t = Math.max(0, Math.min(1, v));
  return t < 0.5 ? [0, t * 2, 1 - t * 2] : [(t - 0.5) * 2, 1 - (t - 0.5) * 2, 0];
}
