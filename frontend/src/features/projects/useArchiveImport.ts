import { useEffect, useEffectEvent, useRef, useState } from "react";
import { useProject, useUploadArchive } from "@/api/hooks/useProjects";
import type { Project } from "@/api/projects";
import { isOutcomeUnknown } from "@/lib/describeError";

export type ArchiveImporter = ReturnType<typeof useArchiveImport>;

/**
 * Upload an archive into an upload project, then follow its import until it ends. Started from an event
 * (`send`), never from an effect, so a re-render can never upload twice.
 */
export function useArchiveImport({ onReady }: { onReady: (project: Project) => void }) {
  const upload = useUploadArchive();
  const [projectId, setProjectId] = useState<string | null>(null);
  const [percent, setPercent] = useState(0);
  const [accepted, setAccepted] = useState(false);
  const [tries, setTries] = useState(0);
  // One key per project and file: kept only while the answer is unknown, so a retry is imported once.
  const attempt = useRef<{ key: string; projectId: string; file: File } | null>(null);
  const project = useProject(projectId ?? "", {
    pollWhileImporting: accepted,
    enabled: accepted && projectId !== null,
  });
  const repository = project.data?.repository;
  const ready = accepted && repository?.sync_state === "ready" && project.data !== undefined;
  const importFailed = accepted && repository?.sync_state === "error" ? (repository.error ?? "") : null;

  const send = (target: string, file: File) => {
    if (attempt.current?.file !== file || attempt.current.projectId !== target) {
      attempt.current = { key: crypto.randomUUID(), projectId: target, file };
    }
    setProjectId(target);
    setPercent(0);
    setAccepted(false);
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
          setAccepted(true);
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

  return {
    send,
    tries,
    percent,
    uploading: upload.isPending,
    uploadError: upload.isError ? upload.error : null,
    importing: accepted && !ready && importFailed === null,
    pollError: project.isError ? project.error : null,
    retryPoll: () => void project.refetch(),
    importFailed,
    ready,
  };
}
