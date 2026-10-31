import { useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { requestPasswordReset } from "@/api/auth";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { FormField } from "@/components/ui/FormField";
import { Spinner } from "@/components/ui/Spinner";
import { AUTH_PATHS } from "@/lib/safeNext";
import { zodResolver } from "@/lib/zodResolver";
import { AuthLayout } from "./AuthLayout";
import { describeError } from "./describeError";
import { ErrorSummary } from "./ErrorSummary";
import { forgotSchema, type ForgotValues } from "./schemas";
import { useFieldError } from "./useFieldError";

const LINK_CLASS = "text-sm font-medium text-brand-600 hover:underline";

/**
 * The answer is the same whether or not the email is registered (the API always says 200). A 429 is
 * shown as it is: the limit is per IP, so it reveals nothing, and claiming "sent" would be false.
 */
export function ForgotPasswordPage() {
  const { t } = useTranslation();
  const fieldError = useFieldError();
  const {
    register,
    handleSubmit,
    formState: { errors, submitCount },
  } = useForm<ForgotValues>({
    resolver: zodResolver(forgotSchema),
    defaultValues: { email: "" },
    shouldFocusError: false,
  });
  const mutation = useMutation({ mutationFn: (values: ForgotValues) => requestPasswordReset(values.email) });

  if (mutation.isSuccess) {
    return (
      <AuthLayout title={t("auth.forgot.title")}>
        <Alert tone="success">{t("auth.forgot.sent")}</Alert>
        <Link to={AUTH_PATHS.login} className={LINK_CLASS}>
          {t("auth.forgot.backToLogin")}
        </Link>
      </AuthLayout>
    );
  }

  const handleFormSubmit = (event: React.FormEvent<HTMLFormElement>) =>
    void handleSubmit(
      (values) => mutation.mutate(values),
      () => mutation.reset(),
    )(event);
  const summary = mutation.isError
    ? describeError(mutation.error, t)
    : Object.keys(errors).length > 0
      ? { title: t("auth.errorSummary"), body: null, requestId: null }
      : null;

  return (
    <AuthLayout title={t("auth.forgot.title")} subtitle={t("auth.forgot.subtitle")}>
      {summary ? <ErrorSummary text={summary} focusKey={submitCount} /> : null}
      <form noValidate onSubmit={handleFormSubmit} className="space-y-4">
        <FormField
          id="forgot-email"
          label={t("auth.email")}
          type="email"
          autoComplete="username"
          error={fieldError(errors.email?.message)}
          {...register("email")}
        />
        <Button type="submit" className="w-full" disabled={mutation.isPending}>
          {mutation.isPending ? <Spinner className="size-4 text-white" /> : null}
          {mutation.isPending ? t("auth.forgot.submitting") : t("auth.forgot.submit")}
        </Button>
      </form>
      <Link to={AUTH_PATHS.login} className={LINK_CLASS}>
        {t("auth.forgot.backToLogin")}
      </Link>
    </AuthLayout>
  );
}
