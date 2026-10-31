import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

/** `htmlFor` is required: a label is always tied to its control (WCAG 1.3.1, jsx-a11y). */
type Props = Omit<ComponentProps<"label">, "htmlFor"> & { htmlFor: string };

export function Label({ className, htmlFor, children, ...props }: Props) {
  return (
    <label htmlFor={htmlFor} className={cn("text-sm font-medium text-fg", className)} {...props}>
      {children}
    </label>
  );
}
