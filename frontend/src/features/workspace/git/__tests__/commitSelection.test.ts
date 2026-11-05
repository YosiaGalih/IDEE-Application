import { describe, expect, it } from "vitest";
import { emptySelection, pickedPaths, reconcileSelection, setAll, toggle } from "../commitSelection";

const hidden = { tickNew: true };
const shown = { tickNew: false };

describe("commit selection", () => {
  it("ticks every file of the list the user first sees", () => {
    const state = reconcileSelection(emptySelection, ["a.py", "b.py"], hidden);
    expect(pickedPaths(state, ["a.py", "b.py"])).toEqual(["a.py", "b.py"]);
  });

  it("keeps a file the user unticked unticked when the list is read again", () => {
    let state = reconcileSelection(emptySelection, ["a.py", "b.py"], hidden);
    state = toggle(state, "b.py");
    state = reconcileSelection(state, ["a.py", "b.py"], shown);
    expect(pickedPaths(state, ["a.py", "b.py"])).toEqual(["a.py"]);
  });

  it("leaves a file that appears while the user looks at the list unticked, and names it", () => {
    let state = reconcileSelection(emptySelection, ["a.py"], hidden);
    state = reconcileSelection(state, ["a.py", ".env"], shown);
    expect(pickedPaths(state, ["a.py", ".env"])).toEqual(["a.py"]);
    expect([...state.fresh]).toEqual([".env"]);
    // Any choice by the user ends the notice.
    expect([...toggle(state, ".env").fresh]).toEqual([]);
  });

  it("ticks files that appeared while the view was hidden: the user sees them ticked when it opens", () => {
    let state = reconcileSelection(emptySelection, ["a.py"], hidden);
    state = reconcileSelection(state, ["a.py", "b.py"], hidden);
    expect(pickedPaths(state, ["a.py", "b.py"])).toEqual(["a.py", "b.py"]);
    expect(state.fresh.size).toBe(0);
  });

  it("drops a file that left the list, and treats it as new when it comes back", () => {
    let state = reconcileSelection(emptySelection, ["a.py", "b.py"], hidden);
    state = reconcileSelection(state, ["a.py"], shown);
    expect(pickedPaths(state, ["a.py"])).toEqual(["a.py"]);
    state = reconcileSelection(state, ["a.py", "b.py"], shown);
    expect(pickedPaths(state, ["a.py", "b.py"])).toEqual(["a.py"]);
    expect([...state.fresh]).toEqual(["b.py"]);
  });

  it("selects and clears all", () => {
    const listed = ["a.py", "b.py"];
    let state = setAll(reconcileSelection(emptySelection, listed, hidden), listed, false);
    expect(pickedPaths(state, listed)).toEqual([]);
    state = setAll(state, listed, true);
    expect(pickedPaths(state, listed)).toEqual(listed);
  });
});
