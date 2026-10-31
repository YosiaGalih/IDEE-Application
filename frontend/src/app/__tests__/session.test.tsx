import { createMemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { redirectToLogin } from "@/app/session";

const router = (path: string) =>
  createMemoryRouter([{ path: "*", element: null }], { initialEntries: [path] });

describe("redirectToLogin (the global 401 handler)", () => {
  it("goes to /login with the current page as next, replacing the entry", async () => {
    const r = router("/projects/p1/tasks?status=open#t3");
    redirectToLogin(r);
    await Promise.resolve();
    expect(r.state.location.pathname).toBe("/login");
    expect(r.state.location.search).toBe(`?next=${encodeURIComponent("/projects/p1/tasks?status=open#t3")}`);
    expect(r.state.historyAction).toBe("REPLACE");
  });

  it.each(["/login?next=%2Fprojects", "/forgot-password", "/reset-password"])(
    "stays on the public page %s (a 401 there is expected, and next must survive)",
    async (path) => {
      const r = router(path);
      redirectToLogin(r);
      await Promise.resolve();
      expect(`${r.state.location.pathname}${r.state.location.search}`).toBe(path);
      expect(r.state.historyAction).toBe("POP");
    },
  );
});
