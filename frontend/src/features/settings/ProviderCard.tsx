import { useTranslation } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/api/errors";
import { usePatchProvider, useReadiness } from "@/api/hooks/useProviders";
import type { Provider } from "@/api/providers";
import { uxStateForError } from "@/api/uxState";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { StateView } from "@/components/ui/StateView";
import { describeError } from "@/lib/describeError";
import { ModelPolicyForm } from "./ModelPolicyForm";
import { ReadinessPanel, VerdictLine } from "./ReadinessPanel";
import { SmokeTest } from "./SmokeTest";

const KEY_TONE = { configured: "success", missing: "danger", unknown: "warning" } as const;

/** One provider row (API §9): what the worker reported about its key, the endpoint, models and readiness. */
export function ProviderCard({ provider }: { provider: Provider }) {
  const { t } = useTranslation();
  const toggle = usePatchProvider(provider.id);
  const readiness = useReadiness(provider.id);
  const when = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" });

  return (
    <Card className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="text-xl font-semibold capitalize">{provider.provider}</h2>
          <VerdictLine verdict={provider.readiness_effective} />
        </div>
        <div className="flex flex-col items-end gap-2">
          <Badge tone={provider.enabled ? "success" : "neutral"}>
            {provider.enabled ? t("providers.enabled.on") : t("providers.enabled.off")}
          </Badge>
          <Button
            variant={provider.enabled ? "danger" : "secondary"}
            size="sm"
            disabled={toggle.isPending}
            onClick={() => toggle.mutate({ enabled: !provider.enabled })}
          >
            {provider.enabled ? t("providers.enabled.disable") : t("providers.enabled.enable")}
          </Button>
        </div>
      </div>
      <p className="text-sm text-fg-muted">{t("providers.enabled.help")}</p>
      {toggle.isError ? (
        <ErrorSummary text={describeError(toggle.error, t)} focusKey={toggle.submittedAt} />
      ) : null}

      <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="font-medium">{t("providers.key.title")}</dt>
          <dd className="mt-1 space-y-1">
            <Badge tone={KEY_TONE[provider.secret_status]}>
              {t(`providers.key.${provider.secret_status}`)}
            </Badge>
            {provider.secret_hint ? (
              <p className="font-mono text-xs">{t("providers.key.hint", { hint: provider.secret_hint })}</p>
            ) : null}
            {provider.secret_checked_at ? (
              <p className="text-xs text-fg-muted">
                {t("providers.key.checkedAt", { when: when.format(new Date(provider.secret_checked_at)) })}
              </p>
            ) : null}
            <p className="text-xs text-fg-muted">{t("providers.key.managedInEnv")}</p>
          </dd>
        </div>
        <div>
          <dt className="font-medium">{t("providers.endpoint")}</dt>
          <dd className="mt-1 break-all font-mono text-xs">
            {provider.base_url ?? t("providers.endpointDefault")}
          </dd>
        </div>
        <div>
          <dt className="font-medium">{t("providers.mode")}</dt>
          <dd className="mt-1 font-mono text-xs">{provider.llm_provider_mode ?? t("providers.none")}</dd>
        </div>
        <div>
          <dt className="font-medium">{t("providers.pricing")}</dt>
          <dd className="mt-1 font-mono text-xs">{provider.pricing_version ?? t("providers.none")}</dd>
        </div>
      </dl>

      <ModelPolicyForm key={JSON.stringify(provider.model_policy)} provider={provider} />

      {readiness.isPending ? (
        <StateView state="loading" />
      ) : readiness.data === undefined ? (
        <StateView
          state={uxStateForError(readiness.error)}
          requestId={readiness.error instanceof ApiError ? readiness.error.requestId : null}
          onAction={() => void readiness.refetch()}
        />
      ) : (
        <ReadinessPanel readiness={readiness.data} />
      )}

      <SmokeTest providerId={provider.id} />
    </Card>
  );
}
