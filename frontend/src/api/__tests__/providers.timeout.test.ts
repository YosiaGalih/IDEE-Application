import { describe, expect, it, vi } from "vitest";
import { JOB_ID, PROVIDER_ID } from "@/__tests__/providerFixtures";
import type * as Client from "@/api/client";

const calls: { path: string; options: { timeoutMs?: number } }[] = [];
vi.mock("@/api/client", async (original) => ({
  ...(await original<typeof Client>()),
  apiRequest: vi.fn((path: string, options: { timeoutMs?: number }) => {
    calls.push({ path, options });
    return Promise.resolve({
      data: { job_id: JOB_ID },
      status: "accepted",
      meta: null,
      requestId: "r",
    });
  }),
}));

describe("runSmoke client limit (spec WP-3.1e2 Lead 4, mutant F2)", () => {
  it("asks the client for 60 s, above the server's own wait, not the 30 s default", async () => {
    const { runSmoke, SMOKE_TIMEOUT_MS } = await import("@/api/providers");
    await runSmoke(PROVIDER_ID);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.options.timeoutMs).toBe(SMOKE_TIMEOUT_MS);
    expect(SMOKE_TIMEOUT_MS).toBe(60_000);
  });
});
