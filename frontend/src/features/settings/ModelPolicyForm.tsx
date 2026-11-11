import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { usePatchProvider } from "@/api/hooks/useProviders";
import { MODEL_ALIASES, type ModelAlias, type Provider } from "@/api/providers";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { FormField } from "@/components/ui/FormField";
import { describeError } from "@/lib/describeError";

/** backend provider_readiness.MODEL_ID: a Claude model id as the API names it; no id is hard-coded here. */
const MODEL_ID = /^claude-[a-z0-9]+(?:[.-][a-z0-9]+)*$/;
const MAX_MODEL_ID = 100;

/**
 * `model_policy` per alias (Lead decision 3.1e #5). Every alias is sent: an empty one as `null`, so it falls
 * back to `CLAUDE_MODEL_*`. A change puts readiness back to "not tested" on the server (ADR-032).
 */
export function ModelPolicyForm({ provider }: { provider: Provider }) {
  const { t } = useTranslation();
  const formId = useId();
  const patch = usePatchProvider(provider.id);
  const [values, setValues] = useState<Record<ModelAlias, string>>(() => ({
    default: provider.model_policy.default ?? "",
    fast: provider.model_policy.fast ?? "",
    complex: provider.model_policy.complex ?? "",
  }));
  const [invalid, setInvalid] = useState<ModelAlias[]>([]);
  const [saved, setSaved] = useState(false);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setSaved(false);
    const wrong = MODEL_ALIASES.filter((alias) => {
      const value = values[alias].trim();
      return value !== "" && (value.length > MAX_MODEL_ID || !MODEL_ID.test(value));
    });
    setInvalid(wrong);
    if (wrong.length > 0 || patch.isPending) return;
    const policy = Object.fromEntries(
      MODEL_ALIASES.map((alias) => [alias, values[alias].trim() === "" ? null : values[alias].trim()]),
    ) as Record<ModelAlias, string | null>;
    patch.mutate({ model_policy: policy }, { onSuccess: () => setSaved(true) });
  };

  return (
    <form onSubmit={submit} noValidate className="space-y-3">
      <h3 className="font-semibold">{t("providers.models.title")}</h3>
      <div className="grid gap-3 sm:grid-cols-3">
        {MODEL_ALIASES.map((alias) => (
          <div key={alias}>
            <FormField
              id={`${formId}-${alias}`}
              label={t(`providers.models.alias.${alias}`)}
              value={values[alias]}
              placeholder={t("providers.models.placeholder")}
              autoComplete="off"
              spellCheck={false}
              maxLength={MAX_MODEL_ID}
              error={invalid.includes(alias) ? t("providers.models.invalid") : undefined}
              onChange={(event) => setValues((current) => ({ ...current, [alias]: event.target.value }))}
            />
            <p className="mt-1 break-all font-mono text-xs text-fg-muted">
              {t("providers.models.effective", { model: provider.effective_models[alias] })}
            </p>
          </div>
        ))}
      </div>
      {patch.isError ? (
        <ErrorSummary text={describeError(patch.error, t)} focusKey={patch.submittedAt} />
      ) : null}
      {saved ? <Alert tone="success">{t("providers.models.saved")}</Alert> : null}
      <Button type="submit" variant="secondary" disabled={patch.isPending}>
        {t("providers.models.save")}
      </Button>
    </form>
  );
}
