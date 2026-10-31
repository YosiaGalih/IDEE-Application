import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

type Props = ComponentProps<"div"> & { tone?: "info" | "warning" | "danger" | "success" };

const TONES = {
  info: "border-status-info/30 bg-status-info-bg text-status-info",
  warning: "border-status-warning/30 bg-status-warning-bg text-status-warning",
  danger: "border-status-danger/30 bg-status-danger-bg text-status-danger",
  success: "border-status-success/30 bg-status-success-bg text-status-success",
} as const;

export function Alert({ className, tone = "info", role, ...props }: Props) {
  return (
    <div
      role={role ?? (tone === "danger" ? "alert" : "status")}
      className={cn("flex gap-3 rounded-[var(--radius-control)] border p-3 text-sm", TONES[tone], className)}
      {...props}
    />
  );
}
