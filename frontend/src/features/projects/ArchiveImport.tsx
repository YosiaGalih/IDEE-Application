import type { TFunction } from "i18next";
import { FileArchive, RotateCcw, Upload } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/errors";
import { ARCHIVE_MAX_BYTES, ARCHIVE_SUFFIXES } from "@/api/projects";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { Label } from "@/components/ui/Label";
import { Spinner } from "@/components/ui/Spinner";
import { describeError, type ErrorText } from "@/lib/describeError";
import { formatBytes } from "@/lib/formatBytes";
import { CONFLICTS, conflictReason, importErrorText } from "./importText";
import { isArchiveName } from "./schemas";
import type { ArchiveImporter } from "./useArchiveImport";

export const ARCHIVE_ACCEPT = ARCHIVE_SUFFIXES.join(",");

type Props = {
  projectId: string;
  importer: ArchiveImporter;
  /** Why the project's last import failed, as stored (`repository.error`), until the user tries again. */
  initialProblem?: string | null;
};

/** The archive picker, the upload progress, then the import's progress and outcome (state in `useArchiveImport`). */
export function ArchiveImport({ projectId, importer, initialProblem = null }: Props) {
  const { t } = useTranslation();
  const [picked, setPicked] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  // A new input element for each attempt: what it shows is always what would be sent.
  const [inputKey, setInputKey] = useState(0);

  const choose = (chosen: File | null) => {
    setPicked(chosen);
    if (chosen === null) setFileError(t("projects.errors.archiveRequired"));
    else if (!isArchiveName(chosen.name)) setFileError(t("projects.errors.archiveType"));
    else if (chosen.size > ARCHIVE_MAX_BYTES) setFileError(t("projects.errors.archiveTooLarge"));
    else setFileError(null);
  };
  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (picked === null) {
      setFileError(t("projects.errors.archiveRequired"));
      return;
    }
    if (fileError !== null) return;
    setPicked(null);
    setInputKey((key) => key + 1);
    importer.send(projectId, picked);
  };

  if (importer.uploading) return <Uploading percent={importer.percent} />;
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
    return <Importing slow={importer.slow} />;
  }
  if (importer.ready) return null; // the caller moves on

  const problem = importer.importFailed ?? (importer.tries === 0 ? initialProblem : null);
  return (
    <form noValidate onSubmit={handleSubmit} className="space-y-3">
      {problem !== null ? (
        <ErrorSummary
          text={{
            title: t("projects.import.failedTitle"),
            body: importErrorText(problem, t),
            requestId: null,
          }}
          focusKey={importer.tries}
        />
      ) : null}
      {importer.uploadError !== null ? (
        <ErrorSummary text={uploadErrorText(importer.uploadError, t)} focusKey={importer.tries} />
      ) : null}
      {importer.lastFile !== null ? (
        <div className="space-y-2 rounded-[var(--radius-control)] border border-line p-3">
          <p className="text-sm text-fg">
            {t("projects.import.heldFile", {
              name: importer.lastFile.name,
              size: formatBytes(importer.lastFile.size),
            })}
          </p>
          <Button type="button" variant="secondary" onClick={importer.retry}>
            <RotateCcw aria-hidden="true" className="size-4" />
            {t("projects.import.retry")}
          </Button>
        </div>
      ) : null}
      <ArchiveInput key={inputKey} error={fileError} onChoose={choose} />
      <Button type="submit" disabled={picked === null || fileError !== null}>
        {problem !== null ? (
          <FileArchive aria-hidden="true" className="size-4" />
        ) : (
          <Upload aria-hidden="true" className="size-4" />
        )}
        {t("projects.import.upload")}
      </Button>
    </form>
  );
}

/** The file input with its limits and its error, shared by the new-project form. */
export function ArchiveInput({
  error,
  onChoose,
  id,
}: {
  error: string | null;
  onChoose: (file: File | null) => void;
  id?: string;
}) {
  const { t } = useTranslation();
  const generated = useId();
  const inputId = id ?? generated;
  return (
    <div className="space-y-1.5">
      <Label htmlFor={inputId}>{t("projects.new.archiveFile")}</Label>
      <input
        id={inputId}
        type="file"
        accept={ARCHIVE_ACCEPT}
        onChange={(event) => onChoose(event.target.files?.[0] ?? null)}
        aria-invalid={error ? true : undefined}
        aria-describedby={`${inputId}-hint${error ? ` ${inputId}-error` : ""}`}
        className="block w-full text-sm text-fg file:mr-3 file:rounded-[var(--radius-control)] file:border file:border-line file:bg-surface file:px-3 file:py-1.5"
      />
      <p id={`${inputId}-hint`} className="text-xs text-fg-muted">
        {t("projects.new.archiveLimit")}
      </p>
      {error ? (
        <p id={`${inputId}-error`} className="text-sm text-status-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function Uploading({ percent }: { percent: number }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => ref.current?.focus(), []); // the submit button is gone: keep keyboard focus here
  return (
    <div ref={ref} tabIndex={-1} className="space-y-2 focus:outline-none">
      {/* Announced once; the percent lives in the progressbar, not in a live region that would speak 100 times. */}
      <p role="status" className="text-sm text-fg">
        {t("projects.import.uploadingTitle")}
      </p>
      <div
        role="progressbar"
        aria-label={t("projects.import.progressLabel")}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={t("projects.import.uploading", { percent })}
        className="h-2 overflow-hidden rounded-full bg-surface-muted"
      >
        <div className="h-full bg-brand-600 transition-[width]" style={{ width: `${percent}%` }} />
      </div>
      <p aria-hidden="true" className="text-xs text-fg-muted">
        {t("projects.import.uploading", { percent })}
      </p>
    </div>
  );
}

function Importing({ slow }: { slow: boolean }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => ref.current?.focus(), []);
  return (
    <div ref={ref} tabIndex={-1} className="space-y-2 focus:outline-none">
      <p role="status" className="flex items-center gap-2 text-sm text-fg">
        <Spinner className="size-4" />
        {t("projects.import.importing")}
      </p>
      {slow ? <p className="text-sm text-fg-muted">{t("projects.import.slow")}</p> : null}
    </div>
  );
}

// `TFunction`, not `useTranslation()["t"]`: with a union of keys the hook's type grows too deep for tsc once
// the catalogue passes ~600 keys (WP-3.1c2), the same type `importText.ts` uses.
function uploadErrorText(error: unknown, t: TFunction): ErrorText {
  if (error instanceof ApiError && error.code === "CONFLICT") {
    const reason = conflictReason(error.details);
    if (reason) return { title: t(CONFLICTS[reason]), body: null, requestId: error.requestId };
  }
  if (error instanceof ApiError && error.status === 408) {
    // The server stopped waiting for the body (stalled or cut off); it kept nothing, so a new try is safe.
    return { title: t("projects.import.stalled"), body: null, requestId: error.requestId };
  }
  if (error instanceof ApiError && error.status === 413) {
    // Also the gateway's own 413 page (no envelope): the body limit in front of the API.
    return { title: t("projects.errors.archiveTooLarge"), body: null, requestId: error.requestId };
  }
  return describeError(error, t, {
    byCode: {
      UNSUPPORTED_FILE_TYPE: t("projects.errors.archiveType"),
      PAYLOAD_TOO_LARGE: t("projects.errors.archiveTooLarge"),
    },
    unknownOutcome: t("projects.import.outcomeUnknown"),
  });
}
