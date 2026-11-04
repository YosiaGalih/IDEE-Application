import { useQuery } from "@tanstack/react-query";
import { getGitStatus, getTree, searchProject, type SearchParams } from "../workspace";

export const workspaceKey = (projectId: string) => ["workspace", projectId] as const;
export const treeKey = (projectId: string, path: string) =>
  [...workspaceKey(projectId), "tree", path] as const;
export const gitStatusKey = (projectId: string) => [...workspaceKey(projectId), "git-status"] as const;

/** One folder of the tree, loaded when it is opened. */
export function useTree(projectId: string, path: string, enabled = true) {
  return useQuery({
    queryKey: treeKey(projectId, path),
    queryFn: ({ signal }) => getTree(projectId, path, signal),
    enabled,
  });
}

export function useGitStatus(projectId: string) {
  return useQuery({
    queryKey: gitStatusKey(projectId),
    queryFn: ({ signal }) => getGitStatus(projectId, signal),
  });
}

/** A search runs only when asked (a submitted form), never per keystroke; a refusal is not retried. */
export function useSearch(projectId: string, params: SearchParams | null) {
  return useQuery({
    queryKey: [...workspaceKey(projectId), "search", params],
    queryFn: ({ signal }) => searchProject(projectId, params as SearchParams, signal),
    enabled: params !== null && params.q.trim() !== "",
    retry: false,
  });
}
