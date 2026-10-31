import { Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";

type Props = { className?: string };

export function Spinner({ className }: Props) {
  return <Loader2 aria-hidden="true" className={cn("size-5 animate-spin text-status-info", className)} />;
}
