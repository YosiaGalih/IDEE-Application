import { FileArchive, Upload } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/errors";
import { ARCHIVE_MAX_BYTES } from "@/api/projects";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { Label } from "@/components/ui/Label";
import { Spinner } from "@/components/ui/Spinner";
import { describeError, isOutcomeUnknown } from "@/lib/describeError";
import { CONFLICTS, conflictReason, importErrorText } from "./importText";
import { isArchiveName } from "./schemas";
import type { ArchiveImporter } from "./useArchiveImport";

type Props = {
  projectId: string;
  importer: ArchiveImporter;
};

/** The archive picker, the percent sent, then the import's progress and outcome (state in `useArchiveImport`). */
export function ArchiveImport({ projectId, importer }: Props) {
  const { t } = useTranslation();
  const inputId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  const handleFile = (event: React.ChangeEvent<HTMLInputElement>) => {
    const chosen = event.target.files?.[0] ?? null;
    setFile(chosen);
    if (chosen === null) setFileError(t("projects.errors.archiveRequired"));
    else if (!isArchiveName(chosen.name)) setFileError(t("projects.errors.archiveType"));
    else if (chosen.size > ARCHIVE_MAX_BYTES) setFileError(t("projects.errors.archiveTooLarge"));
    else setFileError(null);
  };
  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (file === null) setFileError(t("projects.errors.archiveRequired"));
    else if (fileError === null) importer.send(projectId, file);
  };

  if (importer.uploading) {
    return (
      <div className="space-y-2" role="status">
        <p className="text-sm text-fg">{t("projects.import.uploading", { percent: importer.percent })}</p>
        <div
          role="progressbar"
          aria-label={t("projects.import.progressLabel")}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={importer.percent}
          className="h-2 overflow-hidden rounded-full bg-surface-muted"
        >
          <div className="h-full bg-brand-600 transition-[width]" style={{ width: `${importer.percent}%` }} />
        </div>
      </div>
    );
  }
  if (importer.importing) {
    if (importer.pollError) {
      return (
        <div className="space-y-2">
          <ErrorSummary text={describeError(importer.pollError, t)} focusKey={importer.tries} />
          <Button variant="secondary" size="sm" onClick={importer.retryPoll}>
            {t("projects.import.retry")}
          </Button>
        </div>
      );
    }
    return (
      <p role="status" className="flex items-center gap-2 text-sm text-fg">
        <Spinner className="size-4" />
        {t("projects.import.importing")}
      </p>
    );
  }
  if (importer.ready) return null; // the caller moves on

  const unknown = importer.uploadError !== null && isOutcomeUnknown(importer.uploadError);
  return (
    <form noValidate onSubmit={handleSubmit} className="space-y-3">
      {importer.importFailed !== null ? (
        <Alert tone="danger">
          <p className="font-medium">{t("projects.import.failedTitle")}</p>
          <p>{importErrorText(importer.importFailed, t)}</p>
        </Alert>
      ) : null}
      {importer.uploadError !== null ? (
        <ErrorSummary text={uploadErrorText(importer.uploadError, t)} focusKey={importer.tries} />
      ) : null}
      <div className="space-y-1.5">
        <Label htmlFor={inputId}>{t("projects.new.archiveFile")}</Label>
        <input
          id={inputId}
          type="file"
          accept=".zip,.tar,.tar.gz,.tgz"
          onChange={handleFile}
          aria-invalid={fileError ? true : undefined}
          aria-describedby={`${inputId}-hint${fileError ? ` ${inputId}-error` : ""}`}
          className="block w-full text-sm text-fg file:mr-3 file:rounded-[var(--radius-control)] file:border file:border-line file:bg-surface file:px-3 file:py-1.5"
        />
        <p id={`${inputId}-hint`} className="text-xs text-fg-muted">
          {t("projects.new.archiveLimit")}
        </p>
        {fileError ? (
          <p id={`${inputId}-error`} className="text-sm text-status-danger">
            {fileError}
          </p>
        ) : null}
      </div>
      <Button type="submit" disabled={file === null || fileError !== null}>
        {importer.importFailed !== null ? (
          <FileArchive aria-hidden="true" className="size-4" />
        ) : (
          <Upload aria-hidden="true" className="size-4" />
        )}
        {unknown ? t("projects.import.retry") : t("projects.import.upload")}
      </Button>
    </form>
  );
}

function uploadErrorText(error: unknown, t: ReturnType<typeof useTranslation>["t"]) {
  if (error instanceof ApiError && error.code === "CONFLICT") {
    const reason = conflictReason(error.details);
    if (reason) return { title: t(CONFLICTS[reason]), body: null, requestId: error.requestId };
  }
  return describeError(error, t, {
    byCode: {
      UNSUPPORTED_FILE_TYPE: t("projects.errors.archiveType"),
      PAYLOAD_TOO_LARGE: t("projects.errors.archiveTooLarge"),
    },
    unknownOutcome: t("projects.import.outcomeUnknown"),
  });
}
