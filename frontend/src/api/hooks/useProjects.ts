import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createProject,
  listProjects,
  listTemplates,
  setProjectArchived,
  type CreateProjectInput,
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
