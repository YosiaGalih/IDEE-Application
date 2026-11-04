import { useQuery } from "@tanstack/react-query";
import { getDashboard } from "../dashboard";

export const dashboardKey = ["dashboard"] as const;

/** The signed-in user's dashboard: counts and lists in their scope (the server decides it). */
export function useDashboard() {
  return useQuery({ queryKey: dashboardKey, queryFn: ({ signal }) => getDashboard(signal) });
}
