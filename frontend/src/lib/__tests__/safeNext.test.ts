import { describe, expect, it } from "vitest";
import { isPublicPath, loginPath, safeNext } from "@/lib/safeNext";

describe("safeNext (no open redirect, SECURITY_MODEL)", () => {
  it.each([
    "/projects",
    "/projects?tab=tasks",
    "/tasks/t1/review#evidence",
    "/settings/usage?from=2026-10-01&to=2026-10-31",
  ])("keeps the internal path %s", (next) => {
    expect(safeNext(next)).toBe(next);
  });

  it.each([
    [null],
    [undefined],
    [""],
    ["projects"],
    ["//evil.test"],
    ["//evil.test/projects"],
    ["https://evil.test"],
    ["http:/evil.test"],
    ["javascript:alert(1)"],
    ["/\\evil.test"],
    ["\\\\evil.test"],
    ["/..//evil.test"],
    ["/.//evil.test"],
    ["/%2e%2e//evil.test"],
    ["/a/../..//evil.test"],
    ["/\t/evil.test"],
    ["/\n/evil.test"],
    ["/login"],
    ["/login?next=/projects"],
    ["/./login"],
    ["/forgot-password"],
    ["/reset-password/token123"],
    ["/reset-password"],
    ["/LOGIN"],
    ["/Login?next=/x"],
    ["/%6Cogin"],
    ["/forgot-Password"],
    ["/reset-PASSWORD/x"],
    ["/%72eset-password"],
    ["/%E0%A4%A"],
  ])("refuses %j and falls back to /", (next) => {
    expect(safeNext(next)).toBe("/");
  });

  it("knows the public auth pages, and only those", () => {
    expect(isPublicPath("/login")).toBe(true);
    expect(isPublicPath("/reset-password/abc")).toBe(true);
    expect(isPublicPath("/forgot-password")).toBe(true);
    expect(isPublicPath("/loginx")).toBe(false);
    expect(isPublicPath("/projects")).toBe(false);
  });

  it("builds /login?next= from the current location, and plain /login when there is nowhere to return", () => {
    expect(loginPath({ pathname: "/projects", search: "?tab=1", hash: "#x" })).toBe(
      `/login?next=${encodeURIComponent("/projects?tab=1#x")}`,
    );
    expect(loginPath({ pathname: "/" })).toBe("/login");
    expect(loginPath({ pathname: "/login", search: "?next=/x" })).toBe("/login");
  });
});
