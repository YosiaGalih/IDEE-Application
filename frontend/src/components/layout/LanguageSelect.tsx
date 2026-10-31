import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";
import { LANGUAGES, setLanguage, type Language } from "@/i18n";

/** The interface language (id default, en), on the app's top bar and on the sign-in pages. */
export function LanguageSelect() {
  const { t, i18n } = useTranslation();
  const handleLanguageChange = (event: React.ChangeEvent<HTMLSelectElement>) =>
    setLanguage(event.target.value as Language);
  return (
    <label className="flex items-center gap-2 text-sm text-fg-muted">
      <Languages aria-hidden="true" className="size-4" />
      <span className="sr-only">{t("common.language")}</span>
      <select
        className="rounded-md border border-line bg-surface px-2 py-1 text-sm text-fg"
        value={i18n.language}
        onChange={handleLanguageChange}
      >
        {LANGUAGES.map((language) => (
          <option key={language} value={language}>
            {t(language === "id" ? "common.languageId" : "common.languageEn")}
          </option>
        ))}
      </select>
    </label>
  );
}
