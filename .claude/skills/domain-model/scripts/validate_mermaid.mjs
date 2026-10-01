// Parse every ```mermaid block in the given Markdown files with Mermaid's own
// parser (syntax check only; nothing is rendered, so no browser is needed).
//
// Node resolves "mermaid"/"jsdom" relative to THIS file, so copy it into a
// scratch directory that has them installed:
//   cd <scratch> && npm init -y && npm i mermaid@11 jsdom
//   cp <skill>/scripts/validate_mermaid.mjs . && node validate_mermaid.mjs <files...>
// Exit code is 0 even on failure; read the "N/M diagrams parse" line.
import { JSDOM } from "jsdom";
import fs from "node:fs";
const dom = new JSDOM("<!doctype html><html><body></body></html>");
globalThis.window = dom.window; globalThis.document = dom.window.document;
globalThis.DOMParser = dom.window.DOMParser; globalThis.Element = dom.window.Element;
const { default: mermaid } = await import("mermaid");
mermaid.initialize({ startOnLoad: false });
let bad = 0, total = 0;
for (const file of process.argv.slice(2)) {
  const text = fs.readFileSync(file, "utf8");
  for (const m of text.matchAll(/```mermaid\n([\s\S]*?)```/g)) {
    total++;
    try { await mermaid.parse(m[1]); }
    catch (e) { bad++; console.log("FAIL", file, String(e.message ?? e).slice(0, 300)); }
  }
}
console.log(`${total - bad}/${total} diagrams parse`);
