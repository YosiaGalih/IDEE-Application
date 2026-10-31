import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

export const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
  {
    variants: {
      tone: {
        success: "bg-status-success-bg text-status-success",
        info: "bg-status-info-bg text-status-info",
        warning: "bg-status-warning-bg text-status-warning",
        danger: "bg-status-danger-bg text-status-danger",
        neutral: "bg-status-neutral-bg text-status-neutral",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

type Props = ComponentProps<"span"> & VariantProps<typeof badgeVariants>;

export function Badge({ className, tone, ...props }: Props) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
