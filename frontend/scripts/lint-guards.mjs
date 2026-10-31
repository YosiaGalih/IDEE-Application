// Proves each contract lint rule still rejects a violation (frontend.md, IMPLEMENTATION_DESIGN_V2 §4.4):
// the fixtures are written into src/features for the run, linted, then removed.
import { mkdirSync, rmdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { ESLint } from "eslint";

const root = path.resolve(import.meta.dirname, "..");
const dirA = path.join(root, "src/features/lintguard-a");
const dirB = path.join(root, "src/features/lintguard-b");

const cases = [
  {
    file: path.join(dirA, "CrossFeature.ts"),
    source: 'import { b } from "@/features/lintguard-b/b";\nexport const a = b;\n',
    rule: "local/feature-isolation",
  },
  {
    file: path.join(dirA, "CrossFeatureRelative.ts"),
    source: 'import { b } from "../lintguard-b/b";\nexport const c = b;\n',
    rule: "local/feature-isolation",
  },
  {
    file: path.join(dirA, "Socket.ts"),
    source: 'export const s = new WebSocket("wss://example.test");\n',
    rule: "no-restricted-syntax",
  },
  {
    file: path.join(dirA, "Literal.tsx"),
    source: "export function Literal() {\n  return <p>Teks tanpa i18n</p>;\n}\n",
    rule: "i18next/no-literal-string",
  },
  {
    file: path.join(dirA, "SocketMember.ts"),
    source: 'export const s = new globalThis.WebSocket("wss://example.test");\n',
    rule: "no-restricted-syntax",
  },
  {
    file: path.join(dirA, "Events.ts"),
    source: 'export const e = new EventSource("/api/v1/x");\n',
    rule: "no-restricted-syntax",
  },
  {
    file: path.join(dirA, "InnerHtml.ts"),
    source: "export function put(el: HTMLElement, html: string) {\n  el.innerHTML = html;\n}\n",
    rule: "no-restricted-syntax",
  },
  {
    file: path.join(dirA, "CreateElement.ts"),
    source:
      'import { createElement } from "react";\nexport const d = (html: string) => createElement("div", { dangerouslySetInnerHTML: { __html: html } });\n',
    rule: "no-restricted-syntax",
  },
  {
    file: path.join(dirA, "Storage.ts"),
    source: 'export const save = (t: string) => window.localStorage.setItem("token", t);\n',
    rule: "no-restricted-syntax",
  },
  {
    file: path.join(dirA, "Fetch.ts"),
    source: 'export const get = () => fetch("/api/v1/projects");\n',
    rule: "no-restricted-globals",
  },
  {
    file: path.join(dirA, "WindowFetch.ts"),
    source: 'export const get = () => window.fetch("/api/v1/projects");\n',
    rule: "no-restricted-syntax",
  },
  {
    file: path.join(dirA, "Eval.ts"),
    source: "export const run = (code: string): unknown => eval(code);\n",
    rule: "no-eval",
  },
  {
    file: path.join(dirA, "TemplateText.tsx"),
    source: "export function T({ n }: { n: number }) {\n  return <p>{`Halo ${n}`}</p>;\n}\n",
    rule: "no-restricted-syntax",
  },
  {
    file: path.join(dirA, "Danger.tsx"),
    source:
      "export function Danger({ html }: { html: string }) {\n  return <div dangerouslySetInnerHTML={{ __html: html }} />;\n}\n",
    rule: "react/no-danger",
  },
];

let failed = false;
try {
  mkdirSync(dirA, { recursive: true });
  mkdirSync(dirB, { recursive: true });
  writeFileSync(path.join(dirB, "b.ts"), "export const b = 1;\n");
  for (const { file, source } of cases) writeFileSync(file, source);
  const eslint = new ESLint({ cwd: root });
  const results = await eslint.lintFiles(cases.map((c) => c.file));
  for (const { file, rule } of cases) {
    const messages = results.find((r) => r.filePath === file)?.messages ?? [];
    const hit = messages.some((m) => m.ruleId === rule);
    console.log(`${hit ? "ok  " : "FAIL"} ${path.relative(root, file)} -> ${rule}`);
    if (!hit) {
      failed = true;
      console.log(messages.map((m) => `     ${m.ruleId}: ${m.message}`).join("\n"));
    }
  }
} finally {
  rmSync(dirA, { recursive: true, force: true });
  rmSync(dirB, { recursive: true, force: true });
  try {
    rmdirSync(path.join(root, "src/features")); // only when the run created it and it is empty
  } catch {
    // features of the app live there
  }
}
if (failed) {
  console.error("lint guards: a contract rule no longer rejects its violation");
  process.exit(1);
}
console.log("lint guards: every contract rule rejects its violation");
