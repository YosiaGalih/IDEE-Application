import { describe, expect, it } from "vitest";
import en from "@/i18n/locales/en.json";
import id from "@/i18n/locales/id.json";

function keys(value: unknown, prefix = ""): string[] {
  if (typeof value !== "object" || value === null) return [prefix];
  return Object.entries(value).flatMap(([key, inner]) => keys(inner, prefix ? `${prefix}.${key}` : key));
}

describe("locales", () => {
  it("id and en have exactly the same keys", () => {
    expect(keys(en).sort()).toEqual(keys(id).sort());
  });

  it("name no internal work package to the user", () => {
    for (const locale of [id, en]) {
      const internal = keys(locale).filter((key) =>
        /\bWP-\d/.test(
          String(key.split(".").reduce<unknown>((o, k) => (o as Record<string, unknown>)[k], locale)),
        ),
      );
      expect(internal).toEqual([]);
    }
  });

  it("have no empty strings", () => {
    for (const locale of [id, en]) {
      const empty = keys(locale).filter(
        (key) => key.split(".").reduce<unknown>((o, k) => (o as Record<string, unknown>)[k], locale) === "",
      );
      expect(empty).toEqual([]);
    }
  });
});
