import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

type Props = ComponentProps<"input">;

export function Input({ className, ...props }: Props) {
  return (
    <input
      className={cn(
        "h-10 w-full rounded-[var(--radius-control)] border border-line bg-surface px-3 text-sm text-fg placeholder:text-fg-muted aria-[invalid=true]:border-status-danger",
        className,
      )}
      {...props}
    />
  );
}
