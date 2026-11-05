import { useQueryClient } from "@tanstack/react-query";
import { CircleDot, GitBranch, GitCommitHorizontal } from "lucide-react";
import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/errors";
import { commitChanges } from "@/api/git";
import { branchesKey, gitLogKey, gitStatusKey, useGitStatus } from "@/api/hooks/useWorkspace";
import type { GitStatus } from "@/api/workspace";
import { Button } from "@/components/ui/Button";
import { Label } from "@/components/ui/Label";
import { Spinner } from "@/components/ui/Spinner";
import { StateView } from "@/components/ui/StateView";
import { isOutcomeUnknown } from "@/lib/describeError";
import { reasonOf, workspaceErrorText } from "../workspaceErrors";
import {
  emptySelection,
  pickedPaths,
  reconcileSelection,
  setAll,
  toggle,
  type Selection,
} from "./commitSelection";
import { GitBranches } from "./GitBranches";
import { GitDiffDialog } from "./GitDiffDialog";
import { GitLog } from "./GitLog";
import { useRetryKey } from "./retryKey";

const MESSAGE_MAX = 5000; // the API's limits (routes/git.py NewCommit)
const PATHS_MAX = 500;

type Props = {
  projectId: string;
  canRead: boolean;
  canCommit: boolean;
  canBranch: boolean;
  /** Paths of editor tabs with unsaved text: a commit takes what the server holds, not those. */
  unsavedPaths: string[];
  /** Shown now. Branches and the log are read only once the view has been shown. */
  isActive?: boolean;
};

/** The workspace's git view (FR-113, FR-115): what changed, its diff, a commit, branches and the log. */
export function GitPanel({ canRead, ...rest }: Props) {
  if (!canRead) return <StateView state="permission_denied" />;
  return <GitPanelBody {...rest} />;
}

function GitPanelBody({
  projectId,
  canCommit,
  canBranch,
  unsavedPaths,
  isActive = true,
}: Omit<Props, "canRead">) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  // Shared with the explorer's markers; branches and the log wait until the view is first opened.
  const status = useGitStatus(projectId);
  const [wasShown, setWasShown] = useState(isActive);
  if (isActive && !wasShown) setWasShown(true);
  useEffect(() => {
    // Opening the view reads the status again: the list must not be one read before files changed.
    if (isActive) void queryClient.invalidateQueries({ queryKey: gitStatusKey(projectId) });
  }, [isActive, projectId, queryClient]);
  // undefined: no dialog; null: the whole working tree; a path: that file.
  const [diffPath, setDiffPath] = useState<string | null | undefined>(undefined);
  const failure = status.isError ? workspaceErrorText(t, status.error) : null;
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto py-2 text-sm">
      {status.isPending ? (
        <p role="status" className="flex items-center gap-2 px-3 text-fg-muted">
          <Spinner />
        </p>
      ) : null}
      {failure ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 px-3 text-xs text-status-danger">
          {t(status.data ? "workspace.gitPanel.statusStale" : "workspace.gitPanel.statusLoadFailed")}{" "}
          {failure.text}
          {failure.requestId ? (
            <span className="font-mono">{t("common.requestId", { id: failure.requestId })}</span>
          ) : null}
          <Button variant="ghost" size="sm" className="h-6 px-1" onClick={() => void status.refetch()}>
            {t("workspace.retry")}
          </Button>
        </div>
      ) : null}
      {status.data ? (
        <Changes
          projectId={projectId}
          status={status.data}
          canCommit={canCommit}
          unsavedPaths={unsavedPaths}
          isVisible={isActive}
          onShowDiff={setDiffPath}
        />
      ) : null}
      {wasShown ? (
        <>
          <GitBranches
            projectId={projectId}
            canBranch={canBranch}
            currentBranch={status.data?.branch ?? null}
          />
          <GitLog projectId={projectId} />
        </>
      ) : null}
      {diffPath !== undefined ? (
        <GitDiffDialog projectId={projectId} path={diffPath} onClose={() => setDiffPath(undefined)} />
      ) : null}
    </div>
  );
}

type ChangesProps = {
  projectId: string;
  status: GitStatus;
  canCommit: boolean;
  unsavedPaths: string[];
  /** The Git view is shown: a file appearing now stays unticked (commitSelection). */
  isVisible: boolean;
  onShowDiff: (path: string | null) => void;
};

function Changes({ projectId, status, canCommit, unsavedPaths, isVisible, onShowDiff }: ChangesProps) {
  const { t } = useTranslation();
  const listed = useMemo(() => status.changes.map((change) => change.path), [status.changes]);
  const listedKey = listed.join("\0");
  const [selection, setSelection] = useState<Selection>(() =>
    reconcileSelection(emptySelection, listed, { tickNew: true }),
  );
  const [selectionKey, setSelectionKey] = useState(listedKey);
  if (listedKey !== selectionKey) {
    // A new list (read again, or after a save): the user's choices stay; see commitSelection for new files.
    setSelectionKey(listedKey);
    setSelection(reconcileSelection(selection, listed, { tickNew: !isVisible }));
  }
  const fresh = listed.filter((path) => selection.fresh.has(path));
  return (
    <section aria-labelledby={`${projectId}-changes`} className="flex flex-col gap-2 px-3">
      <h2 id={`${projectId}-changes`} className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
        {t("workspace.gitPanel.changes")}
      </h2>
      <p className="flex items-center gap-1 font-mono text-xs text-fg-muted">
        <GitBranch aria-hidden="true" className="size-3.5" />
        {status.branch && status.head
          ? t("workspace.gitPanel.head", { branch: status.branch, head: status.head.slice(0, 7) })
          : t("workspace.gitPanel.noCommit")}
      </p>
      {status.clean ? (
        <p className="text-fg-muted">{t("workspace.gitPanel.clean")}</p>
      ) : (
        <>
          {fresh.length > 0 ? (
            <p role="status" className="text-xs text-status-warning">
              {t("workspace.gitPanel.fresh", { count: fresh.length, paths: fresh.join(", ") })}
            </p>
          ) : null}
          <ul className="flex flex-col">
            {status.changes.map((change) => (
              <li key={change.path} className="flex items-center gap-2 py-0.5">
                {canCommit ? (
                  <input
                    type="checkbox"
                    aria-label={t("workspace.gitPanel.pickOf", { path: change.path })}
                    checked={selection.picked.has(change.path)}
                    onChange={() => setSelection((current) => toggle(current, change.path))}
                  />
                ) : null}
                <span className="inline-flex shrink-0 items-center gap-0.5 text-xs text-status-info">
                  <CircleDot aria-hidden="true" className="size-3" />
                  {t(`workspace.git.${change.status}`)}
                </span>
                <button
                  type="button"
                  aria-label={t("workspace.gitPanel.showDiffOf", { path: change.path })}
                  onClick={() => onShowDiff(change.path)}
                  className="min-w-0 truncate text-left font-mono text-xs text-fg hover:underline"
                  title={change.path}
                >
                  {change.path}
                </button>
              </li>
            ))}
          </ul>
          <Button variant="secondary" size="sm" className="self-start" onClick={() => onShowDiff(null)}>
            {t("workspace.gitPanel.showAllDiff")}
          </Button>
        </>
      )}
      {/* Mounted also when the tree is clean: the answer to a commit that took the last change stays. */}
      {canCommit ? (
        <CommitForm
          projectId={projectId}
          paths={pickedPaths(selection, listed)}
          isClean={status.clean}
          context={`${status.head ?? ""}\0${listedKey}`}
          unsavedPaths={unsavedPaths}
          onAll={(on) => setSelection((current) => setAll(current, listed, on))}
        />
      ) : null}
    </section>
  );
}

type CommitResult =
  | { kind: "done"; sha: string }
  | { kind: "nothing" }
  | { kind: "unknown" }
  | { kind: "invalid"; requestId: string | null }
  | { kind: "failed"; text: string; requestId: string | null };

type CommitFormProps = {
  projectId: string;
  paths: string[];
  isClean: boolean;
  /** The repository state the request is made against (HEAD and the listed changes): see useRetryKey. */
  context: string;
  unsavedPaths: string[];
  onAll: (on: boolean) => void;
};

function CommitForm({ projectId, paths, isClean, context, unsavedPaths, onAll }: CommitFormProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const messageId = useId();
  const retry = useRetryKey();
  const [message, setMessage] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [result, setResult] = useState<CommitResult | null>(null);
  const isTooLong = message.length > MESSAGE_MAX;
  const isTooMany = paths.length > PATHS_MAX;
  const canSubmit =
    message.trim() !== "" && !isTooLong && paths.length > 0 && !isTooMany && !isBusy && !isClean;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) return;
    const body = JSON.stringify({ message, paths });
    const key = retry.keyFor(body, context);
    setIsBusy(true);
    setResult(null);
    try {
      const { commit } = await commitChanges(projectId, { message, paths }, key);
      retry.settle(body, context, key, null);
      setResult({ kind: "done", sha: commit.slice(0, 7) });
      setMessage("");
    } catch (error) {
      retry.settle(body, context, key, error);
      if (reasonOf(error) === "nothing_to_commit") setResult({ kind: "nothing" });
      else if (isOutcomeUnknown(error)) setResult({ kind: "unknown" });
      // 422 (pydantic: message, paths, .kk) or 400 BAD_REQUEST (the adapter refused a path or the message).
      else if (error instanceof ApiError && (error.status === 422 || error.code === "BAD_REQUEST"))
        setResult({ kind: "invalid", requestId: error.requestId });
      else setResult({ kind: "failed", ...workspaceErrorText(t, error) });
    } finally {
      setIsBusy(false);
      // Whatever happened, the list, the explorer's markers and the log show what the server holds now.
      void queryClient.invalidateQueries({ queryKey: gitStatusKey(projectId) });
      void queryClient.invalidateQueries({ queryKey: gitLogKey(projectId) });
      void queryClient.invalidateQueries({ queryKey: branchesKey(projectId) });
    }
  };

  if (isClean) return <CommitResultText result={result} />;
  return (
    <form
      onSubmit={(event) => void handleSubmit(event)}
      className="flex flex-col gap-2 border-t border-line pt-2"
    >
      <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
        <span>{t("workspace.gitPanel.commit.picked", { count: paths.length })}</span>
        <Button type="button" variant="ghost" size="sm" className="h-6 px-1" onClick={() => onAll(true)}>
          {t("workspace.gitPanel.selectAll")}
        </Button>
        <Button type="button" variant="ghost" size="sm" className="h-6 px-1" onClick={() => onAll(false)}>
          {t("workspace.gitPanel.clearAll")}
        </Button>
      </div>
      <Label htmlFor={messageId}>{t("workspace.gitPanel.commit.message")}</Label>
      <textarea
        id={messageId}
        value={message}
        rows={3}
        onChange={(event) => {
          setMessage(event.target.value);
          setResult(null);
        }}
        className="rounded-[var(--radius-control)] border border-line bg-surface px-2 py-1 font-mono text-xs"
      />
      {isTooLong ? (
        <p role="alert" className="text-xs text-status-danger">
          {t("workspace.gitPanel.commit.tooLong")}
        </p>
      ) : null}
      {isTooMany ? (
        <p role="alert" className="text-xs text-status-danger">
          {t("workspace.gitPanel.commit.tooMany")}
        </p>
      ) : null}
      {unsavedPaths.length > 0 ? (
        <p role="status" className="text-xs text-status-warning">
          {t("workspace.gitPanel.commit.unsaved", {
            count: unsavedPaths.length,
            paths: unsavedPaths.join(", "),
          })}
        </p>
      ) : null}
      <Button type="submit" size="sm" disabled={!canSubmit}>
        <GitCommitHorizontal aria-hidden="true" className="size-4" />
        {t("workspace.gitPanel.commit.submit")}
      </Button>
      <CommitResultText result={result} />
    </form>
  );
}

function CommitResultText({ result }: { result: CommitResult | null }) {
  const { t } = useTranslation();
  if (result === null) return null;
  if (result.kind === "done")
    return (
      <p role="status" className="text-xs text-status-success">
        {t("workspace.gitPanel.commit.done", { sha: result.sha })}
      </p>
    );
  if (result.kind === "nothing")
    return (
      <p role="status" className="text-xs text-fg-muted">
        {t("workspace.gitPanel.commit.nothing")}
      </p>
    );
  if (result.kind === "unknown")
    return (
      <p role="alert" className="text-xs text-status-warning">
        {t("workspace.gitPanel.commit.unknownOutcome")}
      </p>
    );
  if (result.kind === "invalid")
    return (
      <p role="alert" className="text-xs text-status-danger">
        {t("workspace.gitPanel.commit.invalid")}
        {result.requestId ? (
          <span className="ml-1 font-mono">{t("common.requestId", { id: result.requestId })}</span>
        ) : null}
      </p>
    );
  return (
    <p role="alert" className="text-xs text-status-danger">
      {t("workspace.gitPanel.commit.failed")} {result.text}
      {result.requestId ? (
        <span className="ml-1 font-mono">{t("common.requestId", { id: result.requestId })}</span>
      ) : null}
    </p>
  );
}
