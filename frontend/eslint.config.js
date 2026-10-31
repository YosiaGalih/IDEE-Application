import js from "@eslint/js";
import i18next from "eslint-plugin-i18next";
import jsxA11y from "eslint-plugin-jsx-a11y";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";
import local from "./eslint/local-rules.js";

// Raw HTML from any source is XSS (SECURITY_MODEL §2.1): JSX, createElement and the DOM APIs alike.
const RAW_HTML = [
  {
    selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
    message: "No raw HTML: render through React.",
  },
  { selector: "Property[key.name='dangerouslySetInnerHTML']", message: "No raw HTML: render through React." },
  {
    selector: "AssignmentExpression[left.property.name=/^(innerHTML|outerHTML)$/]",
    message: "No raw HTML: render through React.",
  },
  {
    selector: "CallExpression[callee.property.name=/^(insertAdjacentHTML|write|writeln)$/]",
    message: "No raw HTML or document.write.",
  },
];
// WebSocket only in src/api/ws/terminal.ts (ADR-059); server events go through src/api/sse (fetch).
const SOCKETS = [
  {
    selector: "NewExpression[callee.name='WebSocket']",
    message: "WebSocket is only opened in src/api/ws/terminal.ts.",
  },
  {
    selector: "NewExpression[callee.property.name='WebSocket']",
    message: "WebSocket is only opened in src/api/ws/terminal.ts.",
  },
  { selector: "Identifier[name='EventSource']", message: "Use src/api/sse/eventStream.ts, not EventSource." },
];
// Session data never goes into browser storage (frontend.md); the language preference lives in src/i18n.
const STORAGE = [
  {
    selector: "Identifier[name=/^(localStorage|sessionStorage|indexedDB)$/]",
    message: "No browser storage here.",
  },
];
// Text shown to people goes through i18n, template literals included.
const UI_TEXT = [
  {
    selector: "JSXElement > JSXExpressionContainer > TemplateLiteral",
    message: "UI text goes through i18n (t()), not a template literal.",
  },
];
// Only src/api talks to the server, so envelope validation, CSRF and timeouts always apply.
const NETWORK_MEMBERS = [
  {
    selector: "MemberExpression[property.name=/^(fetch|XMLHttpRequest)$/]",
    message: "Call the API through src/api (apiRequest, openEventStream).",
  },
];
const NETWORK_GLOBALS = [
  { name: "fetch", message: "Call the API through src/api (apiRequest, openEventStream)." },
  { name: "XMLHttpRequest", message: "Call the API through src/api." },
];

export default tseslint.config(
  { ignores: ["dist", "coverage", "node_modules", "eslint/fixtures"] },
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  {
    languageOptions: {
      globals: globals.browser,
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { react, "react-hooks": reactHooks, "jsx-a11y": jsxA11y, local },
    settings: { react: { version: "19.3" } },
    rules: {
      ...react.configs.flat.recommended.rules,
      ...react.configs.flat["jsx-runtime"].rules,
      ...reactHooks.configs.recommended.rules,
      // Labels, roles and keyboard access are checked at lint time (WCAG 2.2 AA, PRD §4.1).
      ...jsxA11y.flatConfigs.recommended.rules,
      // AI, web and repo content is never rendered as raw HTML (frontend.md, SECURITY_MODEL).
      "react/no-danger": "error",
      "react/jsx-no-target-blank": ["error", { allowReferrer: false, enforceDynamicLinks: "always" }],
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": "error",
      "local/feature-isolation": "error",
      "no-eval": "error",
      "no-implied-eval": "off",
      "@typescript-eslint/no-implied-eval": "error",
      "no-restricted-syntax": ["error", ...RAW_HTML, ...SOCKETS, ...STORAGE, ...UI_TEXT, ...NETWORK_MEMBERS],
      "no-restricted-globals": ["error", ...NETWORK_GLOBALS],
    },
  },
  {
    // The API layer is the one place that talks to the network (frontend.md).
    files: ["src/api/**"],
    rules: {
      "no-restricted-globals": "off",
      "no-restricted-syntax": ["error", ...RAW_HTML, ...SOCKETS, ...STORAGE, ...UI_TEXT],
    },
  },
  {
    // The language preference is the one value kept in browser storage (no tokens: frontend.md).
    files: ["src/i18n/index.ts"],
    rules: { "no-restricted-syntax": ["error", ...RAW_HTML, ...SOCKETS, ...UI_TEXT] },
  },
  {
    // Every UI string goes through i18n (frontend.md); tests may use literals.
    files: ["src/**/*.tsx"],
    ignores: ["src/**/__tests__/**"],
    plugins: { i18next },
    rules: {
      "i18next/no-literal-string": [
        "error",
        {
          mode: "jsx-only",
          // The plugin's defaults plus test ids: none of these is text a person reads.
          "jsx-attributes": {
            exclude: [
              "className",
              "styleName",
              "style",
              "type",
              "key",
              "id",
              "width",
              "height",
              "data-testid",
            ],
          },
        },
      ],
    },
  },
  {
    files: ["src/api/ws/terminal.ts"],
    rules: { "no-restricted-syntax": ["error", ...RAW_HTML, ...STORAGE] },
  },
  {
    files: ["src/**/__tests__/**"],
    rules: {
      "no-restricted-syntax": ["error", ...RAW_HTML, ...SOCKETS, ...STORAGE],
      "no-restricted-globals": "off",
    },
  },
  {
    files: ["**/*.js", "**/*.mjs"],
    ...tseslint.configs.disableTypeChecked,
    languageOptions: {
      ...tseslint.configs.disableTypeChecked.languageOptions,
      globals: globals.node,
    },
  },
);
