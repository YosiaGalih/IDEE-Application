import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { LanguageSelect } from "@/components/layout/LanguageSelect";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

type Props = { title: string; subtitle?: string; children: ReactNode };

/** The frame of the public pages (login, forgot, reset): no app shell, no data of any user. */
export function AuthLayout({ title, subtitle, children }: Props) {
  const { t } = useTranslation();
  useDocumentTitle(title);
  return (
    <div className="flex min-h-screen bg-canvas text-fg">
      <aside className="on-dark hidden w-[26rem] shrink-0 flex-col justify-between bg-sidebar p-10 text-sidebar-fg lg:flex">
        <div className="flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="size-8" />
          <span className="font-semibold text-white">{t("app.name")}</span>
        </div>
        <div>
          <p className="text-xl font-semibold text-white">{t("auth.brandTitle")}</p>
          <p className="mt-3 text-sm">{t("auth.brandBody")}</p>
        </div>
      </aside>
      <div className="flex flex-1 flex-col">
        <div className="flex justify-end p-4">
          <LanguageSelect />
        </div>
        <main id="main" className="flex flex-1 items-start justify-center px-6 pb-12 pt-6 sm:items-center">
          <div className="w-full max-w-sm space-y-6">
            <div>
              <h1 className="text-2xl font-semibold text-fg" data-testid="screen-title">
                {title}
              </h1>
              {subtitle ? <p className="mt-1 text-sm text-fg-muted">{subtitle}</p> : null}
            </div>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
