import "i18next";
import type id from "./locales/id.json";

// Typed keys: a key missing from id.json is a typecheck error (review WP-1.4a, spec WP-1.4b).
// en.json must have the same keys (src/i18n/__tests__/locales.test.ts).
declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "translation";
    resources: { translation: typeof id };
    returnNull: false;
  }
}
