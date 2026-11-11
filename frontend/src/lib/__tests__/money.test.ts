import { describe, expect, it } from "vitest";
import { DECIMAL_STRING, formatUsd, isBelowCent } from "@/lib/money";

describe("money", () => {
  it("formats a decimal string without float rounding", () => {
    // As a float this number is 12345678901234568: the string keeps every digit.
    expect(formatUsd("12345678901234567.25", "en")).toBe("$12,345,678,901,234,567.25");
    expect(formatUsd("12345678901234567.25", "id")).toBe("US$12.345.678.901.234.567,25");
    expect(formatUsd("0.5", "id")).toBe("US$0,50");
  });

  it("rounds up to the cent: a shown cost is never smaller than the recorded one", () => {
    expect(formatUsd("1.001", "id")).toBe("US$1,01");
    expect(formatUsd("0.0149", "id")).toBe("US$0,02");
    expect(formatUsd("2.999", "id")).toBe("US$3,00");
    expect(formatUsd("999.9951", "en")).toBe("$1,000.00");
    // Exact cents, and zeros past them, stay as they are.
    expect(formatUsd("0.01", "id")).toBe("US$0,01");
    expect(formatUsd("1.0000000", "id")).toBe("US$1,00");
    expect(formatUsd("0", "id")).toBe("US$0,00");
  });

  it("tells a cost below a cent", () => {
    expect(isBelowCent("0.0000005")).toBe(true);
    expect(isBelowCent("0.0099999999")).toBe(true);
    expect(isBelowCent("0")).toBe(false); // a counted zero is a zero, not "less than a cent"
    expect(isBelowCent("0.000")).toBe(false);
    expect(isBelowCent("0.01")).toBe(false);
    expect(isBelowCent("2.5")).toBe(false);
    expect(isBelowCent("10.001")).toBe(false);
  });

  it("matches only plain decimal strings", () => {
    for (const ok of ["0", "2", "2.5", "0.0000005"]) expect(DECIMAL_STRING.test(ok)).toBe(true);
    for (const bad of ["5E-7", "-1", ".5", "1.", "1,5", "", " 1"])
      expect(DECIMAL_STRING.test(bad)).toBe(false);
  });
});
