import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/errors";
import { SMOKE_POLL_MS, useReadiness, useSmokeTest } from "@/api/hooks/useProviders";
import type { Readiness } from "@/api/providers";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { describeError } from "@/lib/describeError";

type Run = { kind: "idle" } | { kind: "running"; jobId: string; since: number } | { kind: "finished" };
type Phase = "idle" | "running" | "finished" | "ended";

/**
 * Where a run that answered 202 stands, from a readiness read: only reads made after the 202 decide (the one
 * cached from before would say nothing runs). Its report landed, it still holds the lock, or neither: it
 * ended without a report.
 */
function settle(run: Run, data: Readiness | undefined, updatedAt: number): Phase {
  if (run.kind !== "running") return run.kind;
  if (data === undefined || updatedAt < run.since) return "running";
  if (data.job_id === run.jobId) return "finished";
  return data.running_job_id === run.jobId ? "running" : "ended";
}

/**
 * The smoke test (spec WP-3.1e2 Lead 4): one paid run in the worker, never retried. A 202 is "still running":
 * readiness is read again until the report of that job lands, or the job neither runs nor reported (it ended
 * without a report). Never a result the server did not record.
 */
export function SmokeTest({ providerId }: { providerId: string }) {
  const { t } = useTranslation();
  const smoke = useSmokeTest(providerId);
  const [run, setRun] = useState<Run>({ kind: "idle" });
  const readiness = useReadiness(providerId, (query) =>
    settle(run, query.state.data, query.state.dataUpdatedAt) === "running" ? SMOKE_POLL_MS : false,
  );
  const phase = settle(run, readiness.data, readiness.dataUpdatedAt);
  const busy = useRef(false);

  const start = () => {
    if (busy.current) return;
    busy.current = true;
    setRun({ kind: "idle" });
    smoke.mutate(undefined, {
      onSuccess: (outcome) => {
        if (outcome.kind === "finished") setRun({ kind: "finished" });
        else setRun({ kind: "running", jobId: outcome.jobId, since: Date.now() });
      },
      onSettled: () => {
        busy.current = false;
      },
    });
  };

  const failure =
    smoke.isError && smoke.error instanceof ApiError && smoke.error.kind === "timeout"
      ? { title: t("providers.smoke.timeout"), body: null, requestId: null }
      : smoke.isError
        ? describeError(smoke.error, t, {
            byCode: { CONFLICT: t("providers.smoke.busy"), RATE_LIMITED: t("providers.smoke.rateLimited") },
          })
        : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={start} disabled={smoke.isPending || phase === "running"}>
          {smoke.isPending ? t("providers.smoke.running") : t("providers.smoke.run")}
        </Button>
        <p className="text-sm text-fg-muted">{t("providers.smoke.cost")}</p>
      </div>
      {failure ? <ErrorSummary text={failure} focusKey={smoke.submittedAt} /> : null}
      {phase === "running" ? <Alert tone="info">{t("providers.smoke.stillRunning")}</Alert> : null}
      {phase === "finished" ? <Alert tone="success">{t("providers.smoke.finished")}</Alert> : null}
      {phase === "ended" ? <Alert tone="warning">{t("providers.smoke.endedWithout")}</Alert> : null}
    </div>
  );
}
