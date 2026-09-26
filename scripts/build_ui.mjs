#!/usr/bin/env node
/** Bundle ui/src/app.js → ui/forge.bundle.js */
import * as esbuild from "esbuild";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
await esbuild.build({
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
