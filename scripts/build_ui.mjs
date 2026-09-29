#!/usr/bin/env node
/** Bundle ui/src/app.js → ui/forge.bundle.js */
import * as esbuild from "esbuild";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { readFile, writeFile } from "node:fs/promises";
import "../ui/src/brush.check.mjs";  // a broken brush fails the build

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const worker = await esbuild.build({
  entryPoints: [join(root, "ui", "src", "mesh-worker.js")], bundle: true,
  write: false, format: "iife", platform: "browser", target: ["chrome110"], minify: true,
});
await esbuild.build({
  define: { FORGE_MESH_WORKER: JSON.stringify(worker.outputFiles[0].text) },
  entryPoints: [join(root, "ui", "src", "app.js")],
  bundle: true,
  outfile: join(root, "ui", "forge.bundle.js"),
  format: "iife",
  platform: "browser",
  target: ["chrome110"],
  minify: true,
  sourcemap: false,
  logLevel: "info",
});

// Three embeds GLSL template strings; normalize insignificant shader whitespace.
const bundlePath = join(root, "ui", "forge.bundle.js");
const bundle = await readFile(bundlePath, "utf8");
await writeFile(bundlePath, bundle.replace(/[ \t]+$/gm, "").replace(/^ +(?=\t)/gm, ""));
