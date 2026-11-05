import { useQuery } from "@tanstack/react-query";
import { getBranches, getGitDiff, getGitLog } from "../git";
import { getGitStatus, getTree, searchProject, type SearchParams } from "../workspace";

export const workspaceKey = (projectId: string) => ["workspace", projectId] as const;
export const treeKey = (projectId: string, path: string) =>
  [...workspaceKey(projectId), "tree", path] as const;
export const gitStatusKey = (projectId: string) => [...workspaceKey(projectId), "git-status"] as const;
export const gitLogKey = (projectId: string) => [...workspaceKey(projectId), "git-log"] as const;
export const branchesKey = (projectId: string) => [...workspaceKey(projectId), "git-branches"] as const;

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

/**
 * A search runs only when asked (a submitted form), never per keystroke; a refusal is not retried.
 * `attempt` counts submissions: the same query submitted again asks the server again (files change).
 */
export function useSearch(projectId: string, params: SearchParams | null, attempt = 0) {
  return useQuery({
    queryKey: [...workspaceKey(projectId), "search", params, attempt],
    queryFn: ({ signal }) => searchProject(projectId, params as SearchParams, signal),
    enabled: params !== null && params.q.trim() !== "",
    retry: false,
  });
}

/** Read again every time a diff is opened: it shows the working tree as it is now, never a cached one. */
export function useGitDiff(projectId: string, path: string | null) {
  return useQuery({
    queryKey: [...workspaceKey(projectId), "git-diff", path],
    queryFn: ({ signal }) => getGitDiff(projectId, path, signal),
    staleTime: 0,
    gcTime: 0,
  });
}

export function useBranches(projectId: string, enabled = true) {
  return useQuery({
    queryKey: branchesKey(projectId),
    queryFn: ({ signal }) => getBranches(projectId, signal),
    enabled,
  });
}

export function useGitLog(projectId: string, limit: number, enabled = true) {
  return useQuery({
    queryKey: [...gitLogKey(projectId), limit],
    queryFn: ({ signal }) => getGitLog(projectId, limit, signal),
    enabled,
  });
}
