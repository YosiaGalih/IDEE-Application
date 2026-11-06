import { Bot, CheckSquare, FolderKanban, LayoutDashboard, Settings, type LucideIcon } from "lucide-react";
import type { ComponentType } from "react";
import { useTranslation } from "react-i18next";
import { NavLink } from "react-router";
import { usePendingApprovalCount } from "@/api/hooks/useApprovals";
import type id from "@/i18n/locales/id.json";
import { cn } from "@/lib/cn";

type Item = {
  to: string;
  labelKey: `nav.${keyof (typeof id)["nav"]}`;
  icon: LucideIcon;
  end?: boolean;
  /** Shown after the label, inside the link (so it is part of the link's name). */
  Badge?: ComponentType;
};

/**
 * How many approvals the user can decide now (spec WP-3.1c2 Lead 6). Hidden at 0 and whenever the count
 * could not be read: a wrong count is worse than none, and /approvals shows its own error.
 */
function PendingApprovalsBadge() {
  const { t } = useTranslation();
  const pending = usePendingApprovalCount();
  if (!pending.isSuccess || pending.data === 0) return null;
  return (
    <>
      <span
        aria-hidden="true"
        className="ml-auto min-w-5 rounded-full bg-brand-600 px-1.5 text-center text-xs font-semibold leading-5 text-white"
      >
        {pending.data > 99 ? "99+" : pending.data}
      </span>
      {/* The text starts with its own separator: "Persetujuan, 3 tertunda". */}
      <span className="sr-only">{t("nav.pendingCount", { count: pending.data })}</span>
    </>
  );
}

const WORK: Item[] = [
  { to: "/", labelKey: "nav.dashboard", icon: LayoutDashboard, end: true },
  { to: "/projects", labelKey: "nav.projects", icon: FolderKanban },
  { to: "/agents", labelKey: "nav.agents", icon: Bot },
  { to: "/approvals", labelKey: "nav.approvals", icon: CheckSquare, Badge: PendingApprovalsBadge },
];
const SETTINGS: Item[] = [{ to: "/settings/providers", labelKey: "nav.settings", icon: Settings }];

function NavItems({ items }: { items: Item[] }) {
  const { t } = useTranslation();
  return (
    <ul className="space-y-0.5">
      {items.map(({ to, labelKey, icon: Icon, end, Badge }) => (
        <li key={to}>
          <NavLink
            to={to}
            end={end ?? false}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-sidebar-fg hover:bg-sidebar-active",
                isActive && "bg-sidebar-active font-semibold text-white",
              )
            }
          >
            <Icon aria-hidden="true" className="size-4" />
            {t(labelKey)}
            {Badge ? <Badge /> : null}
          </NavLink>
        </li>
      ))}
    </ul>
  );
}

export function Sidebar() {
  const { t } = useTranslation();
  return (
    <aside className="on-dark flex w-60 shrink-0 flex-col bg-sidebar text-sidebar-fg">
      <div className="flex items-center gap-2 bg-sidebar-deep px-4 py-4">
        <img src="/favicon.svg" alt="" className="size-7" />
        <span className="font-semibold text-white">{t("app.name")}</span>
      </div>
      <nav aria-label={t("app.mainNav")} className="flex-1 space-y-6 px-2 py-4">
        <div>
          <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-sidebar-muted">
            {t("nav.sectionWork")}
          </p>
          <NavItems items={WORK} />
        </div>
        <div>
          <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-sidebar-muted">
            {t("nav.sectionSettings")}
          </p>
          <NavItems items={SETTINGS} />
        </div>
      </nav>
    </aside>
  );
}
