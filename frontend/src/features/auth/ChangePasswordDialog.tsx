import { useMutation } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { changePassword } from "@/api/auth";
import { ApiError } from "@/api/errors";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/Dialog";
import { FormField } from "@/components/ui/FormField";
import { Spinner } from "@/components/ui/Spinner";
import { zodResolver } from "@/lib/zodResolver";
import { describeError } from "./describeError";
import { ErrorSummary } from "./ErrorSummary";
import { changeSchema, type ChangeValues } from "./schemas";
import { useFieldError } from "./useFieldError";

type Props = {
  /** Signs out and goes to /login (after a 409: the password changed elsewhere). */
  onSignOut: () => void;
};

/** Opened from the user menu. The form lives inside the content, so closing the dialog forgets it. */
export function ChangePasswordDialog({ onSignOut }: Props) {
  const { t } = useTranslation();
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm">
          <KeyRound aria-hidden="true" className="size-4" />
          {t("auth.change.open")}
        </Button>
      </DialogTrigger>
      <DialogContent title={t("auth.change.title")} description={t("auth.change.description")}>
        <ChangePasswordForm onSignOut={onSignOut} />
      </DialogContent>
    </Dialog>
  );
}

function ChangePasswordForm({ onSignOut }: Props) {
  const { t } = useTranslation();
  const fieldError = useFieldError();
  const {
    register,
    handleSubmit,
    formState: { errors, submitCount },
  } = useForm<ChangeValues>({
    resolver: zodResolver(changeSchema),
    defaultValues: { currentPassword: "", newPassword: "", confirmPassword: "" },
    shouldFocusError: false,
  });
  const mutation = useMutation({
    mutationFn: (values: ChangeValues) => changePassword(values.currentPassword, values.newPassword),
  });

  if (mutation.isSuccess) {
    return (
      <div className="space-y-4">
        <Alert tone="success">{t("auth.change.done")}</Alert>
        <div className="flex justify-end">
          <DialogClose asChild>
            <Button>{t("auth.change.close")}</Button>
          </DialogClose>
        </div>
      </div>
    );
  }

  const isConflict = mutation.error instanceof ApiError && mutation.error.status === 409;
  const handleFormSubmit = (event: React.FormEvent<HTMLFormElement>) =>
    void handleSubmit(
      (values) => mutation.mutate(values),
      () => mutation.reset(),
    )(event);
  const summary = mutation.isError
    ? describeError(mutation.error, t, { 403: t("auth.change.wrongCurrent"), 409: t("auth.change.conflict") })
    : Object.keys(errors).length > 0
      ? { title: t("auth.errorSummary"), body: null, requestId: null }
      : null;

  return (
    <form noValidate onSubmit={handleFormSubmit} className="space-y-4">
      {summary ? <ErrorSummary text={summary} focusKey={submitCount} /> : null}
      <FormField
        id="change-current"
        label={t("auth.change.current")}
        type="password"
        autoComplete="current-password"
        error={fieldError(errors.currentPassword?.message)}
        {...register("currentPassword")}
      />
      <FormField
        id="change-new"
        label={t("auth.reset.newPassword")}
        type="password"
        autoComplete="new-password"
        error={fieldError(errors.newPassword?.message)}
        {...register("newPassword")}
      />
      <FormField
        id="change-confirm"
        label={t("auth.reset.confirmPassword")}
        type="password"
        autoComplete="new-password"
        error={fieldError(errors.confirmPassword?.message)}
        {...register("confirmPassword")}
      />
      <div className="flex justify-end gap-2">
        <DialogClose asChild>
          <Button variant="secondary">{t("auth.change.cancel")}</Button>
        </DialogClose>
        {isConflict ? (
          <Button onClick={onSignOut}>{t("auth.change.signInAgain")}</Button>
        ) : (
          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? <Spinner className="size-4 text-white" /> : null}
            {mutation.isPending ? t("auth.change.submitting") : t("auth.change.submit")}
          </Button>
        )}
      </div>
    </form>
  );
}
