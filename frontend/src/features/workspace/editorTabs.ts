/**
 * The open files of the editor (FR-027, FR-030). Pure, so the save rules are testable without Monaco:
 * a tab is "saved" only after the server accepted that exact text, and a conflict keeps the user's text.
 */

export type TabKind = "text" | "binary" | "large";
export type TabStatus = "saved" | "dirty" | "saving" | "failed" | "conflict";

export type Tab = {
  path: string;
  kind: TabKind;
  /** What the editor shows now. */
  content: string;
  /** What the server holds at `sha` (the last read or the last accepted save). */
  savedContent: string;
  sha: string;
  language: string | null;
  status: TabStatus;
  /** The text a save in flight is writing. */
  saving?: string | undefined;
  /** A failed save's reason (an API error code or kind). */
  error?: string | undefined;
  /** The server's sha when a save hit a conflict. */
  serverSha?: string | null | undefined;
};

export type TabsState = { tabs: Tab[]; active: string | null };
export const initialTabs: TabsState = { tabs: [], active: null };

export type TabsAction =
  | { type: "opened"; path: string; kind: TabKind; content: string; sha: string; language: string | null }
  | { type: "activated"; path: string }
  | { type: "edited"; path: string; content: string }
  | { type: "saveStarted"; path: string; content: string }
  | { type: "saveSucceeded"; path: string; content: string; sha: string }
  | { type: "saveFailed"; path: string; error: string }
  | { type: "conflict"; path: string; serverSha: string | null }
  | { type: "reloaded"; path: string; content: string; sha: string }
  | { type: "closed"; path: string };

export const isDirty = (tab: Tab) => tab.content !== tab.savedContent;

const statusOf = (tab: Tab): TabStatus => (isDirty(tab) ? "dirty" : "saved");

function update(state: TabsState, path: string, change: (tab: Tab) => Tab): TabsState {
  // An answer for a tab that is gone (closed meanwhile) changes nothing.
  if (!state.tabs.some((tab) => tab.path === path)) return state;
  return { ...state, tabs: state.tabs.map((tab) => (tab.path === path ? change(tab) : tab)) };
}

export function tabsReducer(state: TabsState, action: TabsAction): TabsState {
  switch (action.type) {
    case "opened": {
      if (state.tabs.some((tab) => tab.path === action.path)) return { ...state, active: action.path };
      const tab: Tab = {
        path: action.path,
        kind: action.kind,
        content: action.content,
        savedContent: action.content,
        sha: action.sha,
        language: action.language,
        status: "saved",
      };
      return { tabs: [...state.tabs, tab], active: action.path };
    }
    case "activated":
      return state.tabs.some((tab) => tab.path === action.path) ? { ...state, active: action.path } : state;
    case "edited":
      return update(state, action.path, (tab) => {
        const next = { ...tab, content: action.content };
        // While saving or in conflict the status stays: the save's answer or the user's choice decides.
        return tab.status === "saving" || tab.status === "conflict"
          ? next
          : { ...next, status: statusOf(next) };
      });
    case "saveStarted":
      return update(state, action.path, (tab) => ({
        ...tab,
        status: "saving",
        saving: action.content,
        error: undefined,
      }));
    case "saveSucceeded":
      return update(state, action.path, (tab) => {
        const next = {
          ...tab,
          savedContent: action.content,
          sha: action.sha,
          saving: undefined,
          serverSha: undefined,
        };
        return { ...next, status: statusOf(next) };
      });
    case "saveFailed":
      return update(state, action.path, (tab) => ({
        ...tab,
        status: "failed",
        saving: undefined,
        error: action.error,
      }));
    case "conflict":
      return update(state, action.path, (tab) => ({
        ...tab,
        status: "conflict",
        saving: undefined,
        serverSha: action.serverSha,
      }));
    case "reloaded":
      return update(state, action.path, (tab) => ({
        ...tab,
        content: action.content,
        savedContent: action.content,
        sha: action.sha,
        status: "saved",
        saving: undefined,
        error: undefined,
        serverSha: undefined,
      }));
    case "closed": {
      const index = state.tabs.findIndex((tab) => tab.path === action.path);
      if (index === -1) return state;
      const tabs = state.tabs.filter((tab) => tab.path !== action.path);
      if (state.active !== action.path) return { ...state, tabs };
      const neighbour = tabs[Math.min(index, tabs.length - 1)] ?? null;
      return { tabs, active: neighbour?.path ?? null };
    }
  }
}
