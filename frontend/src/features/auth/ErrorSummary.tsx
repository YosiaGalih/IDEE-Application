import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Alert } from "@/components/ui/Alert";
import type { ErrorText } from "./describeError";

type Props = {
  text: ErrorText;
  /** Focus moves here on mount and whenever this changes (each submit), so the user lands on the problem. */
  focusKey: number;
};

/** What went wrong with a form, focused for keyboard and screen reader users (WCAG 3.3.1). */
export function ErrorSummary({ text, focusKey }: Props) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, [focusKey]);
  return (
    <Alert ref={ref} tone="danger" tabIndex={-1} className="focus:outline-2 focus:outline-status-danger">
      <p className="font-medium">{text.title}</p>
      {text.body ? <p className="mt-1">{text.body}</p> : null}
      {text.requestId ? (
        <p className="mt-1 font-mono text-xs">{t("common.requestId", { id: text.requestId })}</p>
      ) : null}
    </Alert>
  );
}
