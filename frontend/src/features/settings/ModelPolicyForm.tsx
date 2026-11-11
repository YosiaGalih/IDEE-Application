import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { usePatchProvider } from "@/api/hooks/useProviders";
import { MODEL_ALIASES, type ModelAlias, type Provider } from "@/api/providers";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { FormField } from "@/components/ui/FormField";
import { describeError } from "@/lib/describeError";

/** backend app/services/provider_readiness.py MODEL_ID and MAX_MODEL_ID; no model id is hard-coded here. */
const MODEL_ID = /^claude-[a-z0-9]+(?:[.-][a-z0-9]+)*$/;
const MAX_MODEL_ID = 100;

type Props = {
  provider: Provider;
  /** Told when a save starts (false) and when it is stored (true); the message lives outside this form. */
  onSavedChange: (isSaved: boolean) => void;
};

/**
 * `model_policy` per alias (Lead decision 3.1e #5). Every alias is sent: an empty one as `null`, so it falls
 * back to `CLAUDE_MODEL_*`. A change puts readiness back to "needs a test" on the server (ADR-032).
 */
export function ModelPolicyForm({ provider, onSavedChange }: Props) {
  const { t } = useTranslation();
  const formId = useId();
  const patch = usePatchProvider(provider.id);
  const [values, setValues] = useState<Record<ModelAlias, string>>(
    () =>
      Object.fromEntries(MODEL_ALIASES.map((alias) => [alias, provider.model_policy[alias] ?? ""])) as Record<
        ModelAlias,
        string
      >,
  );
  const [invalidAliases, setInvalidAliases] = useState<ModelAlias[]>([]);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = Object.fromEntries(MODEL_ALIASES.map((alias) => [alias, values[alias].trim()])) as Record<
      ModelAlias,
      string
    >;
    const invalid = MODEL_ALIASES.filter(
      (alias) =>
        trimmed[alias] !== "" && (trimmed[alias].length > MAX_MODEL_ID || !MODEL_ID.test(trimmed[alias])),
    );
    setInvalidAliases(invalid);
    if (invalid.length > 0 || patch.isPending) return;
    onSavedChange(false);
    const policy = Object.fromEntries(
      MODEL_ALIASES.map((alias) => [alias, trimmed[alias] === "" ? null : trimmed[alias]]),
    ) as Record<ModelAlias, string | null>;
    patch.mutate({ model_policy: policy }, { onSuccess: () => onSavedChange(true) });
  };

  return (
    <form onSubmit={handleSubmit} noValidate className="space-y-3">
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
              error={invalidAliases.includes(alias) ? t("providers.models.invalid") : undefined}
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
      <Button type="submit" variant="secondary" disabled={patch.isPending}>
        {t("providers.models.save")}
      </Button>
    </form>
  );
}
