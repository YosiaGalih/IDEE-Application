import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, GitBranch, PanelBottomClose, PanelBottomOpen } from "lucide-react";
import { useCallback, useEffect, useId, useReducer, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useBlocker, useParams } from "react-router";
import { ApiError } from "@/api/errors";
import { useProject } from "@/api/hooks/useProjects";
import { gitStatusKey, treeKey } from "@/api/hooks/useWorkspace";
import { uxStateForError } from "@/api/uxState";
import { createPath, deleteFile, getFile, putFile, VersionConflict, type FileContent } from "@/api/workspace";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogContent } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { StateView } from "@/components/ui/StateView";
import { cn } from "@/lib/cn";
import { describeError } from "@/lib/describeError";
import { PROJECTS_PATH } from "@/lib/paths";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { ConflictDialog } from "./ConflictDialog";
import { EditorArea } from "./EditorArea";
import { initialTabs, isDirty, tabsReducer, type TabsAction } from "./editorTabs";
import { Explorer } from "./Explorer";
import { disposeModel } from "./lazyEditor";
import { SearchPanel } from "./SearchPanel";
import type { RepoReason } from "./workspaceErrors";

const RIGHT_TABS = ["search", "chat"] as const;
/** Why a project that is not active cannot be changed (the workspace API's 409 reasons). */
const STATUS_REASON: Partial<Record<string, RepoReason>> = {
  archived: "project_archived",
  deleting_pending_approval: "project_not_active",
};

const parentOf = (path: string) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : ".");
const saveErrorCode = (error: unknown) => {
  if (!(error instanceof ApiError)) return "generic";
  if (error.status === 404) return "not_found";
  if (error.status === 403) return "forbidden";
  return error.code ?? error.kind;
};

/** `/projects/:projectId/workspace` (layar 4, FR-026–032): explorer, editor, search; chat and terminal later. */
export function WorkspacePage() {
  const { projectId = "" } = useParams();
  const { t } = useTranslation();
  const project = useProject(projectId);
  useDocumentTitle(project.data?.name ?? t("screens.workspace"));
  if (project.isPending) return <StateView state="loading" />;
  if (project.isError)
    return (
      <StateView
        state={uxStateForError(project.error)}
        titleAs="h1"
        requestId={project.error instanceof ApiError ? project.error.requestId : null}
        onAction={() => void project.refetch()}
      />
    );
  const repository = project.data.repository;
  if (repository === null || repository.sync_state !== "ready") {
    const reason = repository?.sync_state === "error" ? "repository_missing" : "repository_not_ready";
    return (
      <StateView
        state="partial_success"
        titleAs="h1"
        title={project.data.name}
        detail={t(`workspace.repo.${reason}`)}
      />
    );
  }
  return (
    <Workspace
      projectId={projectId}
      name={project.data.name}
      branch={repository.branch}
      status={project.data.status}
      canWrite={project.data.permissions.includes("workspace:write") && project.data.status === "active"}
    />
  );
}

type WorkspaceProps = { projectId: string; name: string; branch: string; status: string; canWrite: boolean };

function Workspace({ projectId, name, branch, status, canWrite }: WorkspaceProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [state, dispatchToReact] = useReducer(tabsReducer, initialTabs);
  // Handlers read the tabs from this ref, which runs the same pure reducer at dispatch time: Monaco can
  // deliver Ctrl+S before React renders the last keystroke, and the save must send what is on screen.
  const stateRef = useRef(state);
  const dispatch = useCallback((action: TabsAction) => {
    stateRef.current = tabsReducer(stateRef.current, action);
    dispatchToReact(action);
  }, []);
  const [revealLine, setRevealLine] = useState<number | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const [conflictPath, setConflictPath] = useState<string | null>(null);
  const [conflictRound, setConflictRound] = useState(0);
  const [closePath, setClosePath] = useState<string | null>(null);
  const [creating, setCreating] = useState<"file" | "dir" | null>(null);
  const [deletePath, setDeletePath] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<"search" | "chat">("search");
  const [isBottomOpen, setIsBottomOpen] = useState(true);
  const hasDirty = state.tabs.some(isDirty);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => hasDirty && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    if (!hasDirty) return undefined;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [hasDirty]);

  const refreshAround = useCallback(
    (path: string) => {
      void queryClient.invalidateQueries({ queryKey: treeKey(projectId, parentOf(path)) });
      void queryClient.invalidateQueries({ queryKey: gitStatusKey(projectId) });
    },
    [projectId, queryClient],
  );

  const openFile = useCallback(
    async (path: string, line?: number) => {
      setOpenError(null);
      if (stateRef.current.tabs.some((tab) => tab.path === path)) {
        dispatch({ type: "activated", path });
        setRevealLine(line ?? null);
        return;
      }
      try {
        const file = await getFile(projectId, path);
        const kind = file.too_large
          ? "large"
          : file.content === null || file.encoding === "binary"
            ? "binary"
            : "text";
        dispatch({
          type: "opened",
          path,
          kind,
          content: file.content ?? "",
          sha: file.sha256,
          language: file.language,
          size: file.size,
        });
        setRevealLine(line ?? null);
      } catch {
        setOpenError(path);
      }
    },
    [dispatch, projectId],
  );

  const write = useCallback(
    async (path: string, expectedSha: string | null) => {
      const tab = stateRef.current.tabs.find((candidate) => candidate.path === path);
      if (!tab || tab.kind !== "text" || !canWrite) return;
      const content = tab.content;
      dispatch({ type: "saveStarted", path, content });
      try {
        const saved = await putFile(projectId, path, content, expectedSha);
        dispatch({ type: "saveSucceeded", path, content, sha: saved.sha256 });
        setConflictPath(null);
        refreshAround(path); // the explorer's size and Git badge follow the save
      } catch (error) {
        if (error instanceof VersionConflict) {
          // Never resolved here: the user decides in the dialog (a second conflict opens it again).
          dispatch({ type: "conflict", path, serverSha: error.currentSha });
          setConflictPath(path);
          setConflictRound((round) => round + 1);
        } else {
          dispatch({ type: "saveFailed", path, error: saveErrorCode(error) });
        }
      }
    },
    [canWrite, dispatch, projectId, refreshAround],
  );

  const save = useCallback(
    (path: string) => {
      const tab = stateRef.current.tabs.find((candidate) => candidate.path === path);
      if (!tab || tab.status === "saving") return;
      if (tab.status === "conflict") {
        setConflictPath(path);
        return;
      }
      void write(path, tab.sha);
    },
    [write],
  );

  const close = (path: string) => {
    dispatch({ type: "closed", path });
    disposeModel(projectId, path);
  };
  const handleClose = (path: string) => {
    const tab = state.tabs.find((candidate) => candidate.path === path);
    if (tab && isDirty(tab)) setClosePath(path);
    else close(path);
  };
  const handleReload = (path: string, file: FileContent) => {
    dispatch({ type: "reloaded", path, content: file.content ?? "", sha: file.sha256 });
    setConflictPath(null);
  };

  const conflictTab = state.tabs.find((tab) => tab.path === conflictPath) ?? null;
  return (
    <div className="-m-6 flex min-h-[32rem] flex-col lg:h-[calc(100vh-4rem)]">
      <header className="flex flex-wrap items-center gap-3 border-b border-line bg-surface px-4 py-2">
        <Link
          to={PROJECTS_PATH}
          className="inline-flex items-center gap-1 text-sm text-brand-600 hover:underline"
        >
          <ArrowLeft aria-hidden="true" className="size-4" />
          {t("workspace.back")}
        </Link>
        <h1 className="text-lg font-semibold text-fg" data-testid="screen-title">
          {name}
        </h1>
        <span className="inline-flex items-center gap-1 rounded border border-line px-2 py-0.5 font-mono text-xs text-fg-muted">
          <GitBranch aria-hidden="true" className="size-3.5" />
          {t("workspace.branch", { name: branch })}
        </span>
        {STATUS_REASON[status] ? (
          <span className="text-xs text-status-warning">{t(`workspace.repo.${STATUS_REASON[status]}`)}</span>
        ) : null}
      </header>
      {openError ? (
        <p role="alert" className="bg-status-danger-bg px-4 py-2 text-sm text-status-danger">
          {t("workspace.editor.openFailed", { path: openError })}
        </p>
      ) : null}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[16rem_minmax(0,1fr)_20rem]">
        <Explorer
          projectId={projectId}
          canWrite={canWrite}
          activePath={state.active}
          onOpen={(path) => void openFile(path)}
          onCreate={setCreating}
          onDelete={setDeletePath}
        />
        <EditorArea
          projectId={projectId}
          tabs={state.tabs}
          active={state.active}
          canWrite={canWrite}
          revealLine={revealLine}
          onActivate={(path) => dispatch({ type: "activated", path })}
          onClose={handleClose}
          onEdit={(path, content) => dispatch({ type: "edited", path, content })}
          onSave={save}
          onResolve={setConflictPath}
        />
        <aside
          aria-label={t("workspace.layout.right")}
          className="flex min-h-0 flex-col border-t border-line bg-surface lg:border-t-0 lg:border-l"
        >
          <div role="tablist" className="flex border-b border-line">
            {RIGHT_TABS.map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={rightTab === key}
                onClick={() => setRightTab(key)}
                className={cn(
                  "px-3 py-2 text-sm",
                  rightTab === key ? "border-b-2 border-brand-600 text-fg" : "text-fg-muted",
                )}
              >
                {t(key === "search" ? "workspace.search.title" : "workspace.chat.title")}
              </button>
            ))}
          </div>
          <div role="tabpanel" className="min-h-0 flex-1 overflow-auto">
            {rightTab === "search" ? (
              <SearchPanel projectId={projectId} onOpen={(path, line) => void openFile(path, line)} />
            ) : (
              <StateView
                state="capability_disabled"
                title={t("workspace.chat.title")}
                detail={t("workspace.chat.body")}
              />
            )}
          </div>
        </aside>
      </div>
      <section aria-label={t("workspace.layout.bottom")} className="border-t border-line bg-surface">
        <div className="flex items-center gap-3 px-3 py-1">
          <span className="text-sm font-medium text-fg">{t("workspace.bottom.terminal")}</span>
          <span className="text-sm text-fg-muted">{t("workspace.bottom.tests")}</span>
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto"
            aria-label={t(isBottomOpen ? "workspace.layout.hideBottom" : "workspace.layout.showBottom")}
            aria-expanded={isBottomOpen}
            onClick={() => setIsBottomOpen((open) => !open)}
          >
            {isBottomOpen ? (
              <PanelBottomClose aria-hidden="true" className="size-4" />
            ) : (
              <PanelBottomOpen aria-hidden="true" className="size-4" />
            )}
          </Button>
        </div>
        {isBottomOpen ? (
          <StateView
            state="capability_disabled"
            className="p-3"
            title={t("workspace.bottom.terminal")}
            detail={t("workspace.bottom.body")}
          />
        ) : null}
      </section>

      {conflictTab ? (
        <ConflictDialog
          key={`${conflictTab.path}:${conflictRound}`}
          projectId={projectId}
          tab={conflictTab}
          onClose={() => setConflictPath(null)}
          onReload={(file) => handleReload(conflictTab.path, file)}
          onOverwrite={(serverSha) => void write(conflictTab.path, serverSha)}
        />
      ) : null}
      {closePath ? (
        <ConfirmDialog
          title={t("workspace.unsaved.title")}
          body={t("workspace.unsaved.body", { path: closePath })}
          confirm={t("workspace.unsaved.discard")}
          cancel={t("workspace.unsaved.stay")}
          onConfirm={() => {
            close(closePath);
            setClosePath(null);
          }}
          onCancel={() => setClosePath(null)}
        />
      ) : null}
      {blocker.state === "blocked" ? (
        <ConfirmDialog
          title={t("workspace.unsaved.title")}
          body={t("workspace.unsaved.leaveBody")}
          confirm={t("workspace.unsaved.discard")}
          cancel={t("workspace.unsaved.stay")}
          onConfirm={() => blocker.proceed()}
          onCancel={() => blocker.reset()}
        />
      ) : null}
      {creating ? (
        <CreateDialog
          type={creating}
          onCancel={() => setCreating(null)}
          onCreate={async (path) => {
            await createPath(projectId, path, creating);
            refreshAround(path);
            setCreating(null);
            if (creating === "file") void openFile(path);
          }}
        />
      ) : null}
      {deletePath ? (
        <DeleteDialog
          path={deletePath}
          onCancel={() => setDeletePath(null)}
          onDelete={async () => {
            const open = stateRef.current.tabs.find((tab) => tab.path === deletePath);
            const sha = open ? open.sha : (await getFile(projectId, deletePath)).sha256;
            await deleteFile(projectId, deletePath, sha);
            if (open) close(deletePath);
            refreshAround(deletePath);
            setDeletePath(null);
          }}
        />
      ) : null}
    </div>
  );
}

type ConfirmProps = {
  title: string;
  body: string;
  confirm: string;
  cancel: string;
  onConfirm: () => void;
  onCancel: () => void;
};

function ConfirmDialog({ title, body, confirm, cancel, onConfirm, onCancel }: ConfirmProps) {
  return (
    <Dialog open onOpenChange={(isOpen) => !isOpen && onCancel()}>
      <DialogContent title={title} description={body}>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>
            {cancel}
          </Button>
          <Button variant="danger" onClick={onConfirm}>
            {confirm}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

type CreateProps = { type: "file" | "dir"; onCancel: () => void; onCreate: (path: string) => Promise<void> };

function CreateDialog({ type, onCancel, onCreate }: CreateProps) {
  const { t } = useTranslation();
  const inputId = useId();
  const [path, setPath] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const clean = path.trim().replace(/^\/+/, "");
    if (!clean) return;
    setIsBusy(true);
    setError(null);
    try {
      await onCreate(clean);
    } catch (caught) {
      setError(`${t("workspace.create.failed", { path: clean })} ${describeError(caught, t).title}`);
      setIsBusy(false);
    }
  };
  return (
    <Dialog open onOpenChange={(isOpen) => !isOpen && onCancel()}>
      <DialogContent
        title={t(type === "file" ? "workspace.create.titleFile" : "workspace.create.titleFolder")}
      >
        <form onSubmit={(event) => void handleSubmit(event)} className="flex flex-col gap-2">
          <Label htmlFor={inputId}>{t("workspace.create.path")}</Label>
          <Input
            id={inputId}
            value={path}
            onChange={(event) => setPath(event.target.value)}
            className="font-mono"
          />
          {error ? (
            <p role="alert" className="text-sm text-status-danger">
              {error}
            </p>
          ) : null}
          <div className="mt-2 flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onCancel}>
              {t("workspace.create.cancel")}
            </Button>
            <Button type="submit" disabled={isBusy || !path.trim()}>
              {t("workspace.create.submit")}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

type DeleteProps = { path: string; onCancel: () => void; onDelete: () => Promise<void> };

function DeleteDialog({ path, onCancel, onDelete }: DeleteProps) {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const handleDelete = async () => {
    setIsBusy(true);
    setError(null);
    try {
      await onDelete();
    } catch (caught) {
      setError(`${t("workspace.remove.failed", { path })} ${describeError(caught, t).title}`);
      setIsBusy(false);
    }
  };
  return (
    <Dialog open onOpenChange={(isOpen) => !isOpen && onCancel()}>
      <DialogContent title={t("workspace.remove.title")} description={t("workspace.remove.body", { path })}>
        {error ? (
          <p role="alert" className="text-sm text-status-danger">
            {error}
          </p>
        ) : null}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>
            {t("workspace.remove.cancel")}
          </Button>
          <Button variant="danger" disabled={isBusy} onClick={() => void handleDelete()}>
            {t("workspace.remove.submit")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
