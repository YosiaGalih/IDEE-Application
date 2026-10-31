import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { StateView } from "@/components/ui/StateView";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

export function NotFoundPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("screens.notFound"));
  return (
    <div className="mx-auto max-w-xl">
      <StateView state="not_found" title={t("screens.notFound")} />
      <p className="text-center">
        <Link className="text-sm text-status-info underline" to="/">
          {t("common.backHome")}
        </Link>
      </p>
    </div>
  );
}
