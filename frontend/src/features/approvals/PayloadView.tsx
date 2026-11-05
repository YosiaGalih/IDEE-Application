import { useTranslation } from "react-i18next";

type Props = { payload: Record<string, unknown> };

/**
 * The payload exactly as the server holds it, as formatted JSON text (spec 3.1c2 Lead 3). React renders it as
 * text, so markup inside a value stays text; long values wrap instead of widening the page.
 */
export function PayloadView({ payload }: Props) {
  const { t } = useTranslation();
  return (
    <figure className="space-y-1">
      <figcaption className="text-xs text-fg-muted">{t("approvals.card.payload")}</figcaption>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-[var(--radius-control)] bg-surface-muted p-2 font-mono text-xs text-fg">
        {JSON.stringify(payload, null, 2)}
      </pre>
    </figure>
  );
}
