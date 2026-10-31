/** Cookie names from API_CONTRACTS §2 in one place: conflict #29 may move them to `__Host-` names. */
export const SESSION_COOKIE = "kk_session";
export const CSRF_COOKIE = "kk_csrf";

/** Reads a cookie now; the CSRF cookie rotates at login, so callers must never cache the value. */
export function readCookie(name: string): string | null {
  for (const part of document.cookie.split(";")) {
    const index = part.indexOf("=");
    if (index < 0) continue;
    if (part.slice(0, index).trim() === name) {
      const value = part.slice(index + 1).trim();
      try {
        return decodeURIComponent(value);
      } catch {
        return value;
      }
    }
  }
  return null;
}
