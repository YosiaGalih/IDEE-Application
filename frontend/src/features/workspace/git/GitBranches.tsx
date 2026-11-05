import { useQueryClient } from "@tanstack/react-query";
import { GitBranchPlus } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/errors";
import { createBranch, isReservedBranch } from "@/api/git";
import { branchesKey, useBranches } from "@/api/hooks/useWorkspace";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Spinner } from "@/components/ui/Spinner";
import { isOutcomeUnknown } from "@/lib/describeError";
import { reasonOf, workspaceErrorText } from "../workspaceErrors";
import { useRetryKey } from "./retryKey";

type Props = { projectId: string; canBranch: boolean; currentBranch: string | null };

const NAME_MAX = 255; // routes/git.py NewBranch

type BranchResult =
  | { kind: "done"; name: string; head: string }
  | { kind: "reserved" }
  | { kind: "tooLong" }
  | { kind: "exists" }
  | { kind: "invalid"; requestId: string | null }
  | { kind: "invalidFrom"; requestId: string | null }
  | { kind: "unknown" }
  | { kind: "failed"; text: string; requestId: string | null };

/** Branches of the repository; a new one is only a ref, the working tree does not move (FR-118). */
export function GitBranches({ projectId, canBranch, currentBranch }: Props) {
  const { t } = useTranslation();
  const headingId = useId();
  const branches = useBranches(projectId);
  const failure = branches.isError ? workspaceErrorText(t, branches.error) : null;
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2 border-t border-line px-3 pt-3">
      <h2 id={headingId} className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
        {t("workspace.gitPanel.branch.title")}
      </h2>
      {branches.isPending ? <Spinner /> : null}
      {failure ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-status-danger">
          {t(branches.data ? "workspace.gitPanel.branch.stale" : "workspace.gitPanel.branch.loadFailed")}{" "}
          {failure.text}
          <Button variant="ghost" size="sm" className="h-6 px-1" onClick={() => void branches.refetch()}>
            {t("workspace.retry")}
          </Button>
        </div>
      ) : null}
      {branches.data ? (
        <ul aria-label={t("workspace.gitPanel.branch.title")} className="flex flex-col gap-0.5">
          {branches.data.map((branch) => (
            <li key={branch.name} className="flex min-w-0 items-center gap-2 font-mono text-xs text-fg">
              <span className="truncate" title={branch.name}>
                {branch.name}
              </span>
              {branch.name === currentBranch ? (
                <span className="rounded border border-line px-1 font-sans text-status-success">
                  {t("workspace.gitPanel.branch.current")}
                </span>
              ) : null}
              {branch.is_task_branch ? (
                <span className="rounded border border-line px-1 font-sans text-fg-muted">
                  {t("workspace.gitPanel.branch.task")}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {canBranch ? (
        <BranchForm
          projectId={projectId}
          names={branches.data?.map((branch) => branch.name) ?? []}
          currentBranch={currentBranch}
        />
      ) : null}
    </section>
  );
}

function BranchForm({
  projectId,
  names,
  currentBranch,
}: {
  projectId: string;
  names: string[];
  currentBranch: string | null;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const nameId = useId();
  const fromId = useId();
  const retry = useRetryKey();
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [result, setResult] = useState<BranchResult | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const clean = name.trim();
    if (!clean || isBusy) return;
    // The platform's own names are refused here already: the server would refuse them the same way.
    if (isReservedBranch(clean)) {
      setResult({ kind: "reserved" });
      return;
    }
    if (clean.length > NAME_MAX) {
      setResult({ kind: "tooLong" });
      return;
    }
    const request = { name: clean, from: from === "" ? null : from };
    const body = JSON.stringify(request);
    // The branches as listed now: once the branch exists, a request is a new one (useRetryKey).
    const context = names.join("\0");
    const key = retry.keyFor(body, context);
    setIsBusy(true);
    setResult(null);
    try {
      const created = await createBranch(projectId, request, key);
      retry.settle(body, context, key, null);
      setResult({ kind: "done", name: created.name, head: created.head.slice(0, 7) });
      setName("");
    } catch (error) {
      retry.settle(body, context, key, error);
      const refused = refusalOf(error);
      if (refused) setResult(refused);
      else if (isOutcomeUnknown(error)) setResult({ kind: "unknown" });
      else setResult({ kind: "failed", ...workspaceErrorText(t, error) });
    } finally {
      setIsBusy(false);
      void queryClient.invalidateQueries({ queryKey: branchesKey(projectId) });
    }
  };

  return (
    <form onSubmit={(event) => void handleSubmit(event)} className="flex flex-col gap-2">
      <Label htmlFor={nameId}>{t("workspace.gitPanel.branch.name")}</Label>
      <Input
        id={nameId}
        value={name}
        onChange={(event) => {
          setName(event.target.value);
          setResult(null);
        }}
        className="font-mono"
      />
      <Label htmlFor={fromId}>{t("workspace.gitPanel.branch.from")}</Label>
      <select
        id={fromId}
        value={from}
        onChange={(event) => setFrom(event.target.value)}
        className="h-9 rounded-[var(--radius-control)] border border-line bg-surface px-2 font-mono text-xs"
      >
        <option value="">{t("workspace.gitPanel.branch.fromHead")}</option>
        {names.map((branch) => (
          <option key={branch} value={branch}>
            {branch}
          </option>
        ))}
      </select>
      <p className="text-xs text-fg-muted">{t("workspace.gitPanel.branch.hint")}</p>
      <Button type="submit" size="sm" disabled={isBusy || !name.trim()}>
        <GitBranchPlus aria-hidden="true" className="size-4" />
        {t("workspace.gitPanel.branch.submit")}
      </Button>
      <BranchResultText result={result} currentBranch={currentBranch} />
    </form>
  );
}

function BranchResultText({
  result,
  currentBranch,
}: {
  result: BranchResult | null;
  currentBranch: string | null;
}) {
  const { t } = useTranslation();
  if (result === null) return null;
  if (result.kind === "done")
    return (
      <p role="status" className="text-xs text-status-success">
        {currentBranch
          ? t("workspace.gitPanel.branch.done", {
              name: result.name,
              head: result.head,
              current: currentBranch,
            })
          : t("workspace.gitPanel.branch.doneNoCurrent", { name: result.name, head: result.head })}
      </p>
    );
  if (result.kind === "invalid" || result.kind === "invalidFrom")
    return (
      <p role="alert" className="text-xs text-status-danger">
        {t(
          result.kind === "invalid"
            ? "workspace.gitPanel.branch.invalid"
            : "workspace.gitPanel.branch.invalidFrom",
        )}
        {result.requestId ? (
          <span className="ml-1 font-mono">{t("common.requestId", { id: result.requestId })}</span>
        ) : null}
      </p>
    );
  if (result.kind === "failed")
    return (
      <p role="alert" className="text-xs text-status-danger">
        {t("workspace.gitPanel.branch.failed")} {result.text}
        {result.requestId ? (
          <span className="ml-1 font-mono">{t("common.requestId", { id: result.requestId })}</span>
        ) : null}
      </p>
    );
  const key = {
    reserved: "workspace.gitPanel.branch.reserved",
    tooLong: "workspace.gitPanel.branch.tooLong",
    exists: "workspace.gitPanel.branch.exists",
    unknown: "workspace.gitPanel.branch.unknownOutcome",
  } as const;
  return (
    <p role="alert" className="text-xs text-status-danger">
      {t(key[result.kind])}
    </p>
  );
}

/**
 * The backend's answers that are about the input (2.1c), told apart by what they are about:
 * - the name: 400 `reason: "branch"`, 422 on `body.name` (pydantic or a reserved name);
 * - the starting point: 400 `reason: "ref"`, 409 `GIT_ERROR reason: "unknown_ref"`, 422 on `body.from`;
 * - 409 `reason: "branch_exists"`.
 */
function refusalOf(error: unknown): BranchResult | null {
  if (!(error instanceof ApiError)) return null;
  const reason = reasonOf(error);
  const requestId = error.requestId;
  if (reason === "branch_exists") return { kind: "exists" };
  if (reason === "ref" || reason === "unknown_ref" || (error.status === 422 && isAboutFrom(error)))
    return { kind: "invalidFrom", requestId };
  if (reason === "branch" || error.status === 422) return { kind: "invalid", requestId };
  return null;
}

/** A 422 that names the starting point (`from`), not the new branch's name. */
function isAboutFrom(error: ApiError): boolean {
  if (!Array.isArray(error.details)) return false;
  return (error.details as unknown[]).some(
    (detail) =>
      typeof detail === "object" &&
      detail !== null &&
      "loc" in detail &&
      Array.isArray(detail.loc) &&
      (detail.loc as unknown[]).includes("from"),
  );
}
