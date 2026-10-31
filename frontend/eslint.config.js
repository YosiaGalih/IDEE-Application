import js from "@eslint/js";
import i18next from "eslint-plugin-i18next";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";
import local from "./eslint/local-rules.js";

export default tseslint.config(
  { ignores: ["dist", "coverage", "node_modules", "eslint/fixtures"] },
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  {
    languageOptions: {
      globals: globals.browser,
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { react, "react-hooks": reactHooks, local },
    settings: { react: { version: "19.3" } },
    rules: {
      ...react.configs.flat.recommended.rules,
      ...react.configs.flat["jsx-runtime"].rules,
      ...reactHooks.configs.recommended.rules,
      // AI, web and repo content is never rendered as raw HTML (frontend.md, SECURITY_MODEL).
      "react/no-danger": "error",
      "react/jsx-no-target-blank": ["error", { allowReferrer: false, enforceDynamicLinks: "always" }],
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": "error",
      "local/feature-isolation": "error",
      "no-restricted-syntax": [
        "error",
        {
          selector: "NewExpression[callee.name='WebSocket']",
          message: "WebSocket is only opened in src/api/ws/terminal.ts (ADR-059).",
        },
      ],
    },
  },
  {
    // Every UI string goes through i18n (frontend.md); tests may use literals.
    files: ["src/**/*.tsx"],
    ignores: ["src/**/__tests__/**"],
    plugins: { i18next },
    rules: { "i18next/no-literal-string": ["error", { mode: "jsx-only" }] },
  },
  { files: ["src/api/ws/terminal.ts"], rules: { "no-restricted-syntax": "off" } },
  {
    files: ["**/*.js", "**/*.mjs"],
    ...tseslint.configs.disableTypeChecked,
    languageOptions: {
      ...tseslint.configs.disableTypeChecked.languageOptions,
      globals: globals.node,
    },
  },
);
