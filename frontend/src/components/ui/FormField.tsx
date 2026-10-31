import { XCircle } from "lucide-react";
import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";
import { Input } from "./Input";
import { Label } from "./Label";

type Props = Omit<ComponentProps<"input">, "id"> & {
  id: string;
  label: string;
  /** Already translated; ties itself to the input through aria-describedby (WCAG 3.3.1). */
  error?: string | undefined;
};

/** A labelled input with its error: icon + text, never colour alone. */
export function FormField({ id, label, error, className, ...input }: Props) {
  const errorId = `${id}-error`;
  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        {...input}
      />
      {error ? (
        <p id={errorId} className="flex items-center gap-1.5 text-sm text-status-danger">
          <XCircle aria-hidden="true" className="size-4 shrink-0" />
          {error}
        </p>
      ) : null}
    </div>
  );
}
