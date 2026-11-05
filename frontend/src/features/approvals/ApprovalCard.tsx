import { Ban, Check, Hourglass, ShieldAlert } from "lucide-react";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { Approval } from "@/api/approvals";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { actionLabel, riskText, summaryText } from "./approvalText";
import type { Decision } from "./DecisionDialog";
import { PayloadView } from "./PayloadView";

type Props = { approval: Approval; onDecide: (approval: Approval, decision: Decision) => void };

/** One pending approval (mockup screen-11): what would run, bound to which hash, asked by whom, until when. */
export function ApprovalCard({ approval, onDecide }: Props) {
  const { t, i18n } = useTranslation();
  const titleId = useId();
  // "long" time includes the zone: an expiry is a deadline, so it says which clock it is on.
  const when = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "long" });
  const isHighRisk = approval.risk_level >= 3;
  return (
    <article
      aria-labelledby={titleId}
      className="overflow-hidden rounded-[var(--radius-card)] border border-status-warning/40 bg-surface shadow-sm"
    >
      <header className="flex flex-wrap items-center justify-between gap-2 bg-status-warning-bg px-4 py-3">
        <h4 id={titleId} className="flex items-center gap-2 font-semibold text-status-warning">
          <Hourglass aria-hidden="true" className="size-4" />
          {t("approvals.card.required", { action: actionLabel(t, approval.action_type) })}
        </h4>
        <Badge tone={isHighRisk ? "warning" : "info"}>
          <ShieldAlert aria-hidden="true" className="size-3.5" />
          {riskText(t, approval.risk_level)}
        </Badge>
      </header>
      <div className="space-y-3 px-4 py-3 text-sm">
        <p className="break-words text-fg">{summaryText(t, approval)}</p>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
          <dt className="text-fg-muted">{t("approvals.card.hash")}</dt>
          <dd className="font-mono" title={approval.payload_hash}>
            {t("approvals.card.shortHash", { hash: approval.payload_hash.slice(0, 12) })}
          </dd>
          <dt className="text-fg-muted">{t("approvals.card.requestedBy")}</dt>
          <dd className="break-all font-mono">{approval.requested_by}</dd>
          <dt className="text-fg-muted">{t("approvals.card.requestedAt")}</dt>
          <dd>
            <time dateTime={approval.created_at}>{when.format(new Date(approval.created_at))}</time>
          </dd>
          <dt className="text-fg-muted">{t("approvals.card.expires")}</dt>
          <dd>
            {approval.expires_at ? (
              <time dateTime={approval.expires_at}>{when.format(new Date(approval.expires_at))}</time>
            ) : (
              t("approvals.card.noExpiry")
            )}
          </dd>
        </dl>
        <PayloadView payload={approval.payload} />
        <p className="font-medium text-fg">{t("approvals.card.bound")}</p>
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="primary" onClick={() => onDecide(approval, "approve")}>
            <Check aria-hidden="true" className="size-4" />
            {t("approvals.card.approve")}
          </Button>
          <Button variant="secondary" onClick={() => onDecide(approval, "reject")}>
            <Ban aria-hidden="true" className="size-4" />
            {t("approvals.card.reject")}
          </Button>
        </div>
      </div>
    </article>
  );
}
