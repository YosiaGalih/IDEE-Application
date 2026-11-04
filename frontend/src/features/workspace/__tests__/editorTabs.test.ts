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
  openId = 1,
): TabsState {
  return tabsReducer(state, { type: "opened", openId, path, kind: "text", content, sha, language: "python" });
}
const tab = (state: TabsState, path = "app.py") => state.tabs.find((candidate) => candidate.path === path);
const edited = (state: TabsState, content: string, path = "app.py") =>
  tabsReducer(state, { type: "edited", path, content });

describe("tabsReducer", () => {
  it("opens a file once and activates the tab that is already there", () => {
    const twice = opened(opened(opened(), "b.py"), "app.py");
    expect(twice.tabs.map((t) => t.path)).toEqual(["app.py", "b.py"]);
    expect(twice.active).toBe("app.py");
  });

  it("is dirty while the text differs from what was saved, and clean again when it matches", () => {
    let state = edited(opened(), "x = 2\n");
    expect(tab(state)?.status).toBe("dirty");
    expect(isDirty(tab(state)!)).toBe(true);
    state = edited(state, "x = 1\n");
    expect(tab(state)?.status).toBe("saved");
  });

  it("marks saved only when the save succeeded, with the new sha", () => {
    let state = edited(opened(), "x = 2\n");
    state = tabsReducer(state, { type: "saveStarted", openId: 1, path: "app.py", content: "x = 2\n" });
    expect(tab(state)?.status).toBe("saving");
    state = tabsReducer(state, {
      type: "saveSucceeded",
      openId: 1,
      path: "app.py",
      content: "x = 2\n",
      sha: SHA_B,
    });
    expect(tab(state)).toMatchObject({ status: "saved", sha: SHA_B, savedContent: "x = 2\n" });
  });

  it("stays dirty when the user typed while the save was on its way", () => {
    let state = edited(opened(), "x = 2\n");
    state = tabsReducer(state, { type: "saveStarted", openId: 1, path: "app.py", content: "x = 2\n" });
    state = edited(state, "x = 3\n");
    state = tabsReducer(state, {
      type: "saveSucceeded",
      openId: 1,
      path: "app.py",
      content: "x = 2\n",
      sha: SHA_B,
    });
    expect(tab(state)).toMatchObject({
      status: "dirty",
      sha: SHA_B,
      savedContent: "x = 2\n",
      content: "x = 3\n",
    });
  });

  it("keeps the status while saving or in conflict when the user edits, even back to the saved text", () => {
    let state = edited(opened(), "x = 2\n");
    state = tabsReducer(state, { type: "saveStarted", openId: 1, path: "app.py", content: "x = 2\n" });
    expect(tab(edited(state, "x = 1\n"))?.status).toBe("saving");
    state = tabsReducer(state, { type: "conflict", openId: 1, path: "app.py", serverSha: SHA_C });
    expect(tab(edited(state, "x = 1\n"))?.status).toBe("conflict");
  });

  it("ignores the answer to a save of a tab that was closed meanwhile", () => {
    let state = tabsReducer(opened(), { type: "saveStarted", openId: 1, path: "app.py", content: "x = 1\n" });
    state = tabsReducer(state, { type: "closed", path: "app.py" });
    state = tabsReducer(state, {
      type: "saveSucceeded",
      openId: 1,
      path: "app.py",
      content: "x = 1\n",
      sha: SHA_B,
    });
    expect(state.tabs).toEqual([]);
  });

  it("ignores a late answer when the tab was closed and the file opened again", () => {
    let state = edited(opened(), "new\n");
    state = tabsReducer(state, { type: "saveStarted", openId: 1, path: "app.py", content: "new\n" });
    state = tabsReducer(state, { type: "closed", path: "app.py" });
    state = opened(state, "app.py", "old\n", SHA_A, 2);
    for (const late of [
      { type: "saveSucceeded", openId: 1, path: "app.py", content: "new\n", sha: SHA_B },
      { type: "saveFailed", openId: 1, path: "app.py", error: { code: "generic", requestId: null } },
      { type: "conflict", openId: 1, path: "app.py", serverSha: SHA_C },
    ] as const) {
      expect(tab(tabsReducer(state, late))).toEqual(tab(state));
    }
  });

  it("keeps the user's text on a conflict and remembers the server's sha", () => {
    let state = edited(opened(), "mine\n");
    state = tabsReducer(state, { type: "conflict", openId: 1, path: "app.py", serverSha: SHA_C });
    expect(tab(state)).toMatchObject({ status: "conflict", content: "mine\n", sha: SHA_A, serverSha: SHA_C });
  });

  it("replaces the text and sha with the server's version on reload, and only then bumps the revision", () => {
    let state = edited(opened(), "mine\n");
    expect(tab(state)?.revision).toBe(0);
    state = tabsReducer(state, { type: "conflict", openId: 1, path: "app.py", serverSha: SHA_C });
    state = tabsReducer(state, { type: "reloaded", path: "app.py", content: "theirs\n", sha: SHA_C });
    expect(tab(state)).toMatchObject({
      status: "saved",
      content: "theirs\n",
      savedContent: "theirs\n",
      sha: SHA_C,
      revision: 1,
    });
  });

  it("records a failed save with its reason and keeps the text", () => {
    let state = edited(opened(), "mine\n");
    const error = { code: "unknown_outcome", requestId: "r-1" } as const;
    state = tabsReducer(state, { type: "saveFailed", openId: 1, path: "app.py", error });
    expect(tab(state)).toMatchObject({ status: "failed", error, content: "mine\n" });
  });

  it("notes a file whose line endings are mixed, which the editor will make uniform", () => {
    expect(tab(opened(initialTabs, "a.txt", "one\r\ntwo\nthree\r\n"), "a.txt")?.mixedEol).toBe(true);
    expect(tab(opened(initialTabs, "b.txt", "one\r\ntwo\r\n"), "b.txt")?.mixedEol).toBe(false);
    expect(tab(opened(initialTabs, "c.txt", "one\ntwo\n"), "c.txt")?.mixedEol).toBe(false);
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
