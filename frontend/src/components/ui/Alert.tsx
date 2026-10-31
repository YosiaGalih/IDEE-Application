import { AlertTriangle, CheckCircle2, Info, XCircle, type LucideIcon } from "lucide-react";
import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

type Tone = "info" | "warning" | "danger" | "success";
type Props = ComponentProps<"div"> & { tone?: Tone };

const TONES: Record<Tone, { className: string; icon: LucideIcon }> = {
  info: { className: "border-status-info/30 bg-status-info-bg text-status-info", icon: Info },
  warning: {
    className: "border-status-warning/30 bg-status-warning-bg text-status-warning",
    icon: AlertTriangle,
  },
  danger: { className: "border-status-danger/30 bg-status-danger-bg text-status-danger", icon: XCircle },
  success: {
    className: "border-status-success/30 bg-status-success-bg text-status-success",
    icon: CheckCircle2,
  },
};

/** A message box whose tone is shown by an icon as well as colour (frontend.md: never colour alone). */
export function Alert({ className, tone = "info", role, children, ...props }: Props) {
  const { className: toneClass, icon: Icon } = TONES[tone];
  return (
    <div
      role={role ?? (tone === "danger" ? "alert" : "status")}
      className={cn("flex gap-3 rounded-[var(--radius-control)] border p-3 text-sm", toneClass, className)}
      {...props}
    >
      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <div>{children}</div>
    </div>
  );
}
