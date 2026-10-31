import { useMutation, useQueryClient } from "@tanstack/react-query";
import { logout } from "../auth";

/** Signs out and drops every cached query, so the next user of this tab sees nothing of the last one. */
export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: logout,
    onSettled: () => queryClient.clear(),
  });
}
