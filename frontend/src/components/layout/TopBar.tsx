import type { ReactNode } from "react";
import { LanguageSelect } from "./LanguageSelect";

type Props = {
  /** The account controls, given by the app layer (components never import a feature). */
  userMenu?: ReactNode;
};

export function TopBar({ userMenu }: Props) {
  return (
    <header className="flex h-14 items-center justify-end gap-4 border-b border-line bg-surface px-6">
      {userMenu}
      <LanguageSelect />
    </header>
  );
}
