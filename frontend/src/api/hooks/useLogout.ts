import { useMutation, useQueryClient } from "@tanstack/react-query";
import { logout } from "../auth";
import { ApiError } from "../errors";
import { HANDLES_UNAUTHENTICATED } from "../queryClient";

/** A 401 on logout: there was no session left to end. */
export const isAlreadySignedOut = (error: unknown) => error instanceof ApiError && error.status === 401;

/**
 * Signs out. Once the session is gone (success, or a 401) every cached query is dropped, so the next user of
 * this tab sees nothing of the last one. Any other failure leaves the session alive: the cache stays and
 * the caller must say so (SECURITY_MODEL §8: logout revokes the session).
 */
export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: logout,
    meta: HANDLES_UNAUTHENTICATED, // the caller handles the 401: plain /login, no ?next=
    onSuccess: () => queryClient.clear(),
    onError: (error) => {
      if (isAlreadySignedOut(error)) queryClient.clear();
    },
  });
}
