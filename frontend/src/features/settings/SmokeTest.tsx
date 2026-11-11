import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/errors";
import {
  providersKey,
  SMOKE_POLL_MS,
  SMOKE_WAIT_LIMIT_MS,
  useReadiness,
  useSmokeTest,
} from "@/api/hooks/useProviders";
import type { Readiness } from "@/api/providers";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { describeError } from "@/lib/describeError";

/** A run being waited for: its job id, or none when no answer came (then any new report counts). */
type Waiting = { jobId: string | null; before: string | null; since: number };
type Run = { kind: "idle" } | { kind: "waiting"; wait: Waiting } | { kind: "finished" };
type Phase = "idle" | "running" | "finished" | "ended" | "stalled";
type Read = { data?: Readiness | undefined; dataUpdatedAt: number; errorUpdatedAt: number };

/**
 * Where the smoke stands, from the last readiness read (spec WP-3.1e2 Lead 4). Only reads made after the
 * request decide: the one cached from before would say nothing runs.
 * - Idle, but a run holds the lock (another admin, the CLI, or ours before navigating away): running.
 * - Waiting: its report landed → finished; nothing holds the lock and no new report → ended without a
 *   report; still held after the limit → stalled (a stuck lock expires on its own).
 */
function phaseOf(run: Run, read: Read): Phase {
  if (run.kind === "finished") return "finished";
  const { data } = read;
  if (run.kind === "idle") return data?.running_job_id ? "running" : "idle";
  const { jobId, before, since } = run.wait;
  // Reads that keep failing still count against the limit: their attempts end the wait too (AG-13 L1).
  const hasWaitedTooLong = Math.max(read.dataUpdatedAt, read.errorUpdatedAt) - since > SMOKE_WAIT_LIMIT_MS;
  if (data === undefined || read.dataUpdatedAt < since) return hasWaitedTooLong ? "stalled" : "running";
  const hasLanded =
    jobId !== null ? data.job_id === jobId : data.running_job_id === null && data.job_id !== before;
  if (hasLanded) return "finished";
  const isHeld = jobId !== null ? data.running_job_id === jobId : data.running_job_id !== null;
  if (!isHeld) return "ended";
  return hasWaitedTooLong ? "stalled" : "running";
}

/** No answer came, so whether the run started is unknown: wait for readiness instead of claiming either. */
const hasNoAnswer = (error: unknown) =>
  error instanceof ApiError && (error.kind === "timeout" || error.kind === "network");

/**
 * The smoke test: one paid run in the worker, never retried automatically. Never a result the server did
 * not record.
 */
type Props = { providerId: string };

export function SmokeTest({ providerId }: Props) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const smoke = useSmokeTest(providerId);
  const [run, setRun] = useState<Run>({ kind: "idle" });
  const readiness = useReadiness(providerId, (query) =>
    phaseOf(run, query.state) === "running" ? SMOKE_POLL_MS : false,
  );
  const phase = phaseOf(run, readiness);
  const isStarting = useRef(false);

  useEffect(() => {
    // The card's own verdict comes from the list: read it again once the report landed.
    if (phase === "finished") void client.invalidateQueries({ queryKey: providersKey, exact: true });
  }, [phase, client]);

  const handleStart = () => {
    if (isStarting.current) return;
    isStarting.current = true;
    const before = readiness.data?.job_id ?? null;
    setRun({ kind: "idle" });
    smoke.mutate(undefined, {
      onSuccess: (outcome) => {
        if (outcome.kind === "finished") setRun({ kind: "finished" });
        else setRun({ kind: "waiting", wait: { jobId: outcome.jobId, before, since: Date.now() } });
      },
      onError: (error) => {
        if (hasNoAnswer(error)) setRun({ kind: "waiting", wait: { jobId: null, before, since: Date.now() } });
      },
      onSettled: () => {
        isStarting.current = false;
      },
    });
  };

  const failure = !smoke.isError
    ? null
    : hasNoAnswer(smoke.error)
      ? { title: t("providers.smoke.noAnswer"), body: null, requestId: null }
      : describeError(smoke.error, t, {
          byCode: { CONFLICT: t("providers.smoke.busy"), RATE_LIMITED: t("providers.smoke.rateLimited") },
        });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={handleStart} disabled={smoke.isPending || phase === "running"}>
          {smoke.isPending ? t("providers.smoke.running") : t("providers.smoke.run")}
        </Button>
        <p className="text-sm text-fg-muted">{t("providers.smoke.cost")}</p>
      </div>
      {failure ? <ErrorSummary text={failure} focusKey={smoke.submittedAt} /> : null}
      {phase === "running" ? <Alert tone="info">{t("providers.smoke.stillRunning")}</Alert> : null}
      {phase === "running" && readiness.isError ? (
        <Alert tone="warning">
          <p>{t("providers.smoke.readFailed")}</p>
          <p className="mt-1">{describeError(readiness.error, t).title}</p>
        </Alert>
      ) : null}
      {phase === "finished" ? <Alert tone="info">{t("providers.smoke.finished")}</Alert> : null}
      {phase === "ended" ? <Alert tone="warning">{t("providers.smoke.endedWithout")}</Alert> : null}
      {phase === "stalled" ? <Alert tone="warning">{t("providers.smoke.stalled")}</Alert> : null}
    </div>
  );
}
