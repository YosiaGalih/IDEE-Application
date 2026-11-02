import i18n from "@/i18n";

const UNITS = ["byte", "kilobyte", "megabyte", "gigabyte"] as const;

/** A file size for people, in the UI language: "3 byte", "1,5 MB" (binary steps, as the server's limits). */
export function formatBytes(bytes: number, locale: string = i18n.language): string {
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return new Intl.NumberFormat(locale, {
    style: "unit",
    unit: UNITS[unit],
    unitDisplay: "short",
    maximumFractionDigits: unit === 0 ? 0 : 1,
  }).format(value);
}
