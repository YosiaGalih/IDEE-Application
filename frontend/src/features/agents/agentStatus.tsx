import {
  Ban,
  CircleCheck,
  CircleDashed,
  CircleOff,
  CircleX,
  Hand,
  Hourglass,
  Loader,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import type { Agent, AgentRunStatus, AgentStatus } from "@/api/agents";
import { Badge } from "@/components/ui/Badge";

const LOOK: Record<AgentStatus, { icon: LucideIcon; tone: "success" | "warning" | "neutral" | "info" }> = {
  ready: { icon: CircleCheck, tone: "success" },
  not_ready: { icon: Hourglass, tone: "warning" },
  disabled: { icon: CircleOff, tone: "neutral" },
  planned: { icon: CircleDashed, tone: "info" },
};

/** Status as text + icon (never colour alone, PRD §4.2). */
export function AgentStatusBadge({ status }: { status: AgentStatus }) {
  const { t } = useTranslation();
  const { icon: Icon, tone } = LOOK[status];
  return (
    <Badge tone={tone}>
      <Icon aria-hidden="true" data-testid="status-icon" className="size-3.5" />
      {t(`agents.status.${status}`)}
    </Badge>
  );
}

/** "Not ready: runtime not built yet, …, 3 tools not available yet"; empty when the status needs no reason. */
export function reasonsText(
  t: TFunction,
  agent: Pick<Agent, "status_reasons" | "unavailable_tools">,
): string {
  const parts = agent.status_reasons.map((reason) =>
    reason === "tools_unavailable"
      ? t("agents.reasons.tools_unavailable", { count: agent.unavailable_tools.length })
      : t(`agents.reasons.${reason}`),
  );
  return parts.length > 0 ? t("agents.reasons.lead", { reasons: parts.join(", ") }) : "";
}

const RUN_LOOK: Record<
  AgentRunStatus,
  { icon: LucideIcon; tone: "success" | "warning" | "neutral" | "info" | "danger" }
> = {
  running: { icon: Loader, tone: "info" },
  succeeded: { icon: CircleCheck, tone: "success" },
  failed: { icon: CircleX, tone: "danger" },
  cancelled: { icon: Ban, tone: "neutral" },
  budget_exceeded: { icon: Wallet, tone: "warning" },
  approval_required: { icon: Hand, tone: "warning" },
};

/** A run's status as text + icon (frontend.md: never colour or text alone). */
export function RunStatusBadge({ status }: { status: AgentRunStatus }) {
  const { t } = useTranslation();
  const { icon: Icon, tone } = RUN_LOOK[status];
  return (
    <Badge tone={tone}>
      <Icon aria-hidden="true" data-testid="run-status-icon" className="size-3.5" />
      {t(`agents.runStatus.${status}`)}
    </Badge>
  );
}
