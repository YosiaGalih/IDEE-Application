/**
 * Money from the API: a plain decimal string, never a float (API_CONTRACTS usage, spec WP-3.1f Keputusan
 * Lead #4, #13). Formatting works on the digits of the string, so no digit is lost to a float.
 */

/** A plain, non-negative decimal: no sign, no exponent, no thousands separator. */
export const DECIMAL_STRING = /^\d+(\.\d+)?$/;

/** True for a cost above 0 and below 0.01: currency formatting would round it to a zero that is not one. */
export function isBelowCent(value: string): boolean {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return false;
  const whole = match[1] ?? "";
  const fraction = match[2] ?? "";
  if (/[1-9]/.test(whole)) return false;
  return (fraction + "00").slice(0, 2) === "00" && /[1-9]/.test(fraction);
}

/**
 * `value` rounded UP to the cent, as a plain decimal string with two places ("1.001" -> "1.01"). Integer
 * arithmetic on the digits: no float, and no digit lost however large the amount.
 */
export function ceilToCent(value: string): string {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) throw new RangeError("not a plain decimal string");
  const fraction = match[2] ?? "";
  let cents = BigInt(match[1] ?? "0") * 100n + BigInt(fraction.slice(0, 2).padEnd(2, "0"));
  if (/[1-9]/.test(fraction.slice(2))) cents += 1n;
  return `${cents / 100n}.${(cents % 100n).toString().padStart(2, "0")}`;
}

/**
 * The amount in US dollars, formatted for `language`. The display rounds UP to the cent (1.001 shows as
 * US$1,01), so a cost is never shown smaller than the one recorded; below a cent, callers show "< US$0,01"
 * (`isBelowCent`) instead.
 */
export function formatUsd(value: string, language: string): string {
  return new Intl.NumberFormat(language, { style: "currency", currency: "USD" }).format(
    ceilToCent(value) as `${number}`,
  );
}
