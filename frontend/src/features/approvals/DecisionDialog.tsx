import { useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Approval } from "@/api/approvals";
import { useDecideApproval } from "@/api/hooks/useApprovals";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/Dialog";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { Label } from "@/components/ui/Label";
import { actionLabel, decisionError, decisionOutcome, type Outcome } from "./approvalText";
import { PayloadView } from "./PayloadView";

/** API §8 / DATA_MODEL: `decision_note` is at most 2000 characters (checked again by the server). */
const NOTE_MAX = 2000;

export type Decision = "approve" | "reject";
type Props = {
  approval: Approval;
  decision: Decision;
  onClose: () => void;
  onDecided: (outcome: Outcome) => void;
};

/**
 * Confirms one decision on exactly the approval shown: its payload and full hash are on screen, and the hash
 * sent is that approval's own (spec 3.1c2 Lead 4), never one computed here.
 */
export function DecisionDialog({ approval, decision, onClose, onDecided }: Props) {
  const { t } = useTranslation();
  const noteId = useId();
  const hintId = useId();
  const [note, setNote] = useState("");
  const decide = useDecideApproval();
  // A second click before React re-renders the disabled button must not send a second decision.
  const sending = useRef(false);
  const action = actionLabel(t, approval.action_type);
  const isTooLong = note.length > NOTE_MAX;
  const failure = decide.isError ? decisionError(t, decide.error) : null;

  const submit = () => {
    if (sending.current || isTooLong) return;
    sending.current = true;
    decide.mutate(
      {
        approvalId: approval.id,
        input: { decision, payload_hash: approval.payload_hash, note: note.trim() === "" ? null : note },
      },
      {
        onSuccess: (decided) => onDecided(decisionOutcome(t, decided)),
        onSettled: () => {
          sending.current = false;
        },
      },
    );
  };

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent
        className="w-[min(40rem,calc(100vw-2rem))]"
        title={t(decision === "approve" ? "approvals.dialog.approveTitle" : "approvals.dialog.rejectTitle", {
          action,
        })}
        description={t("approvals.dialog.description")}
      >
        <div className="max-h-[60vh] space-y-3 overflow-y-auto text-sm">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
            <dt className="text-fg-muted">{t("approvals.dialog.project")}</dt>
            <dd className="break-words">{approval.project_name}</dd>
            <dt className="text-fg-muted">{t("approvals.dialog.summary")}</dt>
            <dd className="break-words">{approval.summary}</dd>
            <dt className="text-fg-muted">{t("approvals.dialog.hashFull")}</dt>
            <dd className="break-all font-mono text-xs">{approval.payload_hash}</dd>
          </dl>
          <PayloadView payload={approval.payload} />
          {decision === "approve" && approval.action_type === "project_delete" ? (
            <Alert tone="warning">{t("approvals.dialog.projectDeleteWarning")}</Alert>
          ) : null}
          <div className="space-y-1.5">
            <Label htmlFor={noteId}>{t("approvals.dialog.note")}</Label>
            <textarea
              id={noteId}
              value={note}
              rows={3}
              aria-describedby={hintId}
              aria-invalid={isTooLong}
              onChange={(event) => setNote(event.target.value)}
              className="w-full rounded-[var(--radius-control)] border border-line bg-surface px-2 py-1 text-sm"
            />
            <p id={hintId} className="text-xs text-fg-muted">
              {t("approvals.dialog.noteHint")}
            </p>
            {isTooLong ? (
              <p role="alert" className="text-xs text-status-danger">
                {t("approvals.dialog.noteTooLong")}
              </p>
            ) : null}
          </div>
          {failure ? <ErrorSummary text={failure} focusKey={decide.submittedAt} /> : null}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <DialogClose asChild>
            <Button variant="secondary">{t("approvals.dialog.cancel")}</Button>
          </DialogClose>
          <Button
            variant={decision === "approve" ? "primary" : "danger"}
            disabled={decide.isPending || isTooLong || (failure?.isFinal ?? false)}
            aria-busy={decide.isPending}
            onClick={submit}
          >
            {t(decision === "approve" ? "approvals.dialog.confirmApprove" : "approvals.dialog.confirmReject")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
