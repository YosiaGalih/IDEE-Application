import { useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Approval } from "@/api/approvals";
import { ApiError } from "@/api/errors";
import { useApprovals } from "@/api/hooks/useApprovals";
import { uxStateForError } from "@/api/uxState";
import { Alert } from "@/components/ui/Alert";
import { StateView } from "@/components/ui/StateView";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { ApprovalCard } from "./ApprovalCard";
import { ApprovalHistory } from "./ApprovalHistory";
import type { Outcome } from "./approvalText";
import { DecisionDialog, type Decision } from "./DecisionDialog";

/** Pending approvals are few by nature; one page this large shows them all (spec 3.1c2 Lead 2). */
export const PENDING_PAGE_SIZE = 200;

type Group = { projectId: string; projectName: string; approvals: Approval[] };

/** By project, in the server's order (soonest expiry first): a project appears where its first item is. */
function byProject(approvals: Approval[]): Group[] {
  const groups = new Map<string, Group>();
  for (const approval of approvals) {
    const group = groups.get(approval.project_id) ?? {
      projectId: approval.project_id,
      projectName: approval.project_name,
      approvals: [],
    };
    group.approvals.push(approval);
    groups.set(approval.project_id, group);
  }
  return [...groups.values()];
}

/** `/approvals` (mockup screen-11): a person decides R3 actions here; an agent never can (FR-063, FR-067). */
export function ApprovalsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("screens.approvals"));
  const headingRef = useRef<HTMLHeadingElement>(null);
  const pendingHeadingId = useId();
  const [selected, setSelected] = useState<{ approval: Approval; decision: Decision } | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const pending = useApprovals({ status: "pending", page: 1, pageSize: PENDING_PAGE_SIZE });

  const handleDecided = (result: Outcome) => {
    setSelected(null);
    setOutcome(result);
    headingRef.current?.focus(); // the card and its buttons are gone
  };

  let pendingBody: React.ReactNode;
  if (pending.isPending) {
    pendingBody = <StateView state="loading" />;
  } else if (pending.isError) {
    pendingBody = (
      <StateView
        state={uxStateForError(pending.error)}
        requestId={pending.error instanceof ApiError ? pending.error.requestId : null}
        onAction={() => void pending.refetch()}
      />
    );
  } else if (pending.data.items.length === 0) {
    pendingBody = (
      <StateView
        state="empty"
        title={t("approvals.pendingEmpty.title")}
        detail={t("approvals.pendingEmpty.body")}
      />
    );
  } else {
    const { items, meta } = pending.data;
    pendingBody = (
      <div className="space-y-5">
        {meta.total > items.length ? (
          <Alert tone="info">
            {t("approvals.pendingTruncated", { shown: items.length, total: meta.total })}
          </Alert>
        ) : null}
        {byProject(items).map((group) => (
          <ProjectGroup key={group.projectId} group={group}>
            {group.approvals.map((approval) => (
              <ApprovalCard
                key={approval.id}
                approval={approval}
                onDecide={(chosen, decision) => {
                  setOutcome(null);
                  setSelected({ approval: chosen, decision });
                }}
              />
            ))}
          </ProjectGroup>
        ))}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="space-y-1">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-2xl font-semibold text-fg focus:outline-none"
          data-testid="screen-title"
        >
          {t("screens.approvals")}
        </h1>
        <p className="text-fg-muted">{t("approvals.subtitle")}</p>
      </div>
      {outcome ? <Alert tone={outcome.tone}>{outcome.text}</Alert> : null}
      <section aria-labelledby={pendingHeadingId} className="space-y-3">
        <h2 id={pendingHeadingId} className="text-lg font-semibold text-fg">
          {pending.data
            ? t("approvals.pendingHeading", { count: pending.data.meta.total })
            : t("approvals.pendingTitle")}
        </h2>
        {pendingBody}
      </section>
      <ApprovalHistory />
      {selected ? (
        <DecisionDialog
          // A new approval or decision starts a fresh dialog (note, error), never the previous one's.
          key={`${selected.approval.id}:${selected.decision}`}
          approval={selected.approval}
          decision={selected.decision}
          onClose={() => setSelected(null)}
          onDecided={handleDecided}
        />
      ) : null}
    </div>
  );
}

function ProjectGroup({ group, children }: { group: Group; children: React.ReactNode }) {
  const headingId = useId();
  return (
    <div role="group" aria-labelledby={headingId} className="space-y-2">
      <h3 id={headingId} className="text-sm font-medium text-fg-muted">
        {group.projectName}
      </h3>
      {children}
    </div>
  );
}
