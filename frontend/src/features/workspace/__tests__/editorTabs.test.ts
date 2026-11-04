import { describe, expect, it } from "vitest";
import { initialTabs, isDirty, tabsReducer, type TabsState } from "../editorTabs";

const SHA_A = "a".repeat(64);
const SHA_B = "b".repeat(64);
const SHA_C = "c".repeat(64);

function opened(
  state: TabsState = initialTabs,
  path = "app.py",
  content = "x = 1\n",
  sha = SHA_A,
): TabsState {
  return tabsReducer(state, { type: "opened", path, kind: "text", content, sha, language: "python" });
}
const tab = (state: TabsState, path = "app.py") => state.tabs.find((candidate) => candidate.path === path);

describe("tabsReducer", () => {
  it("opens a file once and activates the tab that is already there", () => {
    const twice = opened(opened(opened(), "b.py"), "app.py");
    expect(twice.tabs.map((t) => t.path)).toEqual(["app.py", "b.py"]);
    expect(twice.active).toBe("app.py");
  });

  it("is dirty while the text differs from what was saved, and clean again when it matches", () => {
    let state = tabsReducer(opened(), { type: "edited", path: "app.py", content: "x = 2\n" });
    expect(tab(state)?.status).toBe("dirty");
    expect(isDirty(tab(state)!)).toBe(true);
    state = tabsReducer(state, { type: "edited", path: "app.py", content: "x = 1\n" });
    expect(tab(state)?.status).toBe("saved");
  });

  it("marks saved only when the save succeeded, with the new sha", () => {
    let state = tabsReducer(opened(), { type: "edited", path: "app.py", content: "x = 2\n" });
    state = tabsReducer(state, { type: "saveStarted", path: "app.py", content: "x = 2\n" });
    expect(tab(state)?.status).toBe("saving");
    state = tabsReducer(state, { type: "saveSucceeded", path: "app.py", content: "x = 2\n", sha: SHA_B });
    expect(tab(state)).toMatchObject({ status: "saved", sha: SHA_B, savedContent: "x = 2\n" });
  });

  it("stays dirty when the user typed while the save was on its way", () => {
    let state = tabsReducer(opened(), { type: "edited", path: "app.py", content: "x = 2\n" });
    state = tabsReducer(state, { type: "saveStarted", path: "app.py", content: "x = 2\n" });
    state = tabsReducer(state, { type: "edited", path: "app.py", content: "x = 3\n" });
    state = tabsReducer(state, { type: "saveSucceeded", path: "app.py", content: "x = 2\n", sha: SHA_B });
    expect(tab(state)).toMatchObject({
      status: "dirty",
      sha: SHA_B,
      savedContent: "x = 2\n",
      content: "x = 3\n",
    });
  });

  it("ignores the answer to a save of a tab that was closed meanwhile", () => {
    let state = tabsReducer(opened(), { type: "saveStarted", path: "app.py", content: "x = 1\n" });
    state = tabsReducer(state, { type: "closed", path: "app.py" });
    state = tabsReducer(state, { type: "saveSucceeded", path: "app.py", content: "x = 1\n", sha: SHA_B });
    expect(state.tabs).toEqual([]);
  });

  it("keeps the user's text on a conflict and remembers the server's sha", () => {
    let state = tabsReducer(opened(), { type: "edited", path: "app.py", content: "mine\n" });
    state = tabsReducer(state, { type: "conflict", path: "app.py", serverSha: SHA_C });
    expect(tab(state)).toMatchObject({ status: "conflict", content: "mine\n", sha: SHA_A, serverSha: SHA_C });
  });

  it("replaces the text and sha with the server's version on reload", () => {
    let state = tabsReducer(opened(), { type: "edited", path: "app.py", content: "mine\n" });
    state = tabsReducer(state, { type: "conflict", path: "app.py", serverSha: SHA_C });
    state = tabsReducer(state, { type: "reloaded", path: "app.py", content: "theirs\n", sha: SHA_C });
    expect(tab(state)).toMatchObject({
      status: "saved",
      content: "theirs\n",
      savedContent: "theirs\n",
      sha: SHA_C,
    });
  });

  it("records a failed save with its reason and keeps the text", () => {
    let state = tabsReducer(opened(), { type: "edited", path: "app.py", content: "mine\n" });
    state = tabsReducer(state, { type: "saveFailed", path: "app.py", error: "not_found" });
    expect(tab(state)).toMatchObject({ status: "failed", error: "not_found", content: "mine\n" });
  });

  it("activates a neighbour when the active tab closes", () => {
    let state = opened(opened(opened(), "b.py"), "c.py");
    state = tabsReducer(state, { type: "closed", path: "c.py" });
    expect(state.active).toBe("b.py");
    state = tabsReducer(state, { type: "closed", path: "b.py" });
    state = tabsReducer(state, { type: "closed", path: "app.py" });
    expect(state.active).toBeNull();
  });
});
