import { useQuery } from "@tanstack/react-query";
import { getAgent, getAgents } from "../agents";

export const agentsKey = ["agents"] as const;

/** The whole agent catalog (bounded); the page filters it locally. */
export function useAgents() {
  return useQuery({ queryKey: agentsKey, queryFn: ({ signal }) => getAgents(signal) });
}

/** One agent; its runs are already limited by the server to the user's projects. */
export function useAgent(agentId: string) {
  return useQuery({ queryKey: [...agentsKey, agentId], queryFn: ({ signal }) => getAgent(agentId, signal) });
}
