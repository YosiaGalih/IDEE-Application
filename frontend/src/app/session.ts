import type { DataRouter } from "react-router";
import { isPublicPath, loginPath } from "@/lib/safeNext";

/**
 * The global 401 handler: back to /login with the current page as `next`. Never from a public auth page,
 * where a 401 is expected (review WP-1.4a: onUnauthenticated must know the public routes).
 */
export function redirectToLogin(router: DataRouter): void {
  const { pathname, search, hash } = router.state.location;
  if (isPublicPath(pathname)) return;
  void router.navigate(loginPath({ pathname, search, hash }), { replace: true });
}
