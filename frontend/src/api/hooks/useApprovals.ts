import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { onActivityResumed, whileUserActive } from "@/lib/userActivity";
import { decideApproval, listApprovals, type ApprovalFilter, type DecisionInput } from "../approvals";
import { deleteProject } from "../projects";
import { projectsKey } from "./useProjects";

export const approvalsKey = ["approvals"] as const;
const listKey = (filter: ApprovalFilter) => [...approvalsKey, "list", filter] as const;
const pendingCountKey = [...approvalsKey, "pending-count"] as const;

/**
 * No approval event stream exists for approvals without a task (spec WP-3.1c Non-goals): poll, but only while
 * someone uses the page, so an unattended screen does not keep the session alive (AG-13 WP-3.1c2 M1).
 */
export const PENDING_POLL_MS = 30_000;

/** One page of approvals; a new filter or page keeps the old rows on screen while it loads. */
export function useApprovals(filter: ApprovalFilter) {
  return useQuery({
    queryKey: listKey(filter),
    queryFn: ({ signal }) => listApprovals(filter, signal),
    placeholderData: keepPreviousData,
    refetchInterval: filter.status === "pending" ? whileUserActive(PENDING_POLL_MS) : false,
  });
}

/** How many approvals the user can decide now (the navigation badge, spec 3.1c2 Lead 6). */
export function usePendingApprovalCount() {
  const queryClient = useQueryClient();
  // The badge is on every shell screen: when someone comes back after an idle stop, every approval list
  // catches up at once instead of waiting for the next focus or navigation.
  useEffect(
    () => onActivityResumed(() => void queryClient.invalidateQueries({ queryKey: approvalsKey })),
    [queryClient],
  );
  return useQuery({
    queryKey: pendingCountKey,
    queryFn: async ({ signal }) =>
      (await listApprovals({ status: "pending", page: 1, pageSize: 1 }, signal)).meta.total,
    refetchInterval: whileUserActive(PENDING_POLL_MS),
  });
}

/**
 * A decision. Settled, not only success: after a 409 or a lost answer the lists must show what the server
 * really has (the project may now be deleted or back).
 */
export function useDecideApproval() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ approvalId, input }: { approvalId: string; input: DecisionInput }) =>
      decideApproval(approvalId, input),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: approvalsKey });
      void queryClient.invalidateQueries({ queryKey: projectsKey });
    },
  });
}

/** Asks for a project delete; it creates (or returns) the `project_delete` approval. */
export function useRequestProjectDelete() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId }: { projectId: string }) => deleteProject(projectId),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: projectsKey });
      void queryClient.invalidateQueries({ queryKey: approvalsKey });
    },
  });
}
