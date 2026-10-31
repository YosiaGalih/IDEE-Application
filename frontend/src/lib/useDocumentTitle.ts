import { useEffect } from "react";
import { useTranslation } from "react-i18next";

/** "<screen> · KodeKantor": each route names itself for tabs, history and screen readers. */
export function useDocumentTitle(title: string): void {
  const { t } = useTranslation();
  useEffect(() => {
    document.title = `${title} · ${t("app.name")}`;
  }, [title, t]);
}
