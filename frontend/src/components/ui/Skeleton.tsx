import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

type Props = ComponentProps<"div">;

export function Skeleton({ className, ...props }: Props) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-status-neutral-bg", className)}
      {...props}
    />
  );
}
