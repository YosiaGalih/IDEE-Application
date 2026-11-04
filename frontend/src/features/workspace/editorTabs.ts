/**
 * The open files of the editor (FR-027, FR-030). Pure, so the save rules are testable without Monaco:
 * a tab is "saved" only after the server accepted that exact text, and a conflict keeps the user's text.
 */
import type { RepoReason } from "./workspaceErrors";

export type TabKind = "text" | "binary" | "large";
export type TabStatus = "saved" | "dirty" | "saving" | "failed" | "conflict";

/** Why a save failed, as far as the answer tells: `unknown_outcome` means it may have been written. */
export type SaveErrorCode =
  "not_found" | "forbidden" | "unknown_outcome" | "rate_limited" | "generic" | RepoReason;
export type SaveError = { code: SaveErrorCode; requestId: string | null };

export type Tab = {
  path: string;
  /** Which opening of the file this is: an answer to a save from an earlier opening is ignored. */
  openId: number;
  kind: TabKind;
  /** What the editor shows now. */
  content: string;
  /** What the server holds at `sha` (the last read or the last accepted save). */
  savedContent: string;
  sha: string;
  language: string | null;
  /** Bytes on the server, for a binary or too large file shown as metadata. */
  size?: number | undefined;
  status: TabStatus;
  /** Bumped when the server's text replaces the editor's (a reload); the editor applies only those. */
  revision: number;
  /** CRLF and LF both occur: the editor keeps one kind, so a save changes the other lines' endings. */
  mixedEol: boolean;
  /** The text a save in flight is writing. */
  saving?: string | undefined;
  error?: SaveError | undefined;
  /** The server's sha when a save hit a conflict. */
  serverSha?: string | null | undefined;
};

export type TabsState = { tabs: Tab[]; active: string | null };
export const initialTabs: TabsState = { tabs: [], active: null };

/** The server's answers to a save carry the opening they belong to. */
type Answer = { openId: number; path: string };

export type TabsAction =
  | {
      type: "opened";
      openId: number;
      path: string;
      kind: TabKind;
      content: string;
      sha: string;
      language: string | null;
      size?: number | undefined;
    }
  | { type: "activated"; path: string }
  | { type: "edited"; path: string; content: string }
  | ({ type: "saveStarted"; content: string } & Answer)
  | ({ type: "saveSucceeded"; content: string; sha: string } & Answer)
  | ({ type: "saveFailed"; error: SaveError } & Answer)
  | ({ type: "conflict"; serverSha: string | null } & Answer)
  | { type: "reloaded"; path: string; content: string; sha: string }
  | { type: "closed"; path: string };

export const isDirty = (tab: Tab) => tab.content !== tab.savedContent;

const statusOf = (tab: Tab): TabStatus => (isDirty(tab) ? "dirty" : "saved");

const hasMixedEol = (text: string) => text.includes("\r\n") && /(^|[^\r])\n/.test(text);

function update(
  state: TabsState,
  path: string,
  change: (tab: Tab) => Tab,
  openId: number | null = null,
): TabsState {
  // An answer for a tab that is gone, or for an earlier opening of the same file, changes nothing.
  const matches = (tab: Tab) => tab.path === path && (openId === null || tab.openId === openId);
  if (!state.tabs.some(matches)) return state;
  return { ...state, tabs: state.tabs.map((tab) => (matches(tab) ? change(tab) : tab)) };
}

export function tabsReducer(state: TabsState, action: TabsAction): TabsState {
  switch (action.type) {
    case "opened": {
      if (state.tabs.some((tab) => tab.path === action.path)) return { ...state, active: action.path };
      const tab: Tab = {
        path: action.path,
        openId: action.openId,
        kind: action.kind,
        content: action.content,
        savedContent: action.content,
        sha: action.sha,
        language: action.language,
        size: action.size,
        status: "saved",
        revision: 0,
        mixedEol: hasMixedEol(action.content),
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
      return update(
        state,
        action.path,
        (tab) => ({ ...tab, status: "saving", saving: action.content, error: undefined }),
        action.openId,
      );
    case "saveSucceeded":
      return update(
        state,
        action.path,
        (tab) => {
          const next = {
            ...tab,
            savedContent: action.content,
            sha: action.sha,
            saving: undefined,
            serverSha: undefined,
          };
          return { ...next, status: statusOf(next) };
        },
        action.openId,
      );
    case "saveFailed":
      return update(
        state,
        action.path,
        (tab) => ({ ...tab, status: "failed", saving: undefined, error: action.error }),
        action.openId,
      );
    case "conflict":
      return update(
        state,
        action.path,
        (tab) => ({ ...tab, status: "conflict", saving: undefined, serverSha: action.serverSha }),
        action.openId,
      );
    case "reloaded":
      return update(state, action.path, (tab) => ({
        ...tab,
        content: action.content,
        savedContent: action.content,
        sha: action.sha,
        status: "saved",
        revision: tab.revision + 1,
        mixedEol: hasMixedEol(action.content),
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
