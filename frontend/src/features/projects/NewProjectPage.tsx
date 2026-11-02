import { FilePlus2, LayoutTemplate } from "lucide-react";
import { useRef } from "react";
import { useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";
import { ApiError } from "@/api/errors";
import { useCreateProject, useTemplates } from "@/api/hooks/useProjects";
import type { CreateProjectInput } from "@/api/projects";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { FormField } from "@/components/ui/FormField";
import { Label } from "@/components/ui/Label";
import { Spinner } from "@/components/ui/Spinner";
import { StateView } from "@/components/ui/StateView";
import { describeError, isUnanswered } from "@/lib/describeError";
import { cn } from "@/lib/cn";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { zodResolver } from "@/lib/zodResolver";
import { workspacePath } from "./ProjectsPage";
import { isProjectErrorKey, newProjectSchema, SERVER_FIELD_ERRORS, type NewProjectValues } from "./schemas";

const PROJECTS_PATH = "/projects";
const SOURCES = ["blank", "template"] as const;

function serverFieldErrors(error: unknown) {
  if (!(error instanceof ApiError) || error.code !== "VALIDATION_ERROR" || !Array.isArray(error.details))
    return [];
  return error.details.flatMap((detail: unknown) => {
    if (typeof detail !== "object" || detail === null || !("loc" in detail) || !Array.isArray(detail.loc))
      return [];
    const target = SERVER_FIELD_ERRORS[String(detail.loc[1])];
    return target ? [target] : [];
  });
}

/** `/projects/new`: a blank project or one from a template (FR-013, FR-014). */
export function NewProjectPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("screens.newProject"));
  const navigate = useNavigate();
  const templates = useTemplates();
  const create = useCreateProject();
  // One key per request body: kept only while the answer is unknown, so a retry is answered once.
  const attempt = useRef<{ key: string; body: string } | null>(null);
  const {
    register,
    handleSubmit,
    control,
    setError,
    formState: { errors, submitCount },
  } = useForm<NewProjectValues>({
    resolver: zodResolver(newProjectSchema),
    defaultValues: { name: "", description: "", source: "blank", templateId: "" },
    shouldFocusError: false,
  });
  const source = useWatch({ control, name: "source" });

  const fieldError = (message: string | undefined) => {
    if (message === undefined) return undefined;
    if (isProjectErrorKey(message)) return t(message);
    console.error(`new project form: validation message is not a projects.errors key: ${message}`);
    return t("states.error.title");
  };

  const handleValid = (values: NewProjectValues) => {
    const input: CreateProjectInput = {
      name: values.name,
      description: values.description.trim() === "" ? null : values.description,
      source:
        values.source === "template"
          ? { type: "template", template_id: values.templateId }
          : { type: "blank" },
    };
    const body = JSON.stringify(input);
    if (attempt.current?.body !== body) attempt.current = { key: crypto.randomUUID(), body };
    create.mutate(
      { input, idempotencyKey: attempt.current.key },
      {
        onSuccess: (project) => void navigate(workspacePath(project.id)),
        onError: (error) => {
          if (!isUnanswered(error)) attempt.current = null; // answered: a new attempt gets a new key
          for (const target of serverFieldErrors(error)) {
            setError(target.field, { type: "server", message: target.key });
          }
        },
      },
    );
  };
  const handleFormSubmit = (event: React.FormEvent<HTMLFormElement>) =>
    void handleSubmit(handleValid, () => create.reset())(event);

  const hasFieldErrors = Object.keys(errors).length > 0;
  const summary = create.isError
    ? describeError(create.error, t, { unknownOutcome: t("projects.new.outcomeUnknown") })
    : hasFieldErrors
      ? { title: t("projects.new.errorSummary"), body: null, requestId: null }
      : null;

  let templateChoices: React.ReactNode = null;
  if (source === "template") {
    if (templates.isPending) {
      templateChoices = <StateView state="loading" className="p-3" />;
    } else if (templates.isError) {
      templateChoices = (
        <div className="space-y-2">
          <p className="text-sm text-status-danger">{t("projects.new.templatesFailed")}</p>
          <Button variant="secondary" size="sm" onClick={() => void templates.refetch()}>
            {t("projects.new.retryTemplates")}
          </Button>
        </div>
      );
    } else {
      templateChoices = (
        <fieldset
          className="space-y-2"
          aria-describedby={errors.templateId ? "new-project-template-error" : undefined}
        >
          <legend className="text-sm font-medium text-fg">{t("projects.new.templates")}</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {templates.data.map((template) => (
              <label
                key={template.id}
                htmlFor={`template-${template.id}`}
                className="flex cursor-pointer gap-3 rounded-[var(--radius-control)] border border-line p-3 has-[:checked]:border-brand-600"
              >
                <input
                  id={`template-${template.id}`}
                  type="radio"
                  value={template.id}
                  className="mt-1 size-4"
                  {...register("templateId")}
                />
                <span>
                  <span className="block text-sm font-medium text-fg">{template.name}</span>
                  <span className="block text-xs text-fg-muted">{template.description}</span>
                  <span className="mt-1 block font-mono text-xs text-fg-muted">{template.stack}</span>
                </span>
              </label>
            ))}
          </div>
          {errors.templateId ? (
            <p id="new-project-template-error" className="text-sm text-status-danger">
              {fieldError(errors.templateId.message)}
            </p>
          ) : null}
        </fieldset>
      );
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold text-fg" data-testid="screen-title">
          {t("projects.new.title")}
        </h1>
        <p className="mt-1 text-sm text-fg-muted">{t("projects.new.subtitle")}</p>
      </div>
      {summary ? <ErrorSummary text={summary} focusKey={submitCount} /> : null}
      <form noValidate onSubmit={handleFormSubmit} className="space-y-5">
        <FormField
          id="new-project-name"
          label={t("projects.new.name")}
          autoComplete="off"
          error={fieldError(errors.name?.message)}
          {...register("name")}
        />
        <div className="space-y-1.5">
          <Label htmlFor="new-project-description">{t("projects.new.description")}</Label>
          <textarea
            id="new-project-description"
            rows={3}
            className="w-full rounded-[var(--radius-control)] border border-line bg-surface px-3 py-2 text-sm text-fg aria-[invalid=true]:border-status-danger"
            aria-invalid={errors.description ? true : undefined}
            aria-describedby={errors.description ? "new-project-description-error" : undefined}
            {...register("description")}
          />
          {errors.description ? (
            <p id="new-project-description-error" className="text-sm text-status-danger">
              {fieldError(errors.description.message)}
            </p>
          ) : null}
        </div>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-fg">{t("projects.new.source")}</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {SOURCES.map((kind) => (
              <label
                key={kind}
                htmlFor={`source-${kind}`}
                className={cn(
                  "flex cursor-pointer gap-3 rounded-[var(--radius-control)] border border-line p-3",
                  source === kind && "border-brand-600",
                )}
              >
                <input
                  id={`source-${kind}`}
                  type="radio"
                  value={kind}
                  className="mt-1 size-4"
                  {...register("source")}
                />
                <span>
                  <span className="flex items-center gap-1.5 text-sm font-medium text-fg">
                    {kind === "blank" ? (
                      <FilePlus2 aria-hidden="true" className="size-4" />
                    ) : (
                      <LayoutTemplate aria-hidden="true" className="size-4" />
                    )}
                    {t(kind === "blank" ? "projects.new.blank" : "projects.new.template")}
                  </span>
                  <span className="block text-xs text-fg-muted">
                    {t(kind === "blank" ? "projects.new.blankHint" : "projects.new.templateHint")}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        {templateChoices}
        <div className="flex justify-end gap-2">
          <Button asChild variant="secondary">
            <Link to={PROJECTS_PATH}>{t("projects.new.cancel")}</Link>
          </Button>
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? <Spinner className="size-4 text-white" /> : null}
            {create.isPending ? t("projects.new.submitting") : t("projects.new.submit")}
          </Button>
        </div>
      </form>
    </div>
  );
}
