import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";
import { resetPassword } from "@/api/auth";
import { ApiError } from "@/api/errors";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { FormField } from "@/components/ui/FormField";
import { Spinner } from "@/components/ui/Spinner";
import { AUTH_PATHS } from "@/lib/safeNext";
import { zodResolver } from "@/lib/zodResolver";
import { AuthLayout } from "./AuthLayout";
import { describeError } from "./describeError";
import { ErrorSummary } from "./ErrorSummary";
import { resetSchema, type ResetValues } from "./schemas";
import { useFieldError } from "./useFieldError";

/** backend ResetIn.token: at most 128 characters. */
const MAX_TOKEN_LENGTH = 128;
const LINK_CLASS = "text-sm font-medium text-brand-600 hover:underline";

/**
 * `/reset-password/:token?`. The token is kept in state and the URL is replaced with `/reset-password`
 * at once, so it stays out of the address bar and the history (one route: the state survives).
 */
export function ResetPasswordPage() {
  const { t } = useTranslation();
  const fieldError = useFieldError();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { token: tokenInUrl } = useParams();
  const [token] = useState<string | null>(() => tokenInUrl ?? null);
  useEffect(() => {
    if (tokenInUrl !== undefined) void navigate(AUTH_PATHS.resetPassword, { replace: true });
  }, [tokenInUrl, navigate]);

  const {
    register,
    handleSubmit,
    formState: { errors, submitCount },
  } = useForm<ResetValues>({
    resolver: zodResolver(resetSchema),
    defaultValues: { newPassword: "", confirmPassword: "" },
    shouldFocusError: false,
  });
  const mutation = useMutation({
    mutationFn: (values: ResetValues) => resetPassword(token ?? "", values.newPassword),
    onSuccess: () => {
      queryClient.removeQueries(); // the server signed every session out
      void navigate(AUTH_PATHS.login, { replace: true, state: { notice: "passwordReset" } });
    },
  });

  const isRefused = mutation.error instanceof ApiError && mutation.error.status === 400;
  if (token === null || token.length > MAX_TOKEN_LENGTH || isRefused) {
    return (
      <AuthLayout title={t("auth.reset.title")}>
        <Alert tone={token === null ? "info" : "danger"}>
          {token === null ? t("auth.reset.noToken") : t("auth.reset.invalidLink")}
        </Alert>
        <Link to={AUTH_PATHS.forgotPassword} className={LINK_CLASS}>
          {t("auth.reset.requestNew")}
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
    <AuthLayout title={t("auth.reset.title")} subtitle={t("auth.reset.subtitle")}>
      {summary ? <ErrorSummary text={summary} focusKey={submitCount} /> : null}
      <form noValidate onSubmit={handleFormSubmit} className="space-y-4">
        <FormField
          id="reset-new"
          label={t("auth.reset.newPassword")}
          type="password"
          autoComplete="new-password"
          error={fieldError(errors.newPassword?.message)}
          {...register("newPassword")}
        />
        <FormField
          id="reset-confirm"
          label={t("auth.reset.confirmPassword")}
          type="password"
          autoComplete="new-password"
          error={fieldError(errors.confirmPassword?.message)}
          {...register("confirmPassword")}
        />
        <Button type="submit" className="w-full" disabled={mutation.isPending}>
          {mutation.isPending ? <Spinner className="size-4 text-white" /> : null}
          {mutation.isPending ? t("auth.reset.submitting") : t("auth.reset.submit")}
        </Button>
      </form>
    </AuthLayout>
  );
}
