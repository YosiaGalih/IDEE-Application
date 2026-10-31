import { get, set, type FieldErrors, type FieldValues, type Resolver } from "react-hook-form";
import type { z } from "zod";

/**
 * React Hook Form resolver for a Zod schema (frontend.md: React Hook Form + Zod). Local instead of
 * @hookform/resolvers, whose ~25 optional peers break `npm ci` with npm 10 (spec WP-1.4b).
 * Error messages are i18n keys; the form translates them. Errors are nested the way React Hook Form reads
 * them (`errors.address.street`); an issue on the whole object goes to `errors.root`.
 */
export function zodResolver<T extends FieldValues>(schema: z.ZodType<T>): Resolver<T> {
  return (values) => {
    const result = schema.safeParse(values);
    if (result.success) return { values: result.data, errors: {} };
    const errors: Record<string, unknown> = {};
    for (const issue of result.error.issues) {
      const name = issue.path.length > 0 ? issue.path.map(String).join(".") : "root";
      if (get(errors, name) === undefined) set(errors, name, { type: issue.code, message: issue.message });
    }
    return { values: {}, errors: errors as FieldErrors<T> };
  };
}
