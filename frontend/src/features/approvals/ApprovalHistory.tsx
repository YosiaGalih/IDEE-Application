import {
  Ban,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Hourglass,
  Repeat,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { APPROVAL_STATUSES, type ApprovalFilter, type ApprovalStatus } from "@/api/approvals";
import { ApiError } from "@/api/errors";
import { useApprovals } from "@/api/hooks/useApprovals";
import { uxStateForError } from "@/api/uxState";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Label } from "@/components/ui/Label";
import { StateView } from "@/components/ui/StateView";
import { cn } from "@/lib/cn";
import { actionLabel } from "./approvalText";

export const HISTORY_PAGE_SIZE = 20;
type HistoryStatus = ApprovalFilter["status"];
const FILTERS: readonly HistoryStatus[] = ["all", ...APPROVAL_STATUSES];
const isFilter = (value: string): value is HistoryStatus => (FILTERS as readonly string[]).includes(value);

const LOOK: Record<
  ApprovalStatus,
  { icon: LucideIcon; tone: "success" | "neutral" | "warning" | "danger" | "info" }
> = {
  pending: { icon: Hourglass, tone: "warning" },
  approved: { icon: CheckCircle2, tone: "info" },
  consumed: { icon: CheckCircle2, tone: "success" },
  rejected: { icon: Ban, tone: "neutral" },
  expired: { icon: Clock, tone: "neutral" },
  superseded: { icon: Repeat, tone: "neutral" },
};

/**
 * Every approval in the user's projects, newest first (spec 3.1c2 Lead 5). `all` includes requests still
 * pending, so whoever asked sees that it is waiting, even without the right to decide it.
 */
export function ApprovalHistory() {
  const { t, i18n } = useTranslation();
  const headingId = useId();
  const filterId = useId();
  const [filter, setFilter] = useState<ApprovalFilter>({
    status: "all",
    page: 1,
    pageSize: HISTORY_PAGE_SIZE,
  });
  const history = useApprovals(filter);
  const when = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" });
  const meta = history.data?.meta;
  const pages = meta ? Math.max(1, Math.ceil(meta.total / meta.page_size)) : 1;

  let body: React.ReactNode;
  if (history.isPending || (history.isPlaceholderData && history.data.items.length === 0)) {
    body = <StateView state="loading" />;
  } else if (history.isError) {
    body = (
      <StateView
        state={uxStateForError(history.error)}
        requestId={history.error instanceof ApiError ? history.error.requestId : null}
        onAction={() => void history.refetch()}
      />
    );
  } else if (history.data.items.length === 0) {
    body = (
      <StateView
        state="empty"
        title={filter.status === "all" ? t("approvals.history.empty") : t("approvals.history.filteredEmpty")}
      />
    );
  } else {
    body = (
      <div
        aria-busy={history.isPlaceholderData}
        className={cn(
          "overflow-x-auto rounded-[var(--radius-card)] border border-line bg-surface transition-opacity",
          history.isPlaceholderData && "opacity-60",
        )}
      >
        <table className="w-full text-left text-sm">
          <caption className="sr-only">{t("approvals.history.caption")}</caption>
          <thead className="border-b border-line text-xs uppercase tracking-wide text-fg-muted">
            <tr>
              <th scope="col" className="px-4 py-2">
                {t("approvals.history.action")}
              </th>
              <th scope="col" className="px-4 py-2">
                {t("approvals.history.project")}
              </th>
              <th scope="col" className="px-4 py-2">
                {t("approvals.history.statusColumn")}
              </th>
              <th scope="col" className="px-4 py-2">
                {t("approvals.history.requested")}
              </th>
              <th scope="col" className="px-4 py-2">
                {t("approvals.history.decided")}
              </th>
            </tr>
          </thead>
          <tbody>
            {history.data.items.map((approval) => {
              const { icon: Icon, tone } = LOOK[approval.status];
              return (
                <tr key={approval.id} className="border-b border-line align-top last:border-0">
                  <td className="px-4 py-3">{actionLabel(t, approval.action_type)}</td>
                  <td className="break-words px-4 py-3">{approval.project_name}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      <Badge tone={tone}>
                        <Icon aria-hidden="true" className="size-3.5" />
                        {t(`approvals.status.${approval.status}`)}
                      </Badge>
                      {approval.handler_status === "failed" ? (
                        <Badge tone="danger">
                          <TriangleAlert aria-hidden="true" className="size-3.5" />
                          {t("approvals.history.handlerFailed")}
                        </Badge>
                      ) : null}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-fg-muted">
                    <time dateTime={approval.created_at}>{when.format(new Date(approval.created_at))}</time>
                    <div className="break-all font-mono text-xs">{approval.requested_by}</div>
                  </td>
                  <td className="px-4 py-3 text-fg-muted">
                    {approval.decided_at ? (
                      <time dateTime={approval.decided_at}>{when.format(new Date(approval.decided_at))}</time>
                    ) : null}
                    {approval.decision_note ? (
                      <p className="whitespace-pre-wrap break-words text-xs">
                        {t("approvals.history.note", { note: approval.decision_note })}
                      </p>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {meta && meta.total > meta.page_size ? (
          <nav
            aria-label={t("approvals.history.pagination")}
            className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2 text-sm text-fg-muted"
          >
            <span>{t("approvals.history.pageStatus", { page: meta.page, pages, total: meta.total })}</span>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={filter.page <= 1}
                onClick={() => setFilter({ ...filter, page: filter.page - 1 })}
              >
                <ChevronLeft aria-hidden="true" className="size-4" />
                {t("approvals.history.previous")}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={filter.page >= pages}
                onClick={() => setFilter({ ...filter, page: filter.page + 1 })}
              >
                {t("approvals.history.next")}
                <ChevronRight aria-hidden="true" className="size-4" />
              </Button>
            </div>
          </nav>
        ) : null}
      </div>
    );
  }

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 id={headingId} className="text-lg font-semibold text-fg">
          {t("approvals.history.heading")}
        </h2>
        <div className="space-y-1.5">
          <Label htmlFor={filterId}>{t("approvals.history.filterLabel")}</Label>
          <select
            id={filterId}
            value={filter.status}
            onChange={(event) => {
              if (isFilter(event.target.value)) setFilter({ ...filter, status: event.target.value, page: 1 });
            }}
            className="h-10 rounded-[var(--radius-control)] border border-line bg-surface px-3 text-sm text-fg"
          >
            {FILTERS.map((status) => (
              <option key={status} value={status}>
                {status === "all" ? t("approvals.history.all") : t(`approvals.status.${status}`)}
              </option>
            ))}
          </select>
        </div>
      </div>
      {body}
    </section>
  );
}
