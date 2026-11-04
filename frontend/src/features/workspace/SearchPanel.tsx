import { Search } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/errors";
import { useSearch } from "@/api/hooks/useWorkspace";
import type { SearchMeta, SearchParams } from "@/api/workspace";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { StateView } from "@/components/ui/StateView";
import { describeError } from "@/lib/describeError";
import { reasonOf } from "./workspaceErrors";

const MODES = ["text", "symbol"] as const;

type Props = { projectId: string; onOpen: (path: string, line: number) => void };

/** "No results" only for a whole answer: not when it was cut, skipped files, or had no index to use. */
const isComplete = (asked: SearchParams, meta: SearchMeta) =>
  !meta.truncated && !meta.skipped && !meta.index_truncated && !(asked.mode === "symbol" && !meta.indexed_at);

/** Text and symbol search (FR-020, FR-021): runs on submit, says when the answer is partial. */
export function SearchPanel({ projectId, onOpen }: Props) {
  const { t, i18n } = useTranslation();
  const ids = {
    q: useId(),
    mode: useId(),
    glob: useId(),
    kind: useId(),
    regex: useId(),
    ignoreCase: useId(),
  };
  const [draft, setDraft] = useState<SearchParams>({ q: "", mode: "text", regex: false, ignoreCase: true });
  const [asked, setAsked] = useState<SearchParams | null>(null);
  const [attempt, setAttempt] = useState(0);
  const search = useSearch(projectId, asked, attempt);
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft.q.trim()) return;
    setAsked({ ...draft });
    setAttempt((count) => count + 1);
  };
  const errorText = (error: unknown) => {
    const reason = reasonOf(error);
    if (reason === "regex_unsupported" || reason === "regex" || reason === "timeout")
      return t(`workspace.search.error.${reason}`);
    return describeError(error, t).title;
  };
  const format = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" });

  return (
    <div className="flex min-h-0 flex-col gap-3 p-3 text-sm">
      <form onSubmit={handleSubmit} className="flex flex-col gap-2" role="search">
        <Label htmlFor={ids.q}>{t("workspace.search.q")}</Label>
        <Input
          id={ids.q}
          value={draft.q}
          onChange={(event) => setDraft({ ...draft, q: event.target.value })}
        />
        <Label htmlFor={ids.mode}>{t("workspace.search.mode")}</Label>
        <select
          id={ids.mode}
          value={draft.mode}
          onChange={(event) =>
            setDraft({ ...draft, mode: MODES.find((mode) => mode === event.target.value) ?? "text" })
          }
          className="h-9 rounded-[var(--radius-control)] border border-line bg-surface px-2"
        >
          {MODES.map((mode) => (
            <option key={mode} value={mode}>
              {t(`workspace.search.${mode}`)}
            </option>
          ))}
        </select>
        <Label htmlFor={ids.glob}>{t("workspace.search.glob")}</Label>
        <Input
          id={ids.glob}
          value={draft.glob ?? ""}
          onChange={(event) => setDraft({ ...draft, glob: event.target.value })}
          className="font-mono"
        />
        {draft.mode === "text" ? (
          <div className="flex flex-wrap gap-3">
            <label htmlFor={ids.regex} className="flex items-center gap-1">
              <input
                id={ids.regex}
                type="checkbox"
                checked={draft.regex ?? false}
                onChange={(event) => setDraft({ ...draft, regex: event.target.checked })}
              />
              {t("workspace.search.regex")}
            </label>
            <label htmlFor={ids.ignoreCase} className="flex items-center gap-1">
              <input
                id={ids.ignoreCase}
                type="checkbox"
                checked={draft.ignoreCase ?? true}
                onChange={(event) => setDraft({ ...draft, ignoreCase: event.target.checked })}
              />
              {t("workspace.search.ignoreCase")}
            </label>
          </div>
        ) : (
          <>
            <Label htmlFor={ids.kind}>{t("workspace.search.kind")}</Label>
            <Input
              id={ids.kind}
              value={draft.kind ?? ""}
              onChange={(event) => setDraft({ ...draft, kind: event.target.value })}
            />
          </>
        )}
        <Button type="submit" size="sm" disabled={!draft.q.trim()}>
          <Search aria-hidden="true" className="size-4" />
          {t("workspace.search.submit")}
        </Button>
      </form>
      {asked === null ? null : search.isPending ? (
        <StateView state="loading" />
      ) : search.isError ? (
        <p role="alert" className="text-status-danger">
          {errorText(search.error)}
          {search.error instanceof ApiError && search.error.requestId ? (
            <span className="block font-mono text-xs text-fg-muted">
              {t("common.requestId", { id: search.error.requestId })}
            </span>
          ) : null}
        </p>
      ) : (
        <div className="flex min-h-0 flex-col gap-2">
          {asked.mode === "symbol" && !search.data.meta.indexed_at ? (
            <p className="text-fg-muted">{t("workspace.search.notIndexed")}</p>
          ) : null}
          {asked.mode === "symbol" && search.data.meta.indexed_at ? (
            <p className="text-xs text-fg-muted">
              {t("workspace.search.indexedAt", {
                time: format.format(new Date(search.data.meta.indexed_at)),
              })}
            </p>
          ) : null}
          {search.data.meta.index_truncated ? (
            <p className="text-status-warning">{t("workspace.search.indexTruncated")}</p>
          ) : null}
          {search.data.meta.truncated ? (
            <p className="text-status-warning">{t("workspace.search.truncated")}</p>
          ) : null}
          {search.data.meta.skipped ? (
            <p className="text-status-warning">
              {t("workspace.search.skipped", { count: search.data.meta.skipped })}
            </p>
          ) : null}
          {search.data.hits.length === 0 && isComplete(asked, search.data.meta) ? (
            <p className="text-fg-muted">{t("workspace.search.empty")}</p>
          ) : null}
          <ul className="min-h-0 divide-y divide-line overflow-auto">
            {search.data.hits.map((hit) => (
              <li key={`${hit.path}:${hit.line}:${hit.symbol ?? ""}`}>
                <button
                  type="button"
                  aria-label={t("workspace.search.openHit", { path: hit.path, line: hit.line })}
                  onClick={() => onOpen(hit.path, hit.line)}
                  className="flex w-full flex-col gap-0.5 py-1.5 text-left hover:bg-surface-muted"
                >
                  <span className="truncate font-mono text-xs text-brand-600">
                    {hit.path}:{hit.line}
                    {hit.symbol ? ` · ${hit.symbol}` : ""}
                  </span>
                  <span className="truncate font-mono text-xs text-fg">{hit.preview}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
