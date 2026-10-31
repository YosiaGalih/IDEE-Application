import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

type Props = ComponentProps<"section">;

export function Card({ className, ...props }: Props) {
  return (
    <section
      className={cn("rounded-[var(--radius-card)] border border-line bg-surface p-5 shadow-sm", className)}
      {...props}
    />
  );
}

export function CardTitle({ className, children, ...props }: ComponentProps<"h2">) {
  return (
    <h2 className={cn("text-base font-semibold text-fg", className)} {...props}>
      {children}
    </h2>
  );
}
