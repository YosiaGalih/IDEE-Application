import {
  Ban,
  CheckCircle2,
  CircleHelp,
  CirclePause,
  Clock,
  Hourglass,
  ListChecks,
  Loader2,
  PencilLine,
  RotateCcw,
  ShieldCheck,
  ThumbsDown,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TaskStatus } from "@/api/taskStatus";
import { Badge } from "./Badge";

/** Long-running action states (PRD §4.2). */
export type ActionStatus =
  "queued" | "running" | "waiting_approval" | "validating" | "completed" | "failed" | "cancelled";

const LOOK: Record<
  ActionStatus | TaskStatus,
  { icon: LucideIcon; tone: "success" | "info" | "warning" | "danger" | "neutral" }
> = {
  draft: { icon: PencilLine, tone: "neutral" },
  needs_clarification: { icon: CircleHelp, tone: "warning" },
  planned: { icon: ListChecks, tone: "neutral" },
  needs_changes: { icon: RotateCcw, tone: "warning" },
  interrupted: { icon: CirclePause, tone: "warning" },
  rejected: { icon: ThumbsDown, tone: "neutral" },
  queued: { icon: Clock, tone: "neutral" },
  running: { icon: Loader2, tone: "info" },
  waiting_approval: { icon: Hourglass, tone: "warning" },
  validating: { icon: ShieldCheck, tone: "info" },
  completed: { icon: CheckCircle2, tone: "success" },
  failed: { icon: XCircle, tone: "danger" },
  cancelled: { icon: Ban, tone: "neutral" },
};

type Props = { status: ActionStatus | TaskStatus };

/** Status as icon + text, never colour alone (frontend.md). */
export function StatusPill({ status }: Props) {
  const { t } = useTranslation();
  const { icon: Icon, tone } = LOOK[status];
  return (
    <Badge tone={tone}>
      <Icon aria-hidden="true" className={status === "running" ? "size-3.5 animate-spin" : "size-3.5"} />
      {t(`status.${status}`)}
    </Badge>
  );
}
