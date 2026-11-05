/**
 * Which listed changes go into the next commit (spec WP-2.2b, Lead call 5). Pure, so the rule is testable:
 * a file is ticked when it first appears in the list, the user's choice stays while it stays listed, and a
 * path that is not listed is never committed.
 */
export type Selection = { seen: ReadonlySet<string>; picked: ReadonlySet<string> };

export const emptySelection: Selection = { seen: new Set(), picked: new Set() };

export function reconcileSelection(state: Selection, listed: string[]): Selection {
  const picked = listed.filter((path) => !state.seen.has(path) || state.picked.has(path));
  return { seen: new Set(listed), picked: new Set(picked) };
}

export function toggle(state: Selection, path: string): Selection {
  const picked = new Set(state.picked);
  if (picked.has(path)) picked.delete(path);
  else picked.add(path);
  return { ...state, picked };
}

export function setAll(state: Selection, listed: string[], on: boolean): Selection {
  return { ...state, picked: new Set(on ? listed : []) };
}

/** The paths to commit, in list order, and only those listed now. */
export const pickedPaths = (state: Selection, listed: string[]) =>
  listed.filter((path) => state.picked.has(path));
