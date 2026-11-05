import { Archive, CheckCircle2, Hourglass, Trash2, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import type { ProjectStatus } from "@/api/projects";
import { Badge } from "@/components/ui/Badge";
import { APPROVALS_PATH } from "@/lib/paths";

const LOOK: Record<ProjectStatus, { icon: LucideIcon; tone: "success" | "neutral" | "warning" | "danger" }> =
  {
    active: { icon: CheckCircle2, tone: "success" },
    archived: { icon: Archive, tone: "neutral" },
    deleting_pending_approval: { icon: Hourglass, tone: "warning" },
    deleted: { icon: Trash2, tone: "danger" },
  };

type Props = { status: ProjectStatus };

/**
 * Project status as icon + text, never colour alone (frontend.md). A project waiting for its delete approval
 * links to /approvals, where that decision is made (spec WP-3.1c2 Lead 7).
 */
export function ProjectStatusBadge({ status }: Props) {
  const { t } = useTranslation();
  const { icon: Icon, tone } = LOOK[status];
  const badge = (
    <Badge tone={tone}>
      <Icon aria-hidden="true" className="size-3.5" />
      {t(`projects.status.${status}`)}
    </Badge>
  );
  return status === "deleting_pending_approval" ? (
    <Link to={APPROVALS_PATH} className="rounded-full hover:underline">
      {badge}
    </Link>
  ) : (
    badge
  );
}
