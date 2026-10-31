import { z } from "zod";
import { apiRequest } from "./client";

/** The public User (API_CONTRACTS §2, backend `UserOut`): never a hash, counter or token. */
export const UserSchema = z.object({
  id: z.guid(),
  email: z.string(),
  display_name: z.string(),
  status: z.string(),
  created_at: z.iso.datetime({ offset: true }),
  last_login_at: z.iso.datetime({ offset: true }).nullable(),
});
export type User = z.infer<typeof UserSchema>;

export const MeSchema = z.object({
  user: UserSchema,
  roles: z.array(z.object({ role: z.string(), scope: z.string() })),
  permissions: z.array(z.string()),
});
export type Me = z.infer<typeof MeSchema>;

const LoginResultSchema = z.object({ user: UserSchema });
const EmptySchema = z.object({});

export type LoginInput = { email: string; password: string; rememberMe: boolean };

/** Also the first GET of a visit: its response carries the pre-session CSRF cookie. */
export async function fetchMe(signal?: AbortSignal): Promise<Me> {
  return (await apiRequest("/auth/me", { schema: MeSchema, ...(signal ? { signal } : {}) })).data;
}

export async function login(input: LoginInput): Promise<User> {
  const body = { email: input.email, password: input.password, remember_me: input.rememberMe };
  return (await apiRequest("/auth/login", { method: "POST", body, schema: LoginResultSchema })).data.user;
}

export async function logout(): Promise<void> {
  await apiRequest("/auth/logout", { method: "POST", schema: EmptySchema });
}
