import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

type Props = ComponentProps<"label">;

export function Label({ className, ...props }: Props) {
  return <label className={cn("text-sm font-medium text-fg", className)} {...props} />;
}
