import { describe, expect, it } from "vitest";
import { monacoLanguage } from "../monacoLanguages";

describe("monacoLanguage", () => {
  it.each([
    ["python", "python"],
    ["typescript", "typescript"],
    ["toml", "ini"],
    ["shell", "shell"],
    ["brainfuck", "plaintext"],
    [null, "plaintext"],
  ])("maps %s to %s", (language, id) => {
    expect(monacoLanguage(language)).toBe(id);
  });
});
