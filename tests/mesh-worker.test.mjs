// A 200k-triangle mesh decodes in a worker thread (message round trip), off the main thread, in 300 ms.
import test from "node:test";
import assert from "node:assert/strict";
import { Worker } from "node:worker_threads";
import { instanceGroups } from "../ui/src/mesh-engine.js";

const b64 = typed => Buffer.from(typed.buffer, typed.byteOffset, typed.byteLength).toString("base64");

function grid(tris) {
  const w = Math.floor(Math.sqrt(tris / 2)), n = (w + 1) * (w + 1);
  const pos = new Float32Array(n * 3), idx = new Uint32Array(w * w * 6);
  for (let y = 0; y <= w; y++) for (let x = 0; x <= w; x++) pos.set([x, y, (x * y) % 7], (y * (w + 1) + x) * 3);
  let k = 0;
  for (let y = 0; y < w; y++) for (let x = 0; x < w; x++) {
    const a = y * (w + 1) + x, b = a + 1, c = a + w + 1, d = a + w + 2;
    idx.set([a, b, d, a, d, c], k); k += 6;
  }
  return { pos, idx, faces: new Uint32Array(idx.length / 3).map((_, i) => i) };
}

function spawn() {
  // worker_threads has no `self`; give mesh-worker.js the browser shape it expects.
  const url = new URL("../ui/src/mesh-worker.js", import.meta.url).href;
  return new Worker(`const {parentPort}=require("node:worker_threads");
    globalThis.self={postMessage:(d,t)=>parentPort.postMessage(d,t||[])};
    parentPort.on("message",d=>self.onmessage({data:d}));
    import(${JSON.stringify(url)});`, { eval: true });
}

test("200k-tri mesh decodes in the worker in <= 300 ms", async () => {
  const {pos, idx, faces} = grid(200000);
  const worker = spawn();
  const next = () => new Promise((resolve, reject) => { worker.once("message", resolve); worker.once("error", reject); });
  assert.ok((await next()).ready);
  const params = {id: "m", revision: 1, pb: b64(pos), ib: b64(idx), fb: b64(faces), smooth: false};
  const t = performance.now();
  worker.postMessage({id: 1, op: "prepare", params});
  const {result, error} = await next();
  const ms = performance.now() - t;
  assert.equal(error, undefined);
  assert.equal(result.indices.length / 3, idx.length / 3);
  assert.deepEqual([...result.positions.slice(0, 6)], [...pos.slice(0, 6)]);
  console.log(`  prepare 200k tris in worker: ${ms.toFixed(0)} ms`);
  assert.ok(ms <= 300, `took ${ms} ms`);
  worker.postMessage({id: 2, op: "simplify", params: {key: result.key, cells: 16}});
  const simple = await next();
  assert.ok(simple.result.length > 0 && simple.result.length < idx.length / 4);
  await worker.terminate();
});

test("500 linked wheels group into one draw", () => {
  const m = new Float32Array(16).fill(0);
  const groups = instanceGroups(Array.from({length: 500}, () => ({key: "wheel", matrix: m})));
  assert.equal(groups.size, 1);
  assert.equal(groups.get("wheel").length, 500 * 16);
});
