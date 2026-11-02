import { FileArchive, FilePlus2, LayoutTemplate } from "lucide-react";
import { useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";
import { ApiError } from "@/api/errors";
import { useCreateProject, useTemplates } from "@/api/hooks/useProjects";
import type { CreateProjectInput, Project } from "@/api/projects";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { FormField } from "@/components/ui/FormField";
import { Label } from "@/components/ui/Label";
import { Spinner } from "@/components/ui/Spinner";
import { StateView } from "@/components/ui/StateView";
import { describeError, isOutcomeUnknown } from "@/lib/describeError";
import { cn } from "@/lib/cn";
import { PROJECTS_PATH, workspacePath } from "@/lib/paths";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { zodResolver } from "@/lib/zodResolver";
import { ArchiveImport, ArchiveInput } from "./ArchiveImport";
import { isProjectErrorKey, newProjectSchema, SERVER_FIELD_ERRORS, type NewProjectValues } from "./schemas";
import { useArchiveImport } from "./useArchiveImport";

const SOURCES = ["blank", "template", "archive"] as const;
const SOURCE_TEXT = {
  blank: { icon: FilePlus2, label: "projects.new.blank", hint: "projects.new.blankHint" },
  template: { icon: LayoutTemplate, label: "projects.new.template", hint: "projects.new.templateHint" },
  archive: { icon: FileArchive, label: "projects.new.archive", hint: "projects.new.archiveHint" },
} as const;

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
  const navigate = useNavigate();
  const templates = useTemplates();
  const create = useCreateProject();
  // An archive project: created first, then its archive is uploaded into it (WP-2.1b).
  const [created, setCreated] = useState<Project | null>(null);
  useDocumentTitle(t(created ? "screens.importArchive" : "screens.newProject"));
  // Replace: Back from the workspace must not land on a form that would create a second project.
  const openWorkspace = (project: Project) => void navigate(workspacePath(project.id), { replace: true });
  const importer = useArchiveImport({ onReady: openWorkspace });
  // One key per request body: kept only while the answer is unknown, so a retry is answered once.
  const attempt = useRef<{ key: string; body: string } | null>(null);
  const {
    register,
    handleSubmit,
    control,
    setError,
    setValue,
    formState: { errors, submitCount },
  } = useForm<NewProjectValues>({
    resolver: zodResolver(newProjectSchema),
    defaultValues: { name: "", description: "", source: "blank", templateId: "", archive: null },
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
          : values.source === "archive"
            ? { type: "upload" }
            : { type: "blank" },
    };
    const archive = values.source === "archive" ? values.archive : null;
    const body = JSON.stringify(input);
    if (attempt.current?.body !== body) attempt.current = { key: crypto.randomUUID(), body };
    create.mutate(
      { input, idempotencyKey: attempt.current.key },
      {
        onSuccess: (project) => {
          if (archive === null) {
            openWorkspace(project);
            return;
          }
          setCreated(project);
          importer.send(project.id, archive); // from this event, not an effect: never twice
        },
        onError: (error) => {
          if (!isOutcomeUnknown(error)) attempt.current = null; // answered: a new attempt gets a new key
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
    ? serverFieldErrors(create.error).length > 0
      ? // A retry with the same input fails again: point at the fields, not at "try again".
        {
          title: t("projects.new.serverFieldSummary"),
          body: null,
          requestId: create.error instanceof ApiError ? create.error.requestId : null,
        }
      : describeError(create.error, t, { unknownOutcome: t("projects.new.outcomeUnknown") })
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
    } else if (templates.data.length === 0) {
      templateChoices = <p className="text-sm text-fg-muted">{t("projects.new.noTemplates")}</p>;
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

  if (created !== null) {
    return (
      <div className="mx-auto max-w-2xl space-y-5">
        <div>
          <h1 className="text-2xl font-semibold text-fg" data-testid="screen-title">
            {t("projects.import.title")}
          </h1>
          <p className="mt-1 text-sm text-fg-muted">{created.name}</p>
        </div>
        <ArchiveImport projectId={created.id} importer={importer} />
      </div>
    );
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
          <div className="grid gap-2 sm:grid-cols-3">
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
                  {...register("source", {
                    // A file chosen earlier must not be sent after the user left the archive option.
                    onChange: (event: React.ChangeEvent<HTMLInputElement>) => {
                      if (event.target.value !== "archive") setValue("archive", null);
                    },
                  })}
                />
                <SourceText kind={kind} />
              </label>
            ))}
          </div>
        </fieldset>
        {templateChoices}
        {source === "archive" ? (
          <ArchiveInput
            id="new-project-archive"
            error={fieldError(errors.archive?.message) ?? null}
            onChoose={(file) => setValue("archive", file, { shouldValidate: submitCount > 0 })}
          />
        ) : null}
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

function SourceText({ kind }: { kind: (typeof SOURCES)[number] }) {
  const { t } = useTranslation();
  const { icon: Icon, label, hint } = SOURCE_TEXT[kind];
  return (
    <span>
      <span className="flex items-center gap-1.5 text-sm font-medium text-fg">
        <Icon aria-hidden="true" className="size-4" />
        {t(label)}
      </span>
      <span className="block text-xs text-fg-muted">{t(hint)}</span>
    </span>
  );
}
