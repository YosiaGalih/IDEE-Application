import {
  ChevronDown,
  ChevronRight,
  CircleDot,
  File,
  FilePlus,
  Folder,
  FolderPlus,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useGitStatus, useTree } from "@/api/hooks/useWorkspace";
import type { GitStatus, TreeEntry } from "@/api/workspace";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { formatBytes } from "@/lib/formatBytes";
import { workspaceErrorText } from "./workspaceErrors";

type GitChange = GitStatus["changes"][number]["status"];
const GIT_MARK: Record<GitChange, string> = {
  added: "A",
  modified: "M",
  deleted: "D",
  untracked: "U",
  conflicted: "C",
};

type Props = {
  projectId: string;
  canWrite: boolean;
  activePath: string | null;
  onOpen: (path: string) => void;
  onCreate: (type: "file" | "dir") => void;
  onDelete: (path: string) => void;
};

/** The project tree (FR-019, FR-026): one level per request, folders load when opened. */
export function Explorer({ projectId, canWrite, activePath, onOpen, onCreate, onDelete }: Props) {
  const { t } = useTranslation();
  const git = useGitStatus(projectId);
  // After a failed refresh the last markers may be stale: none is shown rather than old ones.
  const changes = new Map(
    (git.isError ? [] : (git.data?.changes ?? [])).map((change) => [change.path, change.status]),
  );
  return (
    <nav
      aria-label={t("workspace.explorer.title")}
      className="flex max-h-[40vh] min-h-0 flex-col border-b border-line bg-surface lg:max-h-none lg:border-r lg:border-b-0"
    >
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
          {t("workspace.explorer.title")}
        </h2>
        {canWrite ? (
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="sm"
              aria-label={t("workspace.explorer.newFile")}
              onClick={() => onCreate("file")}
            >
              <FilePlus aria-hidden="true" className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              aria-label={t("workspace.explorer.newFolder")}
              onClick={() => onCreate("dir")}
            >
              <FolderPlus aria-hidden="true" className="size-4" />
            </Button>
          </div>
        ) : null}
      </div>
      {git.isError ? (
        <p className="px-3 py-2 text-xs text-fg-muted">{t("workspace.explorer.gitUnavailable")}</p>
      ) : null}
      <div className="min-h-0 flex-1 overflow-auto py-1 text-sm">
        <Folder_
          projectId={projectId}
          path="."
          depth={0}
          {...{ canWrite, activePath, onOpen, onDelete, changes }}
        />
      </div>
    </nav>
  );
}

type FolderProps = {
  projectId: string;
  path: string;
  depth: number;
  canWrite: boolean;
  activePath: string | null;
  changes: Map<string, GitChange>;
  onOpen: (path: string) => void;
  onDelete: (path: string) => void;
};

/** Why a folder could not be read (permission, repository state, ...), with a way to try again. */
function FolderError({ error, depth, onRetry }: { error: unknown; depth: number; onRetry: () => void }) {
  const { t } = useTranslation();
  const { text, requestId } = workspaceErrorText(t, error);
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-2 px-3 py-1 text-xs text-status-danger"
      style={{ paddingLeft: `${depth + 1}rem` }}
    >
      <span>
        {t("workspace.explorer.loadFailed")} {text}
      </span>
      {requestId ? <span className="font-mono">{t("common.requestId", { id: requestId })}</span> : null}
      <Button variant="ghost" size="sm" className="h-6 px-1" onClick={onRetry}>
        {t("workspace.retry")}
      </Button>
    </div>
  );
}

function Folder_({ projectId, path, depth, ...rest }: FolderProps) {
  const { t } = useTranslation();
  const tree = useTree(projectId, path);
  if (tree.isPending)
    return (
      <div
        role="status"
        className="flex items-center gap-2 px-3 py-1 text-fg-muted"
        style={{ paddingLeft: `${depth + 1}rem` }}
      >
        <Spinner />
      </div>
    );
  if (tree.data === undefined)
    return <FolderError error={tree.error} depth={depth} onRetry={() => void tree.refetch()} />;
  const entries = [...tree.data.entries].sort((a, b) =>
    a.type === b.type ? a.name.localeCompare(b.name) : a.type === "dir" ? -1 : 1,
  );
  return (
    <ul>
      {/* A failed refresh keeps the entries already read, and says they may be out of date. */}
      {tree.isError ? (
        <li>
          <FolderError error={tree.error} depth={depth} onRetry={() => void tree.refetch()} />
        </li>
      ) : null}
      {/* "Empty" only for a complete answer: a cut-off one with no entries is not an empty folder. */}
      {entries.length === 0 && !tree.data.truncated ? (
        <li className="px-3 py-1 text-xs text-fg-muted" style={{ paddingLeft: `${depth + 1}rem` }}>
          {t("workspace.explorer.empty")}
        </li>
      ) : null}
      {entries.map((entry) => (
        <Entry key={entry.path} projectId={projectId} entry={entry} depth={depth} {...rest} />
      ))}
      {tree.data.truncated ? (
        <li className="px-3 py-1 text-xs text-status-warning" style={{ paddingLeft: `${depth + 1}rem` }}>
          {t("workspace.explorer.truncated")}
        </li>
      ) : null}
    </ul>
  );
}

type EntryProps = Omit<FolderProps, "path"> & { entry: TreeEntry };

function Entry({ projectId, entry, depth, canWrite, activePath, changes, onOpen, onDelete }: EntryProps) {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const change = changes.get(entry.path);
  const indent = { paddingLeft: `${depth + 0.75}rem` };
  if (entry.type === "dir") {
    return (
      <li>
        <button
          type="button"
          aria-expanded={isOpen}
          aria-label={t(isOpen ? "workspace.explorer.collapseOf" : "workspace.explorer.expandOf", {
            name: entry.name,
          })}
          onClick={() => setIsOpen((open) => !open)}
          className="flex w-full items-center gap-1 px-3 py-1 text-left text-fg hover:bg-surface-muted"
          style={indent}
        >
          {isOpen ? (
            <ChevronDown aria-hidden="true" className="size-3.5" />
          ) : (
            <ChevronRight aria-hidden="true" className="size-3.5" />
          )}
          <Folder aria-hidden="true" className="size-4 text-fg-muted" />
          <span className="truncate">{entry.name}</span>
        </button>
        {isOpen ? (
          <Folder_
            projectId={projectId}
            path={entry.path}
            depth={depth + 1}
            {...{ canWrite, activePath, changes, onOpen, onDelete }}
          />
        ) : null}
      </li>
    );
  }
  const isActive = activePath === entry.path;
  return (
    <li
      className={
        isActive
          ? "group flex items-center bg-surface-muted"
          : "group flex items-center hover:bg-surface-muted"
      }
    >
      <button
        type="button"
        aria-label={t("workspace.explorer.openOf", { name: entry.name })}
        aria-current={isActive ? "true" : undefined}
        onClick={() => onOpen(entry.path)}
        className="flex min-w-0 flex-1 items-center gap-1 px-3 py-1 text-left text-fg"
        style={indent}
      >
        <File aria-hidden="true" className="size-4 shrink-0 text-fg-muted" />
        <span className="truncate">{entry.name}</span>
        {entry.size !== null ? (
          <span className="ml-auto shrink-0 text-xs text-fg-muted">{formatBytes(entry.size)}</span>
        ) : null}
      </button>
      {change ? (
        <span
          title={t(`workspace.git.${change}`)}
          className="mr-1 inline-flex shrink-0 items-center gap-0.5 text-xs font-semibold text-status-info"
        >
          <CircleDot aria-hidden="true" className="size-3" />
          <span aria-hidden="true">{GIT_MARK[change]}</span>
          <span className="sr-only">{t(`workspace.git.${change}`)}</span>
        </span>
      ) : null}
      {canWrite ? (
        <Button
          variant="ghost"
          size="sm"
          className="mr-1 h-6 px-1 opacity-0 focus:opacity-100 group-hover:opacity-100"
          aria-label={t("workspace.explorer.deleteOf", { name: entry.name })}
          onClick={() => onDelete(entry.path)}
        >
          <Trash2 aria-hidden="true" className="size-3.5" />
        </Button>
      ) : null}
    </li>
  );
}
