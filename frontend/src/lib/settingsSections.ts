/**
 * The settings screens and the global permission each one needs (backend app/core/rbac.py
 * GLOBAL_ONLY_PERMISSIONS; spec WP-3.1e2 Lead 2). The sidebar shows Settings to a holder of any of them and
 * the landing page lists the ones the user may open. Display only: the server refuses everyone else (403).
 */
export const SETTINGS_SECTIONS = [
  { key: "providers", path: "/settings/providers", permission: "settings:providers" },
  { key: "usage", path: "/settings/usage", permission: "usage:read_all" },
  { key: "users", path: "/settings/users", permission: "admin:users" },
  { key: "policies", path: "/settings/policies", permission: "admin:policies" },
  { key: "audit", path: "/settings/audit", permission: "audit:read" },
] as const;
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];

/** The settings screens a user with these global permissions may open, in sidebar order. */
export function settingsSectionsFor(permissions: readonly string[]): SettingsSection[] {
  return SETTINGS_SECTIONS.filter((section) => permissions.includes(section.permission));
}
