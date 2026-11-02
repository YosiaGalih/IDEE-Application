import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createProject,
  getProject,
  listProjects,
  listTemplates,
  setProjectArchived,
  uploadArchive,
  type CreateProjectInput,
  type Project,
  type ProjectListFilter,
} from "../projects";

export const projectsKey = ["projects"] as const;
const projectListsKey = [...projectsKey, "list"] as const;

/** Projects in the user's scope (the server decides what that is). */
export function useProjects(filter: ProjectListFilter) {
  return useQuery({
    queryKey: [...projectListsKey, filter],
    queryFn: ({ signal }) => listProjects(filter, signal),
    placeholderData: keepPreviousData, // a new filter keeps the old rows on screen while it loads
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: [...projectsKey, "templates"],
    queryFn: ({ signal }) => listTemplates(signal),
    staleTime: 5 * 60_000, // they change only with a release
  });
}

export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ input, idempotencyKey }: { input: CreateProjectInput; idempotencyKey: string }) =>
      createProject(input, idempotencyKey),
    // Settled, not only success: after a lost answer the list must show what the server really has. Not
    // awaited, so navigation after success never waits for a refetch.
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: projectListsKey });
    },
  });
}

export function useSetProjectArchived() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, archived }: { projectId: string; archived: boolean }) =>
      setProjectArchived(projectId, archived),
    onSettled: () => queryClient.invalidateQueries({ queryKey: projectListsKey }),
  });
}

/** How often an importing project is read again (no project event stream in the contract). */
export const IMPORT_POLL_MS = 2_000;

/** One project; while `pollWhileImporting`, read again until its import ends (ready or error). */
export function useProject(
  projectId: string,
  { pollWhileImporting = false, enabled = true }: { pollWhileImporting?: boolean; enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: [...projectsKey, "detail", projectId],
    queryFn: ({ signal }) => getProject(projectId, signal),
    enabled,
    refetchInterval: (query) =>
      pollWhileImporting && isImporting(query.state.data) ? IMPORT_POLL_MS : false,
  });
}

export const isImporting = (project: Project | undefined) => project?.repository?.sync_state === "importing";

export function useUploadArchive() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      file,
      idempotencyKey,
      onProgress,
    }: {
      projectId: string;
      file: File;
      idempotencyKey: string;
      onProgress: (sent: number, total: number) => void;
    }) => uploadArchive(projectId, file, { idempotencyKey, onProgress }),
    gcTime: 0, // holds a File: nothing to keep once settled
    onSettled: (_data, _error, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: [...projectsKey, "detail", projectId] });
      void queryClient.invalidateQueries({ queryKey: projectListsKey });
    },
  });
}
