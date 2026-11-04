import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogContent } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { workspaceErrorText } from "./workspaceErrors";

type Failure = { text: string; requestId: string | null };

function FailureText({ failure }: { failure: Failure }) {
  const { t } = useTranslation();
  return (
    <p role="alert" className="text-sm text-status-danger">
      {failure.text}
      {failure.requestId ? (
        <span className="ml-1 font-mono text-xs">{t("common.requestId", { id: failure.requestId })}</span>
      ) : null}
    </p>
  );
}

type ConfirmProps = {
  title: string;
  body: string;
  confirm: string;
  cancel: string;
  onConfirm: () => void;
  onCancel: () => void;
};

export function ConfirmDialog({ title, body, confirm, cancel, onConfirm, onCancel }: ConfirmProps) {
  return (
    <Dialog open onOpenChange={(isOpen) => !isOpen && onCancel()}>
      <DialogContent title={title} description={body}>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>
            {cancel}
          </Button>
          <Button variant="danger" onClick={onConfirm}>
            {confirm}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

type CreateProps = { type: "file" | "dir"; onCancel: () => void; onCreate: (path: string) => Promise<void> };

export function CreateDialog({ type, onCancel, onCreate }: CreateProps) {
  const { t } = useTranslation();
  const inputId = useId();
  const [path, setPath] = useState("");
  const [failure, setFailure] = useState<Failure | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const clean = path.trim().replace(/^\/+/, "");
    if (!clean) return;
    setIsBusy(true);
    setFailure(null);
    try {
      await onCreate(clean);
    } catch (caught) {
      const { text, requestId } = workspaceErrorText(t, caught, {
        // The API's "path exists" is a 409 without a reason, as is a request with this key still running.
        byCode: { CONFLICT: t("workspace.create.exists") },
        unknownOutcome: t("workspace.create.unknownOutcome"),
      });
      setFailure({ text: `${t("workspace.create.failed", { path: clean })} ${text}`, requestId });
      setIsBusy(false);
    }
  };
  return (
    <Dialog open onOpenChange={(isOpen) => !isOpen && onCancel()}>
      <DialogContent
        title={t(type === "file" ? "workspace.create.titleFile" : "workspace.create.titleFolder")}
      >
        <form onSubmit={(event) => void handleSubmit(event)} className="flex flex-col gap-2">
          <Label htmlFor={inputId}>{t("workspace.create.path")}</Label>
          <Input
            id={inputId}
            value={path}
            onChange={(event) => setPath(event.target.value)}
            className="font-mono"
          />
          {failure ? <FailureText failure={failure} /> : null}
          <div className="mt-2 flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onCancel}>
              {t("workspace.create.cancel")}
            </Button>
            <Button type="submit" disabled={isBusy || !path.trim()}>
              {t("workspace.create.submit")}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

type DeleteProps = {
  path: string;
  /** The file is open with edits that were not saved: they go with it. */
  hasUnsaved: boolean;
  onCancel: () => void;
  onDelete: () => Promise<void>;
};

export function DeleteDialog({ path, hasUnsaved, onCancel, onDelete }: DeleteProps) {
  const { t } = useTranslation();
  const [failure, setFailure] = useState<Failure | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const handleDelete = async () => {
    setIsBusy(true);
    setFailure(null);
    try {
      await onDelete();
    } catch (caught) {
      const { text, requestId } = workspaceErrorText(t, caught, {
        byCode: { VERSION_CONFLICT: t("workspace.remove.changed") },
        unknownOutcome: t("workspace.remove.unknownOutcome"),
      });
      setFailure({ text: `${t("workspace.remove.failed", { path })} ${text}`, requestId });
      setIsBusy(false);
    }
  };
  return (
    <Dialog open onOpenChange={(isOpen) => !isOpen && onCancel()}>
      <DialogContent title={t("workspace.remove.title")} description={t("workspace.remove.body", { path })}>
        {hasUnsaved ? <p className="text-sm text-status-warning">{t("workspace.remove.unsaved")}</p> : null}
        {failure ? <FailureText failure={failure} /> : null}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>
            {t("workspace.remove.cancel")}
          </Button>
          <Button variant="danger" disabled={isBusy} onClick={() => void handleDelete()}>
            {t("workspace.remove.submit")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
