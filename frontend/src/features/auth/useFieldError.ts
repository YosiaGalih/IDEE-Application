import { useTranslation } from "react-i18next";
import { isAuthErrorKey } from "./schemas";

/** Translates a field's resolver message (an `auth.errors.*` key). */
export function useFieldError() {
  const { t } = useTranslation();
  return (message: string | undefined): string | undefined => {
    if (message === undefined) return undefined;
    if (isAuthErrorKey(message)) return t(message);
    // A schema message that is not a key is a bug: show a plain error, and say it where a developer looks.
    console.error(`auth form: validation message is not an auth.errors key: ${message}`);
    return t("states.error.title");
  };
}
