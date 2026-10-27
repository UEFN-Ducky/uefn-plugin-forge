import test from "node:test";
import assert from "node:assert/strict";
import { colorFor, edgeArrow, flyerParts, makeSender, makeThrottle, triangles } from "../ui/src/presence.js";

test("my camera goes out only when it changed, and at most every interval", () => {
  const due = makeSender(100);
  assert.equal(due(0, { pos: [1, 2, 3] }), true);
  assert.equal(due(50, { pos: [1, 2, 4] }), false);   // too soon
  assert.equal(due(150, { pos: [1, 2, 3] }), false);  // nothing new
  assert.equal(due(160, { pos: [1, 2, 4] }), true);
  const go = makeThrottle(50);
  assert.deepEqual([go(0), go(20), go(49), go(50), go(60), go(101)], [true, false, false, true, false, true]);
});

test("everyone keeps one colour, and names differ", () => {
  assert.equal(colorFor("Ben").getHex(), colorFor("Ben").getHex());
  assert.notEqual(colorFor("Ben").getHex(), colorFor("Ana").getHex());
});

test("a ping off screen gets an arrow on the edge, toward it", () => {
  assert.equal(edgeArrow(0.2, -0.4), null);
  const right = edgeArrow(3, 0);
  assert.ok(Math.abs(right.x - 0.9) < 1e-9);
  assert.equal(right.angle, 0);
  const behind = edgeArrow(0.1, 0.5, true);  // behind the camera: the other way
  assert.ok(behind.y < 0 && Math.abs(Math.max(Math.abs(behind.x), Math.abs(behind.y)) - 0.9) < 1e-9);
});

test("avatars stay small: at most 300 triangles each", () => {
  for (const kind of ["person", "ducky"]) {
    const n = triangles(flyerParts(kind));
    assert.ok(n > 20 && n <= 300, `${kind}: ${n}`);
  }
});
