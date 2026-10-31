import { useQuery } from "@tanstack/react-query";
import { fetchMe } from "../auth";
import { HANDLES_UNAUTHENTICATED } from "../queryClient";

export const meQueryKey = ["auth", "me"] as const;

/** The signed-in user. A 401 is left to the caller (the session guard redirects with `next`). */
export function useMe() {
  return useQuery({
    queryKey: meQueryKey,
    queryFn: ({ signal }) => fetchMe(signal),
    meta: HANDLES_UNAUTHENTICATED,
  });
}
