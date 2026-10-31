import { Construction } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/ui/Card";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

export type ScreenKey =
  | "login"
  | "dashboard"
  | "projects"
  | "newProject"
  | "workspace"
  | "agents"
  | "tasks"
  | "taskDetail"
  | "preview"
  | "testing"
  | "review"
  | "acceptance"
  | "approvals"
  | "deployments"
  | "knowledge"
  | "providers"
  | "usage"
  | "users"
  | "policies"
  | "audit";

type Props = { screen: ScreenKey };

/** Stands in for a screen until WP-1.4b and later work packages build it. */
export function PlaceholderPage({ screen }: Props) {
  const { t } = useTranslation();
  const title = t(`screens.${screen}`);
  useDocumentTitle(title);
  return (
    <Card className="mx-auto max-w-2xl">
      <div className="flex items-start gap-3">
        <Construction aria-hidden="true" className="mt-0.5 size-5 text-brand-600" />
        <div>
          <h1 className="text-lg font-semibold text-fg" data-testid="screen-title">
            {title}
          </h1>
          <p className="mt-1 text-sm text-fg-muted">{t("placeholder.body")}</p>
        </div>
      </div>
    </Card>
  );
}
