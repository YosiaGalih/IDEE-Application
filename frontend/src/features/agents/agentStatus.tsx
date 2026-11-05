import { CircleDashed, CircleOff, CircleCheck, Hourglass, type LucideIcon } from "lucide-react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import type { Agent, AgentStatus } from "@/api/agents";
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
