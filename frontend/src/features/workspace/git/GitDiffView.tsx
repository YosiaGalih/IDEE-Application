import { FileMinus, FilePen, FilePlus, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { DiffFile, DiffHunk, GitDiff } from "@/api/git";

const STATUS_ICON: Record<DiffFile["status"], LucideIcon> = {
  added: FilePlus,
  modified: FilePen,
  deleted: FileMinus,
};
/** A sign per line, so added and removed lines differ without colour too. */
const SIGN: Record<DiffHunk["lines"][number]["type"], string> = { add: "+", del: "−", context: "" };
const ROW: Record<DiffHunk["lines"][number]["type"], string> = {
  add: "bg-status-success-bg",
  del: "bg-status-danger-bg",
  context: "",
};

/**
 * A diff from the API (HEAD against the working tree), as unified lines with their numbers. Every path and
 * line is repository content: it is shown as text, never as markup.
 */
export function GitDiffView({ diff }: { diff: GitDiff }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-3 text-sm">
      {diff.truncated ? (
        <p role="status" className="text-status-warning">
          {t("workspace.gitPanel.diff.truncated")}
        </p>
      ) : null}
      {diff.files.length === 0 && !diff.truncated ? (
        <p className="text-fg-muted">{t("workspace.gitPanel.diff.empty")}</p>
      ) : null}
      {diff.files.map((file) => (
        <FileDiff key={`${file.old_path ?? ""}>${file.path}`} file={file} />
      ))}
    </div>
  );
}

function FileDiff({ file }: { file: DiffFile }) {
  const { t } = useTranslation();
  const Icon = STATUS_ICON[file.status];
  return (
    <section aria-label={file.path} className="rounded border border-line">
      <header className="flex flex-wrap items-center gap-2 border-b border-line bg-surface-muted px-3 py-1.5">
        <span className="font-mono text-xs text-fg">{file.path}</span>
        {file.old_path && file.old_path !== file.path ? (
          <span className="font-mono text-xs text-fg-muted">
            {t("workspace.gitPanel.diff.movedFrom", { path: file.old_path })}
          </span>
        ) : null}
        <span className="inline-flex items-center gap-1 text-xs text-fg-muted">
          <Icon aria-hidden="true" className="size-3.5" />
          {t(`workspace.gitPanel.diff.status.${file.status}`)}
        </span>
        <span className="ml-auto font-mono text-xs text-fg-muted">
          {t("workspace.gitPanel.diff.lineStats", { additions: file.additions, deletions: file.deletions })}
        </span>
      </header>
      {file.is_binary ? (
        <p className="px-3 py-2 text-xs text-fg-muted">{t("workspace.gitPanel.diff.binary")}</p>
      ) : file.hunks.length === 0 ? (
        <p className="px-3 py-2 text-xs text-fg-muted">{t("workspace.gitPanel.diff.noHunks")}</p>
      ) : (
        // Hunk headers carry their ranges, but two equal ones must not share a key.
        file.hunks.map((hunk, index) => <Hunk key={`${index}:${hunk.header}`} hunk={hunk} />)
      )}
    </section>
  );
}

function Hunk({ hunk }: { hunk: DiffHunk }) {
  const { t } = useTranslation();
  return (
    <div className="overflow-x-auto">
      <p className="bg-surface px-3 py-1 font-mono text-xs text-status-info">{hunk.header}</p>
      <table className="w-full border-collapse font-mono text-xs">
        <thead className="sr-only">
          <tr>
            <th scope="col">{t("workspace.gitPanel.diff.oldLine")}</th>
            <th scope="col">{t("workspace.gitPanel.diff.newLine")}</th>
            <th scope="col">{t("workspace.gitPanel.diff.sign")}</th>
            <th scope="col">{t("workspace.gitPanel.diff.content")}</th>
          </tr>
        </thead>
        <tbody>
          {hunk.lines.map((line, index) => (
            // Lines have no identity of their own; their position in the hunk is it.
            <tr key={index} className={ROW[line.type]}>
              <td className="w-10 select-none px-2 text-right text-fg-muted">{line.old_no ?? ""}</td>
              <td className="w-10 select-none px-2 text-right text-fg-muted">{line.new_no ?? ""}</td>
              <td className="w-6 select-none text-center text-fg">
                <span aria-hidden="true">{SIGN[line.type]}</span>
                {line.type === "context" ? null : (
                  <span className="sr-only">
                    {t(
                      line.type === "add"
                        ? "workspace.gitPanel.diff.added"
                        : "workspace.gitPanel.diff.removed",
                    )}
                  </span>
                )}
              </td>
              <td className="whitespace-pre px-2 text-fg">
                <code>{line.text}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
