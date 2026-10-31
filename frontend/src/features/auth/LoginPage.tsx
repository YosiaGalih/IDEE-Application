import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router";
import { login } from "@/api/auth";
import { HANDLES_UNAUTHENTICATED } from "@/api/queryClient";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { FormField } from "@/components/ui/FormField";
import { Spinner } from "@/components/ui/Spinner";
import { AUTH_PATHS, safeNext } from "@/lib/safeNext";
import { zodResolver } from "@/lib/zodResolver";
import { AuthLayout } from "./AuthLayout";
import { describeError } from "./describeError";
import { ErrorSummary } from "./ErrorSummary";
import { loginSchema, type LoginValues } from "./schemas";
import { useFieldError } from "./useFieldError";

/** Set by the reset page in the navigation state (never in the URL). */
const isResetNotice = (state: unknown) =>
  typeof state === "object" && state !== null && "notice" in state && state.notice === "passwordReset";

export function LoginPage() {
  const { t } = useTranslation();
  const fieldError = useFieldError();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const queryClient = useQueryClient();
  const next = safeNext(params.get("next"));

  // This page means there is no session in this tab: forget the last one's data, so Back cannot show it.
  useEffect(() => {
    queryClient.removeQueries();
  }, [queryClient]);

  const {
    register,
    handleSubmit,
    formState: { errors, submitCount },
  } = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "", rememberMe: false },
    shouldFocusError: false, // the summary takes focus instead
  });
  const mutation = useMutation({
    mutationFn: login,
    meta: HANDLES_UNAUTHENTICATED, // a 401 here is wrong credentials, not an expired session
    onSuccess: () => {
      queryClient.removeQueries(); // whatever was cached belongs to whoever used the tab before
      void navigate(next, { replace: true });
    },
  });

  // The submit button is disabled while pending, which also blocks Enter (implicit submission).
  const handleFormSubmit = (event: React.FormEvent<HTMLFormElement>) =>
    void handleSubmit(
      (values) => mutation.mutate(values),
      () => mutation.reset(),
    )(event);

  const hasFieldErrors = Object.keys(errors).length > 0;
  const summary = mutation.isError
    ? describeError(mutation.error, t, { 401: t("auth.login.invalid") })
    : hasFieldErrors
      ? { title: t("auth.errorSummary"), body: null, requestId: null }
      : null;

  return (
    <AuthLayout title={t("auth.login.title")} subtitle={t("auth.login.subtitle")}>
      {isResetNotice(location.state) ? <Alert tone="success">{t("auth.login.resetDone")}</Alert> : null}
      {summary ? <ErrorSummary text={summary} focusKey={submitCount} /> : null}
      <form noValidate onSubmit={handleFormSubmit} className="space-y-4">
        <FormField
          id="login-email"
          label={t("auth.email")}
          type="email"
          autoComplete="username"
          error={fieldError(errors.email?.message)}
          {...register("email")}
        />
        <FormField
          id="login-password"
          label={t("auth.password")}
          type="password"
          autoComplete="current-password"
          error={fieldError(errors.password?.message)}
          {...register("password")}
        />
        <div className="flex items-center justify-between gap-3">
          <label htmlFor="login-remember" className="flex items-center gap-2 text-sm text-fg">
            <input id="login-remember" type="checkbox" className="size-4" {...register("rememberMe")} />
            {t("auth.rememberMe")}
          </label>
          <Link to={AUTH_PATHS.forgotPassword} className="text-sm font-medium text-brand-600 hover:underline">
            {t("auth.login.forgot")}
          </Link>
        </div>
        <Button type="submit" className="w-full" disabled={mutation.isPending}>
          {mutation.isPending ? <Spinner className="size-4 text-white" /> : null}
          {mutation.isPending ? t("auth.login.submitting") : t("auth.login.submit")}
        </Button>
      </form>
    </AuthLayout>
  );
}
