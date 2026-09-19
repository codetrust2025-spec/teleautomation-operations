// Save what a test rendered, so scripts/layout_harness.py can lay it out in a
// real browser. jsdom performs no layout: a test can prove what renders but not
// whether it fits, and the harness is where that second question is answered.
//
// A no-op unless LAYOUT_CAPTURE_DIR is set, so a test may call it freely and a
// normal run -- CI included -- never writes anything.
//
//   LAYOUT_CAPTURE_DIR=../.layout-harness/captures npx vitest run <test file>
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export function captureLayout(name, root = document.body) {
  const dir = typeof process !== "undefined" ? process.env.LAYOUT_CAPTURE_DIR : undefined;
  if (!dir) return null;
  mkdirSync(dir, { recursive: true });
  const file = join(dir, `${name}.html`);
  writeFileSync(file, root.outerHTML, "utf-8");
  return file;
}
