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
import { describeError, isOutcomeUnknown } from "@/lib/describeError";

/**
 * A run being waited for: its job id, or none when no readable answer came (then a new report is not
 * provably ours). `isRecorded`: the server answered 200, its report exists and only the read is pending.
 */
type Waiting = { jobId: string | null; before: string | null; since: number; isRecorded: boolean };
type Run = { kind: "idle" } | { kind: "waiting"; wait: Waiting };
type Phase =
  "idle" | "running" | "loading" | "finished" | "unconfirmed" | "ended" | "noRun" | "stalled" | "unreadable";
type Read = { data?: Readiness | undefined; dataUpdatedAt: number; errorUpdatedAt: number };

/**
 * Where the smoke stands, from the last readiness read (spec WP-3.1e2 Lead 4). Only reads made after the
 * request decide: the one cached from before would say nothing runs. "Finished" only when the report on
 * screen is this run's (silent-failure 3.1e2 H2, M1).
 * - Idle, but a run holds the lock (another admin, the CLI, or ours before navigating away): running, until
 *   reads have failed for longer than the limit.
 * - Known job: its report on screen → finished; the lock held by it → running; neither → ended without a
 *   report (or, after a 200, replaced by a newer report: unconfirmed).
 * - No answer: a run holds the lock → running; a report newer than the one shown at the click →
 *   unconfirmed (whose run is not known); neither → the request may not have arrived.
 * - Past the limit: stalled, or unreadable when the reads themselves fail (the client cannot tell).
 */
function phaseOf(run: Run, read: Read): Phase {
  const { data } = read;
  if (run.kind === "idle") {
    if (!data?.running_job_id) return "idle";
    // A run we did not start is read while it holds the lock, but not forever once reads fail (AG-13 delta LOW-1).
    return read.errorUpdatedAt - read.dataUpdatedAt > SMOKE_WAIT_LIMIT_MS ? "unreadable" : "running";
  }
  const { jobId, before, since, isRecorded } = run.wait;
  const pending: Phase = isRecorded ? "loading" : "running";
  // Reads that keep failing still count against the limit: their attempts end the wait too (AG-13 L1).
  const hasWaitedTooLong = Math.max(read.dataUpdatedAt, read.errorUpdatedAt) - since > SMOKE_WAIT_LIMIT_MS;
  const giveUp: Phase = read.errorUpdatedAt > read.dataUpdatedAt ? "unreadable" : "stalled";
  if (data === undefined || read.dataUpdatedAt < since) return hasWaitedTooLong ? giveUp : pending;
  if (jobId !== null) {
    if (data.job_id === jobId) return "finished";
    if (data.running_job_id === jobId) return hasWaitedTooLong ? giveUp : pending;
    return isRecorded ? "unconfirmed" : "ended";
  }
  if (data.running_job_id !== null) return hasWaitedTooLong ? giveUp : "running";
  return data.job_id !== before ? "unconfirmed" : "noRun";
}

const POLLING: ReadonlySet<Phase> = new Set(["running", "loading"]);
const REPORT_LANDED: ReadonlySet<Phase> = new Set(["finished", "unconfirmed"]);

/**
 * No readable answer (network, timeout, a gateway page or a 2xx that is not the envelope): whether the run
 * started is unknown, so readiness decides instead of claiming either (silent-failure 3.1e2 M3, M4). The
 * API's own error envelopes (409 busy, 429, 503) are answers.
 */
const hasNoAnswer = (error: unknown) =>
  isOutcomeUnknown(error) && !(error instanceof ApiError && error.kind === "http");

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
    POLLING.has(phaseOf(run, query.state)) ? SMOKE_POLL_MS : false,
  );
  const phase = phaseOf(run, readiness);
  const isStarting = useRef(false);

  useEffect(() => {
    // The card's own verdict comes from the list: read it again once a report landed.
    if (REPORT_LANDED.has(phase)) void client.invalidateQueries({ queryKey: providersKey, exact: true });
  }, [phase, client]);

  const handleStart = () => {
    if (isStarting.current) return;
    isStarting.current = true;
    const before = readiness.data?.job_id ?? null;
    setRun({ kind: "idle" });
    smoke.mutate(undefined, {
      onSuccess: (outcome) => {
        // A 200 too waits for the read that shows its report: the panel below must be this run's.
        const isRecorded = outcome.kind === "finished";
        setRun({ kind: "waiting", wait: { jobId: outcome.jobId, before, since: Date.now(), isRecorded } });
      },
      onError: (error) => {
        if (hasNoAnswer(error)) {
          setRun({ kind: "waiting", wait: { jobId: null, before, since: Date.now(), isRecorded: false } });
        }
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
        {/* Off until readiness is read: the report shown at the click tells a new one apart (AG-13 delta
            LOW-2); off while a run is read, its own lock already released after a 200 included. */}
        <Button
          onClick={handleStart}
          disabled={smoke.isPending || POLLING.has(phase) || readiness.data === undefined}
        >
          {smoke.isPending ? t("providers.smoke.running") : t("providers.smoke.run")}
        </Button>
        <p className="text-sm text-fg-muted">{t("providers.smoke.cost")}</p>
      </div>
      {failure ? <ErrorSummary text={failure} focusKey={smoke.submittedAt} /> : null}
      {phase === "running" ? <Alert tone="info">{t("providers.smoke.stillRunning")}</Alert> : null}
      {phase === "loading" ? <Alert tone="info">{t("providers.smoke.loading")}</Alert> : null}
      {POLLING.has(phase) && readiness.isError ? (
        <Alert tone="warning">
          <p>{t("providers.smoke.readFailed")}</p>
          <p className="mt-1">{describeError(readiness.error, t).title}</p>
        </Alert>
      ) : null}
      {phase === "finished" ? <Alert tone="info">{t("providers.smoke.finished")}</Alert> : null}
      {phase === "unconfirmed" ? <Alert tone="warning">{t("providers.smoke.unconfirmed")}</Alert> : null}
      {phase === "ended" ? <Alert tone="warning">{t("providers.smoke.endedWithout")}</Alert> : null}
      {phase === "noRun" ? <Alert tone="warning">{t("providers.smoke.noRun")}</Alert> : null}
      {phase === "stalled" || phase === "unreadable" ? (
        <Alert tone="warning">
          <p>{t(phase === "stalled" ? "providers.smoke.stalled" : "providers.smoke.unreadable")}</p>
          <Button variant="secondary" size="sm" className="mt-2" onClick={() => void readiness.refetch()}>
            {t("providers.smoke.recheck")}
          </Button>
        </Alert>
      ) : null}
    </div>
  );
}
