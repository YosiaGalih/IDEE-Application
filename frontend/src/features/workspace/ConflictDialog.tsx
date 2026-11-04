import { Suspense, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/errors";
import { getFile, type FileContent } from "@/api/workspace";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogContent } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { StateView } from "@/components/ui/StateView";
import type { Tab } from "./editorTabs";
import { LazyDiffView } from "./lazyEditor";

/** What the server holds now: a file, nothing (deleted), or unknown (the read failed). */
type ServerVersion =
  { state: "loading" } | { state: "file"; file: FileContent } | { state: "deleted" } | { state: "failed" };

type Props = {
  projectId: string;
  tab: Tab;
  onClose: () => void;
  /** Take the server's version: the user's text is discarded. */
  onReload: (file: FileContent) => void;
  /** Write the user's text over the version just read (`null`: the file was deleted, recreate it). */
  onOverwrite: (serverSha: string | null) => void;
};

/** A 409 on save (FR-029): never resolved silently; the user picks, after seeing the server's version. */
export function ConflictDialog({ projectId, tab, onClose, onReload, onOverwrite }: Props) {
  const { t } = useTranslation();
  const [server, setServer] = useState<ServerVersion>({ state: "loading" });
  const [showDiff, setShowDiff] = useState(false);
  const [confirmReload, setConfirmReload] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    getFile(projectId, tab.path, controller.signal).then(
      (file) => setServer({ state: "file", file }),
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setServer(
          error instanceof ApiError && error.status === 404 ? { state: "deleted" } : { state: "failed" },
        );
      },
    );
    return () => controller.abort();
  }, [projectId, tab.path]);

  const serverText = server.state === "file" ? (server.file.content ?? "") : "";
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
        {server.state === "failed" ? (
          <p role="alert" className="text-sm text-status-danger">
            {t("workspace.conflict.loadFailed")}
          </p>
        ) : null}
        {server.state === "deleted" ? (
          <p className="text-sm text-fg">{t("workspace.conflict.deletedOnServer")}</p>
        ) : null}
        {showDiff && server.state === "file" ? (
          <Suspense fallback={<StateView state="loading" />}>
            <LazyDiffView
              original={serverText}
              modified={tab.content}
              language={tab.language}
              ariaLabel={t("workspace.conflict.diffLabel")}
            />
          </Suspense>
        ) : null}
        {confirmReload ? (
          <p className="text-sm text-status-warning">{t("workspace.conflict.reloadConfirm")}</p>
        ) : null}
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t("workspace.conflict.cancel")}
          </Button>
          {server.state === "file" ? (
            <>
              <Button variant="secondary" onClick={() => setShowDiff((shown) => !shown)}>
                {t(showDiff ? "workspace.conflict.hideDiff" : "workspace.conflict.diff")}
              </Button>
              <Button
                variant="secondary"
                onClick={() => (confirmReload ? onReload(server.file) : setConfirmReload(true))}
              >
                {t("workspace.conflict.reload")}
              </Button>
              <Button onClick={() => onOverwrite(server.file.sha256)}>
                {t("workspace.conflict.overwrite")}
              </Button>
            </>
          ) : null}
          {server.state === "deleted" ? (
            <Button onClick={() => onOverwrite(null)}>{t("workspace.conflict.overwrite")}</Button>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
