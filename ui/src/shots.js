// Camera shot paths. backend/shots.py evaluates the same curve; tests/shots.test.mjs and
// backend/test_live.py pin both to the same numbers.

export const EASES = ["linear", "in_out", "in", "out"];

export function ease(u, kind = "linear") {
  u = Math.min(1, Math.max(0, Number(u) || 0));
  if (kind === "in_out") return u * u * (3 - 2 * u);
  if (kind === "in") return u * u;
  if (kind === "out") return 1 - (1 - u) * (1 - u);
  return u;
}

function catmull(p0, p1, p2, p3, u) {
  const u2 = u * u, u3 = u2 * u;
  return [0, 1, 2].map(i => 0.5 * (2 * p1[i] + (p2[i] - p0[i]) * u + (2 * p0[i] - 5 * p1[i] + 4 * p2[i] - p3[i]) * u2
    + (3 * p1[i] - p0[i] - 3 * p2[i] + p3[i]) * u3));
}

/** Camera {pos, target, fov} at t seconds: a smooth curve through every key. */
export function evalPath(keys, t, kind = "linear") {
  keys = [...(keys || [])].sort((a, b) => a.t - b.t);
  if (!keys.length) throw new Error("a shot needs at least one key");
  const first = keys[0].t, last = keys[keys.length - 1].t;
  if (keys.length === 1 || last <= first) return { pos: [...keys[0].pos], target: [...keys[0].target], fov: keys[0].fov };
  const tt = first + ease((t - first) / (last - first), kind) * (last - first);
  let i = 0;
  while (i < keys.length - 2 && keys[i + 1].t < tt) i++;
  const a = keys[i], b = keys[i + 1], span = b.t - a.t;
  const u = span <= 0 ? 0 : Math.min(1, Math.max(0, (tt - a.t) / span));
  const before = keys[Math.max(0, i - 1)], after = keys[Math.min(keys.length - 1, i + 2)];
  return {
    pos: catmull(before.pos, a.pos, b.pos, after.pos, u),
    target: catmull(before.target, a.target, b.target, after.target, u),
    fov: a.fov + (b.fov - a.fov) * u,
  };
}

export function shotDuration(shot) {
  const last = Math.max(0, ...(shot?.keys || []).map(k => k.t));
  return Math.max(Number(shot?.duration) || 0, last);
}
