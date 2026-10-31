import { useTranslation } from "react-i18next";
import { isAuthErrorKey } from "./schemas";

/** Translates a field's resolver message (an `auth.errors.*` key). */
export function useFieldError() {
  const { t } = useTranslation();
  return (message: string | undefined): string | undefined => {
    if (message === undefined) return undefined;
    return isAuthErrorKey(message) ? t(message) : t("states.error.title");
  };
}
