import {
  CircleAlert,
  CircleCheck,
  CircleHelp,
  CircleMinus,
  CircleOff,
  CircleX,
  Power,
  PowerOff,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Check, EffectiveState, Provider } from "@/api/providers";
import { Badge } from "@/components/ui/Badge";

type Tone = "success" | "danger" | "neutral" | "warning";
type Look = { icon: LucideIcon; tone: Tone };

/** One look per status: text + icon, never colour alone (frontend.md). */
const VERDICT: Record<EffectiveState, Look> = {
  ready: { icon: CircleCheck, tone: "success" },
  not_ready: { icon: CircleX, tone: "danger" },
  disabled: { icon: CircleOff, tone: "neutral" },
  unknown: { icon: CircleHelp, tone: "warning" },
};
const CHECK: Record<Check["status"], Look> = {
  passed: { icon: CircleCheck, tone: "success" },
  failed: { icon: CircleX, tone: "danger" },
  skipped: { icon: CircleMinus, tone: "neutral" },
};
const KEY: Record<Provider["secret_status"], Look> = {
  configured: { icon: CircleCheck, tone: "success" },
  missing: { icon: CircleX, tone: "danger" },
  unknown: { icon: CircleAlert, tone: "warning" },
};
const ENABLED: Record<"on" | "off", Look> = {
  on: { icon: Power, tone: "success" },
  off: { icon: PowerOff, tone: "neutral" },
};

function StatusBadge({ look, label, className }: { look: Look; label: string; className?: string }) {
  const { icon: Icon, tone } = look;
  return (
    <Badge tone={tone} className={className}>
      <Icon aria-hidden="true" data-testid="status-icon" className="size-3.5" />
      {label}
    </Badge>
  );
}

export function VerdictBadge({ state }: { state: EffectiveState }) {
  const { t } = useTranslation();
  return <StatusBadge look={VERDICT[state]} label={t(`providers.readiness.state.${state}`)} />;
}

export function CheckBadge({ status }: { status: Check["status"] }) {
  const { t } = useTranslation();
  return (
    <StatusBadge
      look={CHECK[status]}
      label={t(`providers.readiness.status.${status}`)}
      className="w-fit shrink-0"
    />
  );
}

export function KeyBadge({ status }: { status: Provider["secret_status"] }) {
  const { t } = useTranslation();
  return <StatusBadge look={KEY[status]} label={t(`providers.key.${status}`)} />;
}

export function EnabledBadge({ isEnabled }: { isEnabled: boolean }) {
  const { t } = useTranslation();
  const state = isEnabled ? "on" : "off";
  return <StatusBadge look={ENABLED[state]} label={t(`providers.enabled.${state}`)} />;
}

/** The screen's one date format (medium date, short time, in the UI language). */
export function useDateTimeFormat(): Intl.DateTimeFormat {
  const { i18n } = useTranslation();
  return new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" });
}
