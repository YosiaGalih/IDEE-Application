import { useId } from "react";
import { useTranslation } from "react-i18next";
import { useProviders } from "@/api/hooks/useProviders";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { StateView } from "@/components/ui/StateView";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { ProviderCard } from "./ProviderCard";
import { QueryFallback } from "./QueryFallback";

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
  if (providers.data === undefined) {
    body = <QueryFallback query={providers} />;
  } else if (providers.data.length === 0) {
    body = <StateView state="empty" title={t("providers.empty")} />;
  } else {
    body = (
      <div className="space-y-5">
        {providers.isError ? (
          // A later read failed: the rows stay, but they are said to be possibly out of date.
          <Alert tone="warning">
            <p>{t("providers.listStale")}</p>
            <Button variant="secondary" size="sm" className="mt-2" onClick={() => void providers.refetch()}>
              {t("providers.reread")}
            </Button>
          </Alert>
        ) : null}
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
