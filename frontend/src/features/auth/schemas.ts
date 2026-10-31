import { z } from "zod";
import { PASSWORD_MAX, PASSWORD_MIN } from "@/api/auth";
import type id from "@/i18n/locales/id.json";

/** Every message below is a key of `auth.errors`; the form translates it. */
export type AuthErrorKey = `auth.errors.${keyof (typeof id)["auth"]["errors"]}`;
const message = (key: AuthErrorKey) => key;

/** Characters as the backend counts them (Python `len`: code points), not UTF-16 units. */
const length = (value: string) => [...value].length;

/**
 * The backend's own rule (services/users.py): an "@" and at most 320 characters. Nothing stricter: an
 * owner created as admin@localhost must still be able to sign in.
 */
const email = z
  .string()
  .trim()
  .min(1, message("auth.errors.emailRequired"))
  .refine((value) => value.includes("@") && value.length <= 320, message("auth.errors.emailInvalid"));

/** An existing password: only checked for presence and the upper bound. */
const currentPassword = z
  .string()
  .min(1, message("auth.errors.passwordRequired"))
  .refine((value) => length(value) <= PASSWORD_MAX, message("auth.errors.passwordTooLong"));

/** A password being set: the backend's 12–1024 characters (validate_new_password). */
const newPassword = z
  .string()
  .refine((value) => length(value) >= PASSWORD_MIN, message("auth.errors.passwordTooShort"))
  .refine((value) => length(value) <= PASSWORD_MAX, message("auth.errors.passwordTooLong"));

const confirmed = <T extends { newPassword: string; confirmPassword: string }>(values: T) =>
  values.newPassword === values.confirmPassword;
const mismatch = { message: message("auth.errors.passwordMismatch"), path: ["confirmPassword"] };

export const loginSchema = z.object({ email, password: currentPassword, rememberMe: z.boolean() });
export type LoginValues = z.infer<typeof loginSchema>;

export const forgotSchema = z.object({ email });
export type ForgotValues = z.infer<typeof forgotSchema>;

export const resetSchema = z.object({ newPassword, confirmPassword: z.string() }).refine(confirmed, mismatch);
export type ResetValues = z.infer<typeof resetSchema>;

export const changeSchema = z
  .object({ currentPassword, newPassword, confirmPassword: z.string() })
  .refine(confirmed, mismatch);
export type ChangeValues = z.infer<typeof changeSchema>;

const KEYS: ReadonlySet<string> = new Set<AuthErrorKey>([
  "auth.errors.emailRequired",
  "auth.errors.emailInvalid",
  "auth.errors.passwordRequired",
  "auth.errors.passwordTooShort",
  "auth.errors.passwordTooLong",
  "auth.errors.passwordMismatch",
]);

/** Narrows a resolver message to a translation key; anything else is a bug and shows as a plain error. */
export const isAuthErrorKey = (value: string | undefined): value is AuthErrorKey =>
  value !== undefined && KEYS.has(value);
