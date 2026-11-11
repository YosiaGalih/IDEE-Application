import { useId } from "react";
import { useTranslation } from "react-i18next";
import i18n from "@/i18n";
import { CHECK_KEYS, type EffectiveState, type Readiness, type Verdict } from "@/api/providers";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";

const STATE_TONE: Record<EffectiveState, "success" | "danger" | "neutral" | "warning"> = {
  ready: "success",
  not_ready: "danger",
  disabled: "neutral",
  unknown: "warning",
};
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
const STATUS_TONE = { passed: "success", failed: "danger", skipped: "neutral" } as const;

function isKnown<T extends string>(list: readonly T[], value: string): value is T {
  return (list as readonly string[]).includes(value);
}

/** Whether the provider may be called now, in words: the reason the server gave, never the colour alone. */
export function VerdictLine({ verdict }: { verdict: Verdict }) {
  const { t } = useTranslation();
  const reason = isKnown(KNOWN_REASONS, verdict.reason)
    ? t(`providers.readiness.reason.${verdict.reason}`)
    : t("providers.readiness.reason.other", { reason: verdict.reason });
  return (
    <div className="space-y-2">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        {/* Spec WP-3.1e2 Lead 3: the colour comes from whether the report counts now, not its own result. */}
        <Badge tone={STATE_TONE[verdict.state]}>{t(`providers.readiness.state.${verdict.state}`)}</Badge>
        {verdict.reason === "readiness_not_required" ? null : <span>{reason}</span>}
      </p>
      {verdict.reason === "readiness_not_required" ? (
        <Alert tone="warning" role="alert">
          {reason}
        </Alert>
      ) : null}
    </div>
  );
}

/** The last report (API §11.3) with every check by its own label; details are server text, shown as text. */
export function ReadinessPanel({ readiness }: { readiness: Readiness }) {
  const { t } = useTranslation();
  const headingId = useId();
  const when = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" });
  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h2 id={headingId} className="text-lg font-semibold">
        {t("providers.readiness.title")}
      </h2>
      <VerdictLine verdict={readiness.effective} />
      <p className="text-sm text-fg-muted">
        {readiness.checked_at
          ? t("providers.readiness.checkedAt", {
              when: when.format(new Date(readiness.checked_at)),
              mode: readiness.mode ?? t("providers.none"),
            })
          : t("providers.readiness.never")}
      </p>
      {readiness.checks.length > 0 ? (
        <>
          <p className="text-sm">
            {t(`providers.readiness.reportResult.${readiness.report_ready ? "passed" : "failed"}`)}
          </p>
          <ul className="divide-y divide-line rounded-[var(--radius-control)] border border-line">
            {readiness.checks.map((check) => (
              <li
                key={check.key}
                className="flex flex-col gap-1 px-3 py-2 sm:flex-row sm:items-start sm:gap-3"
              >
                <Badge tone={STATUS_TONE[check.status]} className="w-fit shrink-0">
                  {t(`providers.readiness.status.${check.status}`)}
                </Badge>
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
