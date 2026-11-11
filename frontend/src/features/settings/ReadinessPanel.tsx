import { useId } from "react";
import { useTranslation } from "react-i18next";
import { CHECK_KEYS, type Readiness, type Verdict } from "@/api/providers";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { CheckBadge, useDateTimeFormat, VerdictBadge } from "./providerStatus";

const KNOWN_REASONS = [
  "passed",
  "readiness_not_required",
  "disabled",
  "no_report",
  "other_mode",
  "models_changed",
  "key_rotated",
  "key_missing",
  "endpoint_changed",
  "report_failed",
] as const;

function isKnown<T extends string>(list: readonly T[], value: string): value is T {
  return (list as readonly string[]).includes(value);
}

type VerdictLineProps = {
  verdict: Verdict;
  /** Announce a gate switched off as an alert; only one place on the page does (the readiness panel). */
  hasAlert?: boolean;
};

/** Whether the provider may be called now, in words: the reason the server gave, never the colour alone. */
export function VerdictLine({ verdict, hasAlert = false }: VerdictLineProps) {
  const { t } = useTranslation();
  const reason = isKnown(KNOWN_REASONS, verdict.reason)
    ? t(`providers.readiness.reason.${verdict.reason}`)
    : t("providers.readiness.reason.other", { reason: verdict.reason });
  const isGateOff = verdict.reason === "readiness_not_required";
  return (
    <div className="space-y-2">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        {/* Spec WP-3.1e2 Lead 3: the colour comes from whether the report counts now, not its own result. */}
        <VerdictBadge state={verdict.state} />
        {isGateOff && hasAlert ? null : <span>{reason}</span>}
      </p>
      {isGateOff && hasAlert ? (
        <Alert tone="warning" role="alert">
          {reason}
        </Alert>
      ) : null}
    </div>
  );
}

type Props = {
  readiness: Readiness;
  /** When the last read failed: the time the shown data was read (silent-failure 3.1e2 M1). */
  staleSince?: number | null;
  onReread?: () => void;
};

/** The last report (API §11.3) with every check by its own label; details are server text, shown as text. */
export function ReadinessPanel({ readiness, staleSince = null, onReread }: Props) {
  const { t } = useTranslation();
  const headingId = useId();
  const dateTime = useDateTimeFormat();
  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h3 id={headingId} className="text-lg font-semibold">
        {t("providers.readiness.title")}
      </h3>
      {staleSince !== null ? (
        <Alert tone="warning">
          <p>{t("providers.readiness.stale")}</p>
          <p className="mt-1">
            {t("providers.readiness.staleAt", { when: dateTime.format(new Date(staleSince)) })}
          </p>
          {onReread ? (
            <Button variant="secondary" size="sm" className="mt-2" onClick={onReread}>
              {t("providers.reread")}
            </Button>
          ) : null}
        </Alert>
      ) : null}
      <VerdictLine verdict={readiness.effective} hasAlert />
      <p className="text-sm text-fg-muted">
        {readiness.checked_at
          ? t("providers.readiness.checkedAt", {
              when: dateTime.format(new Date(readiness.checked_at)),
              mode: readiness.mode ?? t("providers.none"),
            })
          : t("providers.readiness.never")}
      </p>
      {readiness.checks.length > 0 ? (
        <>
          <p className="text-sm">
            {t(`providers.readiness.reportResult.${readiness.report_ready ? "passed" : "failed"}`)}
          </p>
          {readiness.effective.state === "unknown" ? (
            // Mode, models, key or endpoint moved since this report: its result no longer counts (L4).
            <p className="text-sm font-medium">{t("providers.readiness.outdated")}</p>
          ) : null}
          <ul className="divide-y divide-line rounded-[var(--radius-control)] border border-line">
            {readiness.checks.map((check) => (
              <li
                key={check.key}
                className="flex flex-col gap-1 px-3 py-2 sm:flex-row sm:items-start sm:gap-3"
              >
                <CheckBadge status={check.status} />
                <span className="min-w-0 break-words text-sm">
                  <span className="font-medium">
                    {isKnown(CHECK_KEYS, check.key) ? t(`providers.checks.${check.key}`) : check.key}
                  </span>
                  <span className="block text-fg-muted">{check.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
