/** The public auth pages (ARCHITECTURE §3.1b), the one place their paths are written. */
export const AUTH_PATHS = {
  login: "/login",
  forgotPassword: "/forgot-password",
  resetPassword: "/reset-password",
} as const;

/** Why the login page was opened, carried in the navigation state (never in the URL). */
export type LoginNotice = "passwordReset" | "sessionExpired";
const NOTICES: ReadonlySet<string> = new Set<LoginNotice>(["passwordReset", "sessionExpired"]);

export function loginNoticeState(notice: LoginNotice): { notice: LoginNotice } {
  return { notice };
}

export function readLoginNotice(state: unknown): LoginNotice | null {
  if (typeof state !== "object" || state === null || !("notice" in state)) return null;
  const { notice } = state;
  return typeof notice === "string" && NOTICES.has(notice) ? (notice as LoginNotice) : null;
}

/** Pages a sign-in must never return to (they would loop or reuse a spent token). */
const PUBLIC_PREFIXES: readonly string[] = Object.values(AUTH_PATHS);

/**
 * Whether a path shows a public auth page. The router matches case-insensitively and on the decoded path,
 * so `/LOGIN` and `/%6Cogin` count too; a path that does not decode counts as public (never a target).
 */
export function isPublicPath(pathname: string): boolean {
  let decoded: string;
  try {
    decoded = decodeURIComponent(pathname).toLowerCase();
  } catch {
    return true;
  }
  return PUBLIC_PREFIXES.some((prefix) => decoded === prefix || decoded.startsWith(`${prefix}/`));
}

/**
 * Where to go after signing in: the `next` value only when it is a path on this origin, never a URL,
 * `//host`, `/\host`, a scheme, or an auth page; "/" otherwise (no open redirect, SECURITY_MODEL).
 */
export function safeNext(next: string | null | undefined): string {
  // eslint-disable-next-line no-control-regex -- refusing control characters is the point
  if (!next || !next.startsWith("/") || next.startsWith("//") || /[\\\u0000-\u001F\u007F]/.test(next)) {
    return "/";
  }
  let url: URL;
  try {
    url = new URL(next, window.location.origin);
  } catch {
    return "/";
  }
  // Dot segments fold away when parsed: "/..//evil.test" becomes "//evil.test", a protocol-relative URL.
  if (url.origin !== window.location.origin || url.pathname.startsWith("//") || isPublicPath(url.pathname)) {
    return "/";
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

/** `/login?next=<current path>` for a page that needs a session; plain `/login` when there is nowhere to return to. */
export function loginPath(current: { pathname: string; search?: string; hash?: string }): string {
  const next = safeNext(`${current.pathname}${current.search ?? ""}${current.hash ?? ""}`);
  return next === "/" ? AUTH_PATHS.login : `${AUTH_PATHS.login}?next=${encodeURIComponent(next)}`;
}
