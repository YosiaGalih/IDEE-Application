/**
 * Which listed changes go into the next commit (spec WP-2.2b, Lead call 5). Pure, so the rule is testable:
 * - the list the user first sees is ticked, and so are files that appear while the Git view is hidden (the
 *   user sees them ticked when it opens);
 * - a file that appears while the user is looking stays unticked and is named (`fresh`), so a file written
 *   meanwhile (by an agent, a save, a tool) never slips into a commit unseen;
 * - the user's choice stays while a file stays listed, and a path that is not listed is never committed.
 */
export type Selection = {
  seen: ReadonlySet<string>;
  picked: ReadonlySet<string>;
  /** Appeared while the view was shown and left unticked; cleared by the user's next choice. */
  fresh: ReadonlySet<string>;
};

export const emptySelection: Selection = { seen: new Set(), picked: new Set(), fresh: new Set() };

export function reconcileSelection(
  state: Selection,
  listed: string[],
  { tickNew }: { tickNew: boolean },
): Selection {
  const isNew = (path: string) => !state.seen.has(path);
  const picked = listed.filter((path) => state.picked.has(path) || (tickNew && isNew(path)));
  const fresh = listed.filter((path) => state.fresh.has(path) || (!tickNew && isNew(path)));
  return { seen: new Set(listed), picked: new Set(picked), fresh: new Set(fresh) };
}

export function toggle(state: Selection, path: string): Selection {
  const picked = new Set(state.picked);
  if (picked.has(path)) picked.delete(path);
  else picked.add(path);
  return { ...state, picked, fresh: new Set() };
}

export function setAll(state: Selection, listed: string[], on: boolean): Selection {
  return { ...state, picked: new Set(on ? listed : []), fresh: new Set() };
}

/** The paths to commit, in list order, and only those listed now. */
export const pickedPaths = (state: Selection, listed: string[]) =>
  listed.filter((path) => state.picked.has(path));
