import { useQuery } from "@tanstack/react-query";
import { fetchMe } from "../auth";

export const meQueryKey = ["auth", "me"] as const;

export function useMe() {
  return useQuery({ queryKey: meQueryKey, queryFn: ({ signal }) => fetchMe(signal) });
}
