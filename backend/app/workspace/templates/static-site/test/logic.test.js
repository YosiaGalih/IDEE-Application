import { test } from "node:test";
import assert from "node:assert/strict";
import { countWords } from "../src/logic.js";

test("countWords", () => {
  assert.equal(countWords(""), 0);
  assert.equal(countWords("  satu dua   tiga "), 3);
});
