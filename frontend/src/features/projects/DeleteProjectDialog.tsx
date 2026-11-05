import type { TFunction } from "i18next";
import { Trash2 } from "lucide-react";
import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/errors";
import { useRequestProjectDelete } from "@/api/hooks/useApprovals";
import type { Project } from "@/api/projects";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/Dialog";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { describeError, isOutcomeUnknown, type ErrorText } from "@/lib/describeError";

type Props = { project: Project; onClose: () => void; onRequested: (project: Project) => void };

const reasonOf = (details: unknown): string | null => {
  const first: unknown = Array.isArray(details) ? details[0] : null;
  return typeof first === "object" && first !== null && "reason" in first ? String(first.reason) : null;
};

/** Why the request was refused, in this screen's words (spec WP-3.1c Goal 5, 3.1c2 Lead 7). */
function requestError(t: TFunction, error: unknown): ErrorText {
  const api = error instanceof ApiError ? error : null;
  if (api?.kind === "http" && api.code === "INVALID_TRANSITION") {
    const failedBefore = reasonOf(api.details) === "delete_failed";
    return {
      title: t(failedBefore ? "projects.delete.failedBefore" : "projects.delete.notDeletable"),
      body: null,
      requestId: api.requestId,
    };
  }
  if (isOutcomeUnknown(error)) {
    return { title: t("projects.delete.outcomeUnknown"), body: null, requestId: api?.requestId ?? null };
  }
  return describeError(error, t);
}

/**
 * Asks for a project delete (R3). Nothing is deleted here: the server opens a `project_delete` approval
 * that a person decides on /approvals.
 */
export function DeleteProjectDialog({ project, onClose, onRequested }: Props) {
  const { t } = useTranslation();
  const request = useRequestProjectDelete();
  // A second click before the button re-renders disabled must not send a second request.
  const sending = useRef(false);

  const submit = () => {
    if (sending.current) return;
    sending.current = true;
    request.mutate(
      { projectId: project.id },
      {
        onSuccess: (result) => onRequested(result.project),
        onSettled: () => {
          sending.current = false;
        },
      },
    );
  };

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent
        title={t("projects.delete.title", { name: project.name })}
        description={t("projects.delete.explain")}
      >
        {request.isError ? (
          <div className="mb-4">
            <ErrorSummary text={requestError(t, request.error)} focusKey={request.submittedAt} />
          </div>
        ) : null}
        <div className="flex justify-end gap-2">
          <DialogClose asChild>
            <Button variant="secondary">{t("projects.delete.cancel")}</Button>
          </DialogClose>
          <Button
            variant="danger"
            disabled={request.isPending}
            aria-busy={request.isPending}
            onClick={submit}
          >
            <Trash2 aria-hidden="true" className="size-4" />
            {t("projects.delete.confirm")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
