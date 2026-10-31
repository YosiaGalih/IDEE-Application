import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import id from "./locales/id.json";

export const LANGUAGES = ["id", "en"] as const;
export type Language = (typeof LANGUAGES)[number];
const STORAGE_KEY = "kk.language"; // a per-viewer preference, never anything secret

function initialLanguage(): Language {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "id" || saved === "en") return saved;
  } catch {
    // storage blocked: fall back to the default
  }
  return "id";
}

export function setLanguage(language: Language): void {
  void i18n.changeLanguage(language);
  document.documentElement.lang = language;
  try {
    window.localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // storage blocked: the choice lasts for this page only
  }
}

void i18n.use(initReactI18next).init({
  resources: { id: { translation: id }, en: { translation: en } },
  lng: initialLanguage(),
  fallbackLng: "id",
  interpolation: { escapeValue: false }, // React escapes
  returnNull: false,
});

export default i18n;
