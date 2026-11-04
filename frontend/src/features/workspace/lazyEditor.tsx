import { lazy } from "react";
import type * as EditorModule from "./CodeEditor";

/**
 * Monaco weighs megabytes, so it stays out of the route chunk: the workspace (tree, tabs, search) shows
 * at once and the editor loads beside it. The chunk is fetched once; every editor and diff shares it.
 */
let loaded: typeof EditorModule | null = null;
const load = () =>
  import("./CodeEditor").then((module) => {
    loaded = module;
    return module;
  });

export const LazyCodeEditor = lazy(() => load().then((module) => ({ default: module.CodeEditor })));
export const LazyDiffView = lazy(() => load().then((module) => ({ default: module.DiffView })));

/** A model exists only once Monaco has loaded; a tab closed before that has nothing to free. */
export function disposeModel(projectId: string, path: string): void {
  loaded?.disposeModel(projectId, path);
}
