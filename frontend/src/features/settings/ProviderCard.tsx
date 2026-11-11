import { PowerOff } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { usePatchProvider, useReadiness } from "@/api/hooks/useProviders";
import type { Provider } from "@/api/providers";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/Dialog";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { describeError } from "@/lib/describeError";
import { ModelPolicyForm } from "./ModelPolicyForm";
import { EnabledBadge, KeyBadge, useDateTimeFormat } from "./providerStatus";
import { QueryFallback } from "./QueryFallback";
import { ReadinessPanel, VerdictLine } from "./ReadinessPanel";
import { SmokeTest } from "./SmokeTest";

type Props = { provider: Provider };

/** One provider row (API §9): what the worker reported about its key, the endpoint, models and readiness. */
export function ProviderCard({ provider }: Props) {
  const { t } = useTranslation();
  const toggle = usePatchProvider(provider.id);
  const readiness = useReadiness(provider.id);
  const dateTime = useDateTimeFormat();
  const [isConfirming, setIsConfirming] = useState(false);
  // Above the form, which is re-keyed by the saved policy: the message survives the list read.
  const [isPolicySaved, setIsPolicySaved] = useState(false);

  const handleToggle = () => {
    if (provider.enabled) setIsConfirming(true);
    else toggle.mutate({ enabled: true });
  };
  const handleDisable = () => {
    toggle.mutate({ enabled: false }, { onSettled: () => setIsConfirming(false) });
  };

  return (
    <Card className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="text-xl font-semibold capitalize">{provider.provider}</h2>
          <VerdictLine verdict={provider.readiness_effective} />
        </div>
        <div className="flex flex-col items-end gap-2">
          <EnabledBadge isEnabled={provider.enabled} />
          <Button
            variant={provider.enabled ? "danger" : "secondary"}
            size="sm"
            disabled={toggle.isPending}
            onClick={handleToggle}
          >
            {provider.enabled ? t("providers.enabled.disable") : t("providers.enabled.enable")}
          </Button>
        </div>
      </div>
      <p className="text-sm text-fg-muted">{t("providers.enabled.help")}</p>
      {toggle.isError && !isConfirming ? (
        <ErrorSummary text={describeError(toggle.error, t)} focusKey={toggle.submittedAt} />
      ) : null}

      <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="font-medium">{t("providers.key.title")}</dt>
          <dd className="mt-1 space-y-1">
            <KeyBadge status={provider.secret_status} />
            {provider.secret_hint ? (
              <p className="font-mono text-xs">{t("providers.key.hint", { hint: provider.secret_hint })}</p>
            ) : null}
            {provider.secret_checked_at ? (
              <p className="text-xs text-fg-muted">
                {t("providers.key.checkedAt", {
                  when: dateTime.format(new Date(provider.secret_checked_at)),
                })}
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

      <div className="space-y-3">
        <ModelPolicyForm
          key={JSON.stringify(provider.model_policy)}
          provider={provider}
          onSavedChange={setIsPolicySaved}
        />
        {isPolicySaved ? <Alert tone="success">{t("providers.models.saved")}</Alert> : null}
      </div>

      {readiness.data === undefined ? (
        <QueryFallback query={readiness} />
      ) : (
        <ReadinessPanel readiness={readiness.data} />
      )}

      <SmokeTest providerId={provider.id} />

      {isConfirming ? (
        // Not while the request is in flight: closing would drop its answer.
        <Dialog open onOpenChange={(open) => (open || toggle.isPending ? undefined : setIsConfirming(false))}>
          <DialogContent
            title={t("providers.enabled.confirmTitle", { provider: provider.provider })}
            description={t("providers.enabled.confirmBody")}
          >
            {toggle.isError ? (
              <div className="mb-4">
                <ErrorSummary text={describeError(toggle.error, t)} focusKey={toggle.submittedAt} />
              </div>
            ) : null}
            <div className="flex justify-end gap-2">
              <DialogClose asChild>
                <Button variant="secondary" disabled={toggle.isPending}>
                  {t("providers.enabled.cancel")}
                </Button>
              </DialogClose>
              <Button
                variant="danger"
                disabled={toggle.isPending}
                aria-busy={toggle.isPending}
                onClick={handleDisable}
              >
                <PowerOff aria-hidden="true" className="size-4" />
                {t("providers.enabled.confirm")}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      ) : null}
    </Card>
  );
}
