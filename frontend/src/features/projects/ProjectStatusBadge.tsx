import { Archive, CheckCircle2, Hourglass, Trash2, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ProjectStatus } from "@/api/projects";
import { Badge } from "@/components/ui/Badge";

const LOOK: Record<ProjectStatus, { icon: LucideIcon; tone: "success" | "neutral" | "warning" | "danger" }> =
  {
    active: { icon: CheckCircle2, tone: "success" },
    archived: { icon: Archive, tone: "neutral" },
    deleting_pending_approval: { icon: Hourglass, tone: "warning" },
    deleted: { icon: Trash2, tone: "danger" },
  };

type Props = { status: ProjectStatus };

/** Project status as icon + text, never colour alone (frontend.md). */
export function ProjectStatusBadge({ status }: Props) {
  const { t } = useTranslation();
  const { icon: Icon, tone } = LOOK[status];
  return (
    <Badge tone={tone}>
      <Icon aria-hidden="true" className="size-3.5" />
      {t(`projects.status.${status}`)}
    </Badge>
  );
}
