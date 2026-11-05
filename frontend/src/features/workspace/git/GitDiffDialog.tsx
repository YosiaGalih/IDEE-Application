import { useTranslation } from "react-i18next";
import { useGitDiff } from "@/api/hooks/useWorkspace";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogContent } from "@/components/ui/Dialog";
import { StateView } from "@/components/ui/StateView";
import { workspaceErrorText } from "../workspaceErrors";
import { GitDiffView } from "./GitDiffView";

type Props = { projectId: string; path: string | null; onClose: () => void };

/** HEAD against the working tree, read when the dialog opens: one file, or all of them (`path` null). */
export function GitDiffDialog({ projectId, path, onClose }: Props) {
  const { t } = useTranslation();
  const diff = useGitDiff(projectId, path);
  const failure = diff.isError ? workspaceErrorText(t, diff.error) : null;
  return (
    <Dialog open onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent
        title={
          path === null
            ? t("workspace.gitPanel.diff.titleAll")
            : t("workspace.gitPanel.diff.titleOf", { path })
        }
        className="max-h-[85vh] w-[min(64rem,calc(100vw-2rem))] overflow-y-auto"
      >
        {diff.isPending ? <StateView state="loading" /> : null}
        {failure ? (
          <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-status-danger">
            {t(diff.data ? "workspace.gitPanel.diff.stale" : "workspace.gitPanel.diff.loadFailed")}{" "}
            {failure.text}
            {failure.requestId ? (
              <span className="font-mono text-xs">{t("common.requestId", { id: failure.requestId })}</span>
            ) : null}
            <Button size="sm" variant="secondary" onClick={() => void diff.refetch()}>
              {t("workspace.retry")}
            </Button>
          </div>
        ) : null}
        {diff.data ? <GitDiffView diff={diff.data} /> : null}
        <div className="mt-4 flex justify-end">
          <Button variant="ghost" onClick={onClose}>
            {t("workspace.gitPanel.diff.close")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
