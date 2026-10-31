import {
  AlertTriangle,
  CircleSlash,
  Clock,
  Gauge,
  Inbox,
  LockKeyhole,
  LogIn,
  PlugZap,
  PowerOff,
  SearchX,
  ServerCrash,
  SplitSquareHorizontal,
  WifiOff,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { UxState } from "@/api/uxState";
import { cn } from "@/lib/cn";
import { Button } from "./Button";
import { Spinner } from "./Spinner";

const ICONS: Record<Exclude<UxState, "loading">, LucideIcon> = {
  empty: Inbox,
  error: AlertTriangle,
  timeout: Clock,
  disconnected: WifiOff,
  permission_denied: LockKeyhole,
  partial_success: SplitSquareHorizontal,
  quota_exceeded: Gauge,
  capability_disabled: PowerOff,
  provider_degraded: ServerCrash,
  unauthenticated: LogIn,
  rate_limited: CircleSlash,
  not_found: SearchX,
};

/** States that warrant an action button when the caller gives one. */
const ACTIONABLE: ReadonlySet<UxState> = new Set([
  "error",
  "timeout",
  "disconnected",
  "provider_degraded",
  "unauthenticated",
  "rate_limited",
]);

type Props = {
  state: UxState;
  /** Overrides the default title/body (already translated by the caller). */
  title?: string;
  detail?: string;
  requestId?: string | null;
  onAction?: () => void;
  className?: string;
};

/** One view for every required screen state (PRD §4.1–§4.2): icon + text, never colour alone. */
export function StateView({ state, title, detail, requestId, onAction, className }: Props) {
  const { t } = useTranslation();
  const isProblem = state !== "loading" && state !== "empty" && state !== "partial_success";
  if (state === "loading") {
    return (
      <div
        role="status"
        aria-busy="true"
        className={cn("flex items-center gap-3 p-6 text-fg-muted", className)}
      >
        <Spinner />
        <span>{title ?? t("states.loading.title")}</span>
      </div>
    );
  }
  const Icon = ICONS[state];
  const action = ACTIONABLE.has(state) && onAction ? t(`states.${state}.action`) : null;
  return (
    <div
      role={isProblem ? "alert" : "status"}
      data-state={state}
      className={cn("flex flex-col items-center gap-3 p-8 text-center", className)}
    >
      <Icon aria-hidden="true" className={cn("size-8", isProblem ? "text-status-danger" : "text-fg-muted")} />
      <p className="text-base font-semibold text-fg">{title ?? t(`states.${state}.title`)}</p>
      <p className="max-w-md text-sm text-fg-muted">{detail ?? t(`states.${state}.body`)}</p>
      {requestId ? (
        <p className="font-mono text-xs text-fg-muted">{t("common.requestId", { id: requestId })}</p>
      ) : null}
      {action ? (
        <Button variant="secondary" onClick={onAction}>
          {state === "disconnected" ? <PlugZap aria-hidden="true" className="size-4" /> : null}
          {action}
        </Button>
      ) : null}
    </div>
  );
}
