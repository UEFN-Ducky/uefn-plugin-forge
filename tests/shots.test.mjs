import test from "node:test";
import assert from "node:assert/strict";
import { ease, evalPath, shotDuration } from "../ui/src/shots.js";

// backend/test_live.py pins backend/shots.py to the same numbers.
const two = [{ t: 0, pos: [0, 0, 0], target: [0, 0, 0], fov: 30 }, { t: 2, pos: [100, 0, 0], target: [0, 0, 0], fov: 60 }];

test("the path goes through its keys on a smooth curve", () => {
  assert.equal(evalPath(two, 1).pos[0], 50);
  assert.equal(evalPath(two, 1).fov, 45);
  assert.equal(evalPath(two, 0.5).pos[0], 20.3125);
  assert.ok(Math.abs(evalPath(two, 0.5, "in_out").pos[0] - 11.0931396484375) < 1e-9);
  assert.deepEqual(evalPath(two, 5).pos, [100, 0, 0]);
  assert.deepEqual(evalPath(two, -1).pos, [0, 0, 0]);
  const three = [two[0], { t: 1, pos: [100, 0, 0], target: [0, 0, 0], fov: 30 }, { t: 2, pos: [100, 100, 0], target: [0, 0, 0], fov: 30 }];
  assert.deepEqual(evalPath(three, 0.5).pos.slice(0, 2), [50, -6.25]);
  assert.deepEqual(evalPath(three, 1).pos, [100, 0, 0]);
});

test("eases and durations", () => {
  assert.equal(ease(0.5, "in_out"), 0.5);
  assert.equal(ease(2, "in"), 1);
  assert.equal(ease(-1, "out"), 0);
  assert.equal(shotDuration({ duration: 4, keys: [{ t: 6 }] }), 6);
  assert.equal(shotDuration({ duration: 4, keys: [] }), 4);
  assert.throws(() => evalPath([], 0));
});
