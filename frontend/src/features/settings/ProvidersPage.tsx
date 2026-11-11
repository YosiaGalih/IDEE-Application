import { useId } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/errors";
import { useProviders } from "@/api/hooks/useProviders";
import { uxStateForError } from "@/api/uxState";
import { Card } from "@/components/ui/Card";
import { StateView } from "@/components/ui/StateView";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { ProviderCard } from "./ProviderCard";

/**
 * `/settings/providers` (spec WP-3.1e Goals 3.1e2): provider status as the worker reported it, models, the
 * readiness gate and the smoke test, with the data flow statement (SECURITY_MODEL §7, FR-158). The key is
 * never set or shown here (ADR-004, ADR-073).
 */
export function ProvidersPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("screens.providers"));
  const dataFlowId = useId();
  const providers = useProviders();

  let body: React.ReactNode;
  if (providers.isPending) {
    body = <StateView state="loading" />;
  } else if (providers.data === undefined) {
    body = (
      <StateView
        state={uxStateForError(providers.error)}
        requestId={providers.error instanceof ApiError ? providers.error.requestId : null}
        onAction={() => void providers.refetch()}
      />
    );
  } else if (providers.data.length === 0) {
    body = <StateView state="empty" title={t("providers.empty")} />;
  } else {
    body = (
      <div className="space-y-5">
        {providers.data.map((provider) => (
          <ProviderCard key={provider.id} provider={provider} />
        ))}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <header>
        <h1 data-testid="screen-title" tabIndex={-1} className="text-2xl font-semibold">
          {t("screens.providers")}
        </h1>
        <p className="mt-1 text-sm text-fg-muted">{t("providers.lead")}</p>
      </header>
      <Card aria-labelledby={dataFlowId} className="space-y-2">
        <h2 id={dataFlowId} className="font-semibold">
          {t("providers.dataFlowTitle")}
        </h2>
        <p className="text-sm">{t("providers.dataFlow")}</p>
      </Card>
      {body}
    </div>
  );
}
