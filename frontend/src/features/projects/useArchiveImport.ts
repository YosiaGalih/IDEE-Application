import { useEffect, useEffectEvent, useRef, useState } from "react";
import { useProject, useUploadArchive } from "@/api/hooks/useProjects";
import type { Project } from "@/api/projects";
import { isOutcomeUnknown } from "@/lib/describeError";

export type ArchiveImporter = ReturnType<typeof useArchiveImport>;

/** After this long an import is still followed, but the user is told they may leave. */
export const SLOW_IMPORT_MS = 5 * 60_000;

/** The same file as far as a retry is concerned: a picker returns a new `File` object for the same file. */
const identity = (projectId: string, file: File) =>
  `${projectId}|${file.name}|${file.size}|${file.lastModified}`;

/**
 * Upload an archive into an upload project, then follow its import until it ends. Started from an event
 * (`send`, `retry`), never from an effect, so a re-render can never upload twice.
 */
export function useArchiveImport({ onReady }: { onReady: (project: Project) => void }) {
  const upload = useUploadArchive();
  const [projectId, setProjectId] = useState<string | null>(null);
  const [lastFile, setLastFile] = useState<File | null>(null);
  const [percent, setPercent] = useState(0);
  const [acceptedAt, setAcceptedAt] = useState<number | null>(null);
  const [tries, setTries] = useState(0);
  // One key per project and file: kept only while the answer is unknown, so a retry is imported once.
  const attempt = useRef<{ key: string; identity: string } | null>(null);
  const accepted = acceptedAt !== null;
  const project = useProject(projectId ?? "", {
    pollWhileImporting: accepted,
    enabled: accepted && projectId !== null,
  });
  const repository = project.data?.repository;
  const ready = accepted && repository?.sync_state === "ready" && project.data !== undefined;
  // Anything else (importing, indexing, no repository yet) is still running: useProject keeps polling it.
  const importFailed = accepted && repository?.sync_state === "error" ? (repository.error ?? "") : null;

  const send = (target: string, file: File) => {
    const id = identity(target, file);
    if (attempt.current?.identity !== id) attempt.current = { key: crypto.randomUUID(), identity: id };
    setProjectId(target);
    setLastFile(file);
    setPercent(0);
    setAcceptedAt(null);
    setTries((count) => count + 1);
    upload.mutate(
      {
        projectId: target,
        file,
        idempotencyKey: attempt.current.key,
        onProgress: (sent, total) => setPercent(total > 0 ? Math.floor((sent / total) * 100) : 0),
      },
      {
        onSuccess: () => {
          attempt.current = null;
          setAcceptedAt(Date.now());
        },
        onError: (error) => {
          if (!isOutcomeUnknown(error)) attempt.current = null;
        },
      },
    );
  };

  const finish = useEffectEvent((done: Project) => onReady(done));
  useEffect(() => {
    if (ready && project.data) finish(project.data);
  }, [ready, project.data]);

  const outcomeUnknown = upload.isError && isOutcomeUnknown(upload.error);
  return {
    send,
    /** Send the last file again with its key: only offered while the last answer is unknown. */
    retry: () => {
      if (projectId !== null && lastFile !== null) send(projectId, lastFile);
    },
    tries,
    percent,
    lastFile: outcomeUnknown ? lastFile : null,
    uploading: upload.isPending,
    uploadError: upload.isError ? upload.error : null,
    outcomeUnknown,
    importing: accepted && !ready && importFailed === null,
    slow: accepted && project.dataUpdatedAt - acceptedAt > SLOW_IMPORT_MS,
    pollError: project.isError ? project.error : null,
    retryPoll: () => void project.refetch(),
    importFailed,
    ready,
  };
}
