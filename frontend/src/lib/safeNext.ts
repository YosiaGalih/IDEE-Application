/** The public auth pages (ARCHITECTURE §3.1b), the one place their paths are written. */
export const AUTH_PATHS = {
  login: "/login",
  forgotPassword: "/forgot-password",
  resetPassword: "/reset-password",
} as const;

/** Pages a sign-in must never return to (they would loop or reuse a spent token). */
const PUBLIC_PREFIXES: readonly string[] = Object.values(AUTH_PATHS);

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
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
