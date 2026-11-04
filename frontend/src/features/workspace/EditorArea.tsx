import {
  AlertTriangle,
  CheckCircle2,
  CircleDot,
  FileX,
  Loader2,
  Save,
  X,
  type LucideIcon,
} from "lucide-react";
import { Suspense } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { StateView } from "@/components/ui/StateView";
import { cn } from "@/lib/cn";
import { formatBytes } from "@/lib/formatBytes";
import type { Tab, TabStatus } from "./editorTabs";
import { LazyCodeEditor } from "./lazyEditor";

const STATUS_ICON: Record<TabStatus, LucideIcon> = {
  saved: CheckCircle2,
  dirty: CircleDot,
  saving: Loader2,
  failed: AlertTriangle,
  conflict: AlertTriangle,
};

const nameOf = (path: string) => path.split("/").pop() ?? path;

type Props = {
  projectId: string;
  tabs: Tab[];
  active: string | null;
  canWrite: boolean;
  revealLine: number | null;
  onActivate: (path: string) => void;
  onClose: (path: string) => void;
  onEdit: (path: string, content: string) => void;
  onSave: (path: string) => void;
  onResolve: (path: string) => void;
};

function StatusText({ tab }: { tab: Tab }) {
  const { t } = useTranslation();
  const Icon = STATUS_ICON[tab.status];
  return (
    <span className="inline-flex items-center gap-1 text-xs text-fg-muted">
      <Icon aria-hidden="true" className={cn("size-3.5", tab.status === "saving" && "animate-spin")} />
      {t(`workspace.editor.status.${tab.status}`)}
    </span>
  );
}

/** The open files (FR-027, FR-030): each tab says whether the server holds what it shows. */
export function EditorArea({
  projectId,
  tabs,
  active,
  canWrite,
  revealLine,
  onActivate,
  onClose,
  onEdit,
  onSave,
  onResolve,
}: Props) {
  const { t } = useTranslation();
  const tab = tabs.find((candidate) => candidate.path === active) ?? null;
  return (
    <section aria-label={t("workspace.layout.main")} className="flex min-h-0 min-w-0 flex-col bg-[#1e1e1e]">
      <div role="tablist" className="flex overflow-x-auto border-b border-line bg-surface">
        {tabs.map((item) => (
          <div
            key={item.path}
            className={cn(
              "flex shrink-0 items-center gap-2 border-r border-line px-3 py-2 text-sm",
              item.path === active ? "bg-surface-muted text-fg" : "text-fg-muted",
            )}
          >
            <button
              type="button"
              role="tab"
              aria-selected={item.path === active}
              onClick={() => onActivate(item.path)}
              className="flex items-center gap-2"
            >
              <span className="font-mono">{nameOf(item.path)}</span>
              <StatusText tab={item} />
            </button>
            <button
              type="button"
              aria-label={t("workspace.editor.closeOf", { name: nameOf(item.path) })}
              onClick={() => onClose(item.path)}
              className="rounded p-0.5 hover:bg-surface"
            >
              <X aria-hidden="true" className="size-3.5" />
            </button>
          </div>
        ))}
      </div>
      {tab === null ? (
        <p className="m-auto p-6 text-sm text-fg-muted">{t("workspace.editor.empty")}</p>
      ) : (
        <div role="tabpanel" className="flex min-h-0 flex-1 flex-col">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-surface px-3 py-1.5">
            <span className="min-w-0 truncate font-mono text-xs text-fg">{tab.path}</span>
            <div className="flex items-center gap-2">
              <StatusText tab={tab} />
              {tab.status === "conflict" ? (
                <Button size="sm" variant="secondary" onClick={() => onResolve(tab.path)}>
                  {t("workspace.conflict.title")}
                </Button>
              ) : null}
              {canWrite && tab.kind === "text" ? (
                <Button
                  size="sm"
                  aria-label={t("workspace.editor.saveOf", { name: nameOf(tab.path) })}
                  disabled={tab.status === "saving" || tab.status === "conflict" || tab.status === "saved"}
                  onClick={() => onSave(tab.path)}
                >
                  <Save aria-hidden="true" className="size-4" />
                  {t("workspace.editor.save")}
                </Button>
              ) : null}
            </div>
          </div>
          {tab.status === "failed" ? (
            <div
              role="alert"
              className="flex flex-wrap items-center gap-2 bg-status-danger-bg px-3 py-2 text-sm text-status-danger"
            >
              <AlertTriangle aria-hidden="true" className="size-4" />
              {t(
                `workspace.editor.failed.${tab.error === "not_found" || tab.error === "forbidden" ? tab.error : "generic"}`,
              )}
              {canWrite ? (
                <Button size="sm" variant="secondary" onClick={() => onSave(tab.path)}>
                  {t("workspace.editor.failed.retry")}
                </Button>
              ) : null}
            </div>
          ) : null}
          {!canWrite && tab.kind === "text" ? (
            <p className="bg-surface px-3 py-1 text-xs text-fg-muted">{t("workspace.editor.readOnly")}</p>
          ) : null}
          {tab.kind === "text" ? (
            <div className="min-h-0 flex-1">
              <Suspense fallback={<StateView state="loading" />}>
                <LazyCodeEditor
                  projectId={projectId}
                  path={tab.path}
                  value={tab.content}
                  language={tab.language}
                  isReadOnly={!canWrite}
                  ariaLabel={t("workspace.editor.editorOf", { name: tab.path })}
                  onChange={(content) => onEdit(tab.path, content)}
                  onSave={() => onSave(tab.path)}
                  revealLine={revealLine}
                />
              </Suspense>
            </div>
          ) : (
            <FileMeta tab={tab} />
          )}
        </div>
      )}
    </section>
  );
}

function FileMeta({ tab }: { tab: Tab }) {
  const { t } = useTranslation();
  return (
    <div className="m-6 flex flex-col gap-3 rounded-[var(--radius-card)] border border-line bg-surface p-5 text-sm">
      <p className="flex items-center gap-2 font-medium text-fg">
        <FileX aria-hidden="true" className="size-4" />
        {t(tab.kind === "binary" ? "workspace.editor.binary" : "workspace.editor.tooLarge")}
      </p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-fg-muted">
        <dt>{t("workspace.editor.size")}</dt>
        <dd>{formatBytes(tab.size ?? 0)}</dd>
        <dt>{t("workspace.editor.sha")}</dt>
        <dd className="break-all font-mono text-xs">{tab.sha}</dd>
      </dl>
    </div>
  );
}
