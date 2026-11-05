import { describe, expect, it } from "vitest";
import { emptySelection, pickedPaths, reconcileSelection, setAll, toggle } from "../commitSelection";

describe("commit selection", () => {
  it("ticks every file the first time the list arrives", () => {
    const state = reconcileSelection(emptySelection, ["a.py", "b.py"]);
    expect(pickedPaths(state, ["a.py", "b.py"])).toEqual(["a.py", "b.py"]);
  });

  it("keeps a file the user unticked unticked when the list is read again", () => {
    let state = reconcileSelection(emptySelection, ["a.py", "b.py"]);
    state = toggle(state, "b.py");
    state = reconcileSelection(state, ["a.py", "b.py"]);
    expect(pickedPaths(state, ["a.py", "b.py"])).toEqual(["a.py"]);
  });

  it("ticks a file that appears later", () => {
    let state = reconcileSelection(emptySelection, ["a.py"]);
    state = toggle(state, "a.py");
    state = reconcileSelection(state, ["a.py", "c.py"]);
    expect(pickedPaths(state, ["a.py", "c.py"])).toEqual(["c.py"]);
  });

  it("drops a file that left the list, and never picks a path that is not listed", () => {
    let state = reconcileSelection(emptySelection, ["a.py", "b.py"]);
    state = reconcileSelection(state, ["a.py"]);
    expect(pickedPaths(state, ["a.py"])).toEqual(["a.py"]);
    // b.py comes back: a file the list did not show in between is new again, so it is ticked.
    state = reconcileSelection(state, ["a.py", "b.py"]);
    expect(pickedPaths(state, ["a.py", "b.py"])).toEqual(["a.py", "b.py"]);
    expect(pickedPaths(state, ["a.py"])).toEqual(["a.py"]);
  });

  it("selects and clears all", () => {
    const listed = ["a.py", "b.py"];
    let state = setAll(reconcileSelection(emptySelection, listed), listed, false);
    expect(pickedPaths(state, listed)).toEqual([]);
    state = setAll(state, listed, true);
    expect(pickedPaths(state, listed)).toEqual(listed);
  });
});
