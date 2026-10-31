import { Bot, CheckSquare, FolderKanban, LayoutDashboard, Settings, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { NavLink } from "react-router";
import type id from "@/i18n/locales/id.json";
import { cn } from "@/lib/cn";

type Item = { to: string; labelKey: `nav.${keyof (typeof id)["nav"]}`; icon: LucideIcon; end?: boolean };

const WORK: Item[] = [
  { to: "/", labelKey: "nav.dashboard", icon: LayoutDashboard, end: true },
  { to: "/projects", labelKey: "nav.projects", icon: FolderKanban },
  { to: "/agents", labelKey: "nav.agents", icon: Bot },
  { to: "/approvals", labelKey: "nav.approvals", icon: CheckSquare },
];
const SETTINGS: Item[] = [{ to: "/settings/providers", labelKey: "nav.settings", icon: Settings }];

function NavItems({ items }: { items: Item[] }) {
  const { t } = useTranslation();
  return (
    <ul className="space-y-0.5">
      {items.map(({ to, labelKey, icon: Icon, end }) => (
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
