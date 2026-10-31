import { describe, expect, it } from "vitest";
import { isAuthErrorKey } from "@/features/auth/schemas";

describe("isAuthErrorKey", () => {
  it("accepts only the keys of auth.errors", () => {
    expect(isAuthErrorKey("auth.errors.emailRequired")).toBe(true);
    expect(isAuthErrorKey("auth.errors.passwordMismatch")).toBe(true);
    expect(isAuthErrorKey("auth.errors.nope")).toBe(false);
    expect(isAuthErrorKey("auth.login.title")).toBe(false);
    expect(isAuthErrorKey("Invalid input: expected string, received undefined")).toBe(false);
    expect(isAuthErrorKey("toString")).toBe(false);
    expect(isAuthErrorKey(undefined)).toBe(false);
  });
});
