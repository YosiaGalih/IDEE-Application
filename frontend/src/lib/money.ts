/**
 * Money from the API: a plain decimal string, never a float (API_CONTRACTS usage, spec WP-3.1f Keputusan
 * Lead #4, #13). Formatting passes the string itself to `Intl.NumberFormat`, which keeps every digit.
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

/** The amount in US dollars, formatted for `language` from the decimal string (no float rounding). */
export function formatUsd(value: string, language: string): string {
  return new Intl.NumberFormat(language, { style: "currency", currency: "USD" }).format(value as `${number}`);
}
