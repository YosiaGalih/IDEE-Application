import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, GitBranch, PanelBottomClose, PanelBottomOpen } from "lucide-react";
import { useCallback, useEffect, useId, useReducer, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useBlocker, useParams } from "react-router";
import { ApiError } from "@/api/errors";
import { useProject } from "@/api/hooks/useProjects";
import { gitStatusKey, treeKey, useGitStatus } from "@/api/hooks/useWorkspace";
import type { Project } from "@/api/projects";
import { uxStateForError } from "@/api/uxState";
import { createPath, deleteFile, getFile, putFile, VersionConflict, type FileContent } from "@/api/workspace";
import { Button } from "@/components/ui/Button";
import { StateView } from "@/components/ui/StateView";
import { cn } from "@/lib/cn";
import { PROJECTS_PATH } from "@/lib/paths";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import type { RevealRequest } from "./CodeEditor";
import { ConflictDialog } from "./ConflictDialog";
import { EditorArea } from "./EditorArea";
import { initialTabs, isDirty, tabsReducer, type TabsAction } from "./editorTabs";
import { Explorer } from "./Explorer";
import { GitPanel } from "./git/GitPanel";
import { disposeModel, disposeProjectModels } from "./lazyEditor";
import { SearchPanel } from "./SearchPanel";
import { ConfirmDialog, CreateDialog, DeleteDialog } from "./workspaceDialogs";
import { saveErrorOf, workspaceErrorText, type RepoReason } from "./workspaceErrors";

const RIGHT_TABS = ["search", "chat"] as const;
type LeftView = "explorer" | "git";
type RightTab = (typeof RIGHT_TABS)[number];
/** Why a project that is not active cannot be changed (the workspace API's 409 reasons). */
const STATUS_REASON: Partial<Record<string, RepoReason>> = {
  archived: "project_archived",
  deleting_pending_approval: "project_not_active",
};

/** The folders a path sits in, root first: creating `a/b/c.txt` can create `a` and `a/b` as well. */
const foldersAbove = (path: string) => {
  const parts = path.split("/");
  return [".", ...parts.slice(1).map((_, index) => parts.slice(0, index + 1).join("/"))];
};

const kindOf = (file: FileContent) =>
  file.too_large ? "large" : file.content === null || file.encoding === "binary" ? "binary" : "text";

// Module-wide, so they stay unique when the workspace mounts again.
let openSequence = 0;
let revealSequence = 0;

/** `/projects/:projectId/workspace` (layar 4, FR-026–032): explorer, editor, search; chat and terminal later. */
export function WorkspacePage() {
  const { projectId = "" } = useParams();
  const { t } = useTranslation();
  const project = useProject(projectId);
  useDocumentTitle(project.data?.name ?? t("screens.workspace"));
  // Only without any project data: once the workspace shows, a failed refresh must not unmount the
  // open tabs (and the unsaved text in them). It is said in a banner instead.
  if (project.data === undefined) {
    if (project.isError)
      return (
        <StateView
          state={uxStateForError(project.error)}
          titleAs="h1"
          requestId={project.error instanceof ApiError ? project.error.requestId : null}
          onAction={() => void project.refetch()}
        />
      );
    return <StateView state="loading" />;
  }
  return (
    <Workspace
      key={projectId}
      projectId={projectId}
      project={project.data}
      refreshError={project.isError ? project.error : null}
      onRefresh={() => void project.refetch()}
    />
  );
}

type WorkspaceProps = { projectId: string; project: Project; refreshError: unknown; onRefresh: () => void };

function Workspace({ projectId, project, refreshError, onRefresh }: WorkspaceProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const repository = project.repository;
  const repoReason: RepoReason | null =
    repository === null || repository.sync_state !== "ready"
      ? repository?.sync_state === "error"
        ? "repository_error"
        : "repository_not_ready"
      : null;
  const canWrite =
    project.permissions.includes("workspace:write") && project.status === "active" && repoReason === null;
  const canBranch =
    project.permissions.includes("git:branch") && project.status === "active" && repoReason === null;
  const [state, dispatchToReact] = useReducer(tabsReducer, initialTabs);
  // Handlers read the tabs from this ref, which runs the same pure reducer at dispatch time: Monaco can
  // deliver Ctrl+S before React renders the last keystroke, and the save must send what is on screen.
  const stateRef = useRef(state);
  const dispatch = useCallback((action: TabsAction) => {
    stateRef.current = tabsReducer(stateRef.current, action);
    dispatchToReact(action);
  }, []);
  const [reveal, setReveal] = useState<RevealRequest | null>(null);
  const [openError, setOpenError] = useState<{ path: string; text: string; requestId: string | null } | null>(
    null,
  );
  const [conflictPath, setConflictPath] = useState<string | null>(null);
  const [conflictRound, setConflictRound] = useState(0);
  const [closePath, setClosePath] = useState<string | null>(null);
  const [creating, setCreating] = useState<"file" | "dir" | null>(null);
  const [deletePath, setDeletePath] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<RightTab>("search");
  const [leftView, setLeftView] = useState<LeftView>("explorer");
  const gitStatus = useGitStatus(projectId);
  const changeCount = gitStatus.data?.changes.length ?? 0;
  const [isBottomOpen, setIsBottomOpen] = useState(true);
  const rightId = useId();
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

  // Leaving the workspace frees the editor models (text and undo history) of this project.
  useEffect(() => () => disposeProjectModels(projectId), [projectId]);

  const refreshAround = useCallback(
    (path: string) => {
      for (const folder of foldersAbove(path)) {
        void queryClient.invalidateQueries({ queryKey: treeKey(projectId, folder) });
      }
      void queryClient.invalidateQueries({ queryKey: gitStatusKey(projectId) });
    },
    [projectId, queryClient],
  );

  const openFile = useCallback(
    async (path: string, line?: number) => {
      setOpenError(null);
      // Only a search hit moves the cursor; opening from the explorer forgets an earlier request.
      setReveal(line ? { path, line, nonce: (revealSequence += 1) } : null);
      if (stateRef.current.tabs.some((tab) => tab.path === path)) {
        dispatch({ type: "activated", path });
        return;
      }
      let file: FileContent;
      try {
        file = await getFile(projectId, path);
      } catch (error) {
        setOpenError({ path, ...workspaceErrorText(t, error) });
        return;
      }
      dispatch({
        type: "opened",
        openId: (openSequence += 1),
        path,
        kind: kindOf(file),
        content: file.content ?? "",
        sha: file.sha256,
        language: file.language,
        size: file.size,
      });
    },
    [dispatch, projectId, t],
  );

  const write = useCallback(
    async (path: string, expectedSha: string | null) => {
      const tab = stateRef.current.tabs.find((candidate) => candidate.path === path);
      // One write per tab at a time: a second one would carry the same sha and conflict with the first.
      if (!tab || tab.kind !== "text" || !canWrite || tab.status === "saving") return;
      const { content, openId } = tab;
      dispatch({ type: "saveStarted", openId, path, content });
      try {
        const saved = await putFile(projectId, path, content, expectedSha);
        dispatch({ type: "saveSucceeded", openId, path, content, sha: saved.sha256 });
        setConflictPath((current) => (current === path ? null : current));
        refreshAround(path); // the explorer's size and Git badge follow the save
      } catch (error) {
        if (error instanceof VersionConflict) {
          // Never resolved here: the user decides in the dialog (a second conflict opens it again).
          dispatch({ type: "conflict", openId, path, serverSha: error.currentSha });
          setConflictPath(path);
          setConflictRound((round) => round + 1);
        } else {
          dispatch({ type: "saveFailed", openId, path, error: saveErrorOf(error) });
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
  const handleReload = (path: string, content: string, sha: string) => {
    dispatch({ type: "reloaded", path, content, sha });
    setConflictPath(null);
  };
  const handleRightKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const step = event.key === "ArrowRight" ? 1 : RIGHT_TABS.length - 1;
    const next = RIGHT_TABS[(RIGHT_TABS.indexOf(rightTab) + step) % RIGHT_TABS.length] ?? "search";
    setRightTab(next);
    document.getElementById(`${rightId}-tab-${next}`)?.focus();
  };

  // A repository that is not ready, before anything was opened: nothing to show but why.
  if (repoReason !== null && state.tabs.length === 0)
    return (
      <StateView
        state="partial_success"
        titleAs="h1"
        title={project.name}
        detail={t(`workspace.repo.${repoReason}`)}
      />
    );

  const conflictTab = state.tabs.find((tab) => tab.path === conflictPath) ?? null;
  const deleteTab = state.tabs.find((tab) => tab.path === deletePath) ?? null;
  const refreshFailure = refreshError === null ? null : workspaceErrorText(t, refreshError);
  const statusReason = STATUS_REASON[project.status];
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
          {project.name}
        </h1>
        {repository ? (
          <span className="inline-flex items-center gap-1 rounded border border-line px-2 py-0.5 font-mono text-xs text-fg-muted">
            <GitBranch aria-hidden="true" className="size-3.5" />
            {t("workspace.branch", { name: repository.branch })}
          </span>
        ) : null}
        {statusReason ? (
          <span className="text-xs text-status-warning">{t(`workspace.repo.${statusReason}`)}</span>
        ) : null}
      </header>
      {refreshFailure ? (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 bg-status-warning-bg px-4 py-2 text-sm text-status-warning"
        >
          <AlertTriangle aria-hidden="true" className="size-4" />
          {t("workspace.projectStale")} {refreshFailure.text}
          {refreshFailure.requestId ? (
            <span className="font-mono text-xs">
              {t("common.requestId", { id: refreshFailure.requestId })}
            </span>
          ) : null}
          <Button size="sm" variant="secondary" onClick={onRefresh}>
            {t("workspace.retry")}
          </Button>
        </div>
      ) : null}
      {repoReason !== null ? (
        <p role="status" className="bg-status-warning-bg px-4 py-2 text-sm text-status-warning">
          {t(`workspace.repo.${repoReason}`)} {t("workspace.repoChangedWhileOpen")}
        </p>
      ) : null}
      {openError ? (
        <p role="alert" className="bg-status-danger-bg px-4 py-2 text-sm text-status-danger">
          {t("workspace.editor.openFailed", { path: openError.path })} {openError.text}
          {openError.requestId ? (
            <span className="ml-1 font-mono text-xs">
              {t("common.requestId", { id: openError.requestId })}
            </span>
          ) : null}
        </p>
      ) : null}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[16rem_minmax(0,1fr)_20rem]">
        <div className="flex max-h-[50vh] min-h-0 flex-col border-b border-line bg-surface lg:max-h-none lg:border-r lg:border-b-0">
          <div role="group" aria-label={t("workspace.layout.left")} className="flex border-b border-line">
            <button
              type="button"
              aria-pressed={leftView === "explorer"}
              onClick={() => setLeftView("explorer")}
              className={cn(
                "flex-1 px-3 py-1.5 text-xs",
                leftView === "explorer" ? "font-semibold text-fg" : "text-fg-muted",
              )}
            >
              {t("workspace.gitPanel.view.explorer")}
            </button>
            <button
              type="button"
              aria-pressed={leftView === "git"}
              aria-label={
                changeCount > 0 ? t("workspace.gitPanel.view.gitCount", { count: changeCount }) : undefined
              }
              onClick={() => setLeftView("git")}
              className={cn(
                "flex flex-1 items-center justify-center gap-1 px-3 py-1.5 text-xs",
                leftView === "git" ? "font-semibold text-fg" : "text-fg-muted",
              )}
            >
              {t("workspace.gitPanel.view.git")}
              {changeCount > 0 ? (
                <span aria-hidden="true" className="rounded-full border border-line px-1.5 text-[0.65rem]">
                  {changeCount}
                </span>
              ) : null}
            </button>
          </div>
          {/* Both stay mounted: open folders and a half-written commit message survive the switch. */}
          <div hidden={leftView !== "explorer"} className="flex min-h-0 flex-1 flex-col">
            <Explorer
              projectId={projectId}
              canWrite={canWrite}
              activePath={state.active}
              onOpen={(path) => void openFile(path)}
              onCreate={setCreating}
              onDelete={setDeletePath}
            />
          </div>
          <div hidden={leftView !== "git"} className="flex min-h-0 flex-1 flex-col">
            <GitPanel
              projectId={projectId}
              canRead={project.permissions.includes("git:read")}
              canCommit={canWrite}
              canBranch={canBranch}
              unsavedPaths={state.tabs.filter(isDirty).map((tab) => tab.path)}
              isActive={leftView === "git"}
            />
          </div>
        </div>
        <EditorArea
          projectId={projectId}
          tabs={state.tabs}
          active={state.active}
          canWrite={canWrite}
          reveal={reveal}
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
                id={`${rightId}-tab-${key}`}
                type="button"
                role="tab"
                aria-selected={rightTab === key}
                aria-controls={`${rightId}-panel`}
                tabIndex={rightTab === key ? 0 : -1}
                onClick={() => setRightTab(key)}
                onKeyDown={handleRightKey}
                className={cn(
                  "px-3 py-2 text-sm",
                  rightTab === key ? "border-b-2 border-brand-600 text-fg" : "text-fg-muted",
                )}
              >
                {t(key === "search" ? "workspace.search.title" : "workspace.chat.title")}
              </button>
            ))}
          </div>
          <div
            role="tabpanel"
            id={`${rightId}-panel`}
            aria-labelledby={`${rightId}-tab-${rightTab}`}
            className="min-h-0 flex-1 overflow-auto"
          >
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
          onReload={(content, sha) => handleReload(conflictTab.path, content, sha)}
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
            try {
              await createPath(projectId, path, creating);
            } finally {
              refreshAround(path); // also after a failure: the explorer shows what is really there
            }
            setCreating(null);
            if (creating === "file") void openFile(path);
          }}
        />
      ) : null}
      {deletePath ? (
        <DeleteDialog
          path={deletePath}
          hasUnsaved={deleteTab !== null && isDirty(deleteTab)}
          onCancel={() => setDeletePath(null)}
          onDelete={async () => {
            try {
              // The open tab's sha: a file changed on the server since it was read is refused (409).
              const sha = deleteTab ? deleteTab.sha : (await getFile(projectId, deletePath)).sha256;
              await deleteFile(projectId, deletePath, sha);
            } finally {
              refreshAround(deletePath);
            }
            if (deleteTab) close(deletePath);
            setDeletePath(null);
          }}
        />
      ) : null}
    </div>
  );
}
