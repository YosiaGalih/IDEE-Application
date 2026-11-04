import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/errors";
import { getFile } from "@/api/workspace";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogContent } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import type { Tab } from "./editorTabs";
import { DiffSlot } from "./lazyEditor";
import { saveFailureText, workspaceErrorText } from "./workspaceErrors";

/**
 * What the server holds now: text, something that is not text (binary or too large: it can be neither
 * shown nor loaded into the editor), nothing (deleted), or unknown (the read failed).
 */
type ServerVersion =
  | { state: "loading" }
  | { state: "text"; content: string; sha: string }
  | { state: "notText"; sha: string }
  | { state: "deleted" }
  | { state: "failed"; error: unknown };

type Props = {
  projectId: string;
  tab: Tab;
  onClose: () => void;
  /** Take the server's text: the user's text is discarded. */
  onReload: (content: string, sha: string) => void;
  /** Write the user's text over the version just read (`null`: the file was deleted, recreate it). */
  onOverwrite: (serverSha: string | null) => void;
};

/** A 409 on save (FR-029): never resolved silently; the user picks, after seeing the server's version. */
export function ConflictDialog({ projectId, tab, onClose, onReload, onOverwrite }: Props) {
  const { t } = useTranslation();
  const [server, setServer] = useState<ServerVersion>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [showDiff, setShowDiff] = useState(false);
  const [confirmReload, setConfirmReload] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    getFile(projectId, tab.path, controller.signal).then(
      (file) =>
        setServer(
          file.content === null || file.too_large || file.encoding === "binary"
            ? { state: "notText", sha: file.sha256 }
            : { state: "text", content: file.content, sha: file.sha256 },
        ),
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setServer(
          error instanceof ApiError && error.status === 404
            ? { state: "deleted" }
            : { state: "failed", error },
        );
      },
    );
    return () => controller.abort();
  }, [projectId, tab.path, attempt]);

  // One write at a time: a second click while the first is on its way would conflict with itself.
  const isSaving = tab.status === "saving";
  const failure = server.state === "failed" ? workspaceErrorText(t, server.error) : null;
  return (
    <Dialog open onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent
        title={t("workspace.conflict.title")}
        description={t("workspace.conflict.body", { path: tab.path })}
      >
        {server.state === "loading" ? (
          <p role="status" className="flex items-center gap-2 text-sm text-fg-muted">
            <Spinner />
            {t("workspace.conflict.loading")}
          </p>
        ) : null}
        {failure ? (
          <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-status-danger">
            {t("workspace.conflict.loadFailed")} {failure.text}
            {failure.requestId ? (
              <span className="font-mono text-xs">{t("common.requestId", { id: failure.requestId })}</span>
            ) : null}
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setServer({ state: "loading" });
                setAttempt((count) => count + 1);
              }}
            >
              {t("workspace.conflict.retry")}
            </Button>
          </div>
        ) : null}
        {server.state === "deleted" ? (
          <p className="text-sm text-fg">{t("workspace.conflict.deletedOnServer")}</p>
        ) : null}
        {server.state === "notText" ? (
          <p className="text-sm text-fg">{t("workspace.conflict.notText")}</p>
        ) : null}
        {showDiff && server.state === "text" ? (
          <DiffSlot
            original={server.content}
            modified={tab.content}
            language={tab.language}
            ariaLabel={t("workspace.conflict.diffLabel")}
          />
        ) : null}
        {confirmReload ? (
          <p className="text-sm text-status-warning">{t("workspace.conflict.reloadConfirm")}</p>
        ) : null}
        {tab.status === "failed" ? (
          <p role="alert" className="text-sm text-status-danger">
            {saveFailureText(t, tab.error)}
            {tab.error?.requestId ? (
              <span className="ml-1 font-mono text-xs">
                {t("common.requestId", { id: tab.error.requestId })}
              </span>
            ) : null}
          </p>
        ) : null}
        {isSaving ? (
          <p role="status" className="flex items-center gap-2 text-sm text-fg-muted">
            <Spinner />
            {t("workspace.editor.status.saving")}
          </p>
        ) : null}
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t("workspace.conflict.cancel")}
          </Button>
          {server.state === "text" ? (
            <>
              <Button variant="secondary" onClick={() => setShowDiff((shown) => !shown)}>
                {t(showDiff ? "workspace.conflict.hideDiff" : "workspace.conflict.diff")}
              </Button>
              <Button
                variant="secondary"
                disabled={isSaving}
                onClick={() =>
                  confirmReload ? onReload(server.content, server.sha) : setConfirmReload(true)
                }
              >
                {t("workspace.conflict.reload")}
              </Button>
            </>
          ) : null}
          {server.state === "text" || server.state === "notText" ? (
            <Button disabled={isSaving} onClick={() => onOverwrite(server.sha)}>
              {t("workspace.conflict.overwrite")}
            </Button>
          ) : null}
          {server.state === "deleted" ? (
            <Button disabled={isSaving} onClick={() => onOverwrite(null)}>
              {t("workspace.conflict.overwrite")}
            </Button>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
