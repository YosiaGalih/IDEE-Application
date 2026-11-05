import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useGitLog } from "@/api/hooks/useWorkspace";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { workspaceErrorText } from "../workspaceErrors";

/** The API has no cursor: "load more" asks for a longer list, up to its maximum of 200 (routes/git.py). */
const LIMITS = [20, 50, 100, 200] as const;

export function GitLog({ projectId }: { projectId: string }) {
  const { t, i18n } = useTranslation();
  const headingId = useId();
  const [step, setStep] = useState(0);
  const limit = LIMITS[step] ?? 20;
  const log = useGitLog(projectId, limit);
  const failure = log.isError ? workspaceErrorText(t, log.error) : null;
  const format = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" });
  const entries = log.data ?? [];
  const isFull = entries.length >= limit;
  const isLast = step === LIMITS.length - 1;
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2 border-t border-line px-3 pt-3">
      <h2 id={headingId} className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
        {t("workspace.gitPanel.log.title")}
      </h2>
      {log.isPending ? <Spinner /> : null}
      {failure ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-status-danger">
          {t("workspace.gitPanel.log.loadFailed")} {failure.text}
          <Button variant="ghost" size="sm" className="h-6 px-1" onClick={() => void log.refetch()}>
            {t("workspace.retry")}
          </Button>
        </div>
      ) : null}
      {log.data && entries.length === 0 ? (
        <p className="text-xs text-fg-muted">{t("workspace.gitPanel.log.empty")}</p>
      ) : null}
      {entries.length > 0 ? (
        <ol aria-label={t("workspace.gitPanel.log.title")} className="flex flex-col gap-1.5">
          {entries.map((entry) => (
            <li key={entry.commit} className="flex min-w-0 flex-col text-xs">
              <span className="flex min-w-0 gap-2">
                <span className="shrink-0 font-mono text-brand-600">{entry.commit.slice(0, 7)}</span>
                <span className="truncate text-fg" title={entry.message}>
                  {entry.message}
                </span>
              </span>
              <span className="truncate text-fg-muted" title={entry.author}>
                {entry.author}
              </span>
              <time dateTime={entry.date} className="text-fg-muted">
                {format.format(new Date(entry.date))}
              </time>
            </li>
          ))}
        </ol>
      ) : null}
      {isFull && !isLast ? (
        <Button
          variant="ghost"
          size="sm"
          className="self-start"
          disabled={log.isFetching}
          onClick={() => setStep((current) => Math.min(current + 1, LIMITS.length - 1))}
        >
          {t("workspace.gitPanel.log.more")}
        </Button>
      ) : null}
      {isFull && isLast ? (
        <p className="text-xs text-fg-muted">{t("workspace.gitPanel.log.limit", { count: limit })}</p>
      ) : null}
    </section>
  );
}
