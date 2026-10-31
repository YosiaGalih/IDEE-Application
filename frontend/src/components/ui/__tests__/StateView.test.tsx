import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { UxState } from "@/api/uxState";
import { StateView } from "@/components/ui/StateView";
import { StatusPill, type ActionStatus } from "@/components/ui/StatusPill";
import id from "@/i18n/locales/id.json";

const STATES: UxState[] = [
  "loading",
  "empty",
  "error",
  "timeout",
  "disconnected",
  "permission_denied",
  "partial_success",
  "quota_exceeded",
  "capability_disabled",
  "provider_degraded",
  "unauthenticated",
  "rate_limited",
  "not_found",
];

describe("StateView", () => {
  it.each(STATES)("renders %s with its text and an icon, not colour alone", (state) => {
    const { container } = render(<StateView state={state} />);
    expect(screen.getByText(id.states[state].title)).toBeInTheDocument();
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("marks problems as alerts and loading as a busy status", () => {
    const { rerender } = render(<StateView state="permission_denied" />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    rerender(<StateView state="loading" />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
  });

  it("offers the action only for recoverable states and calls it", async () => {
    const onAction = vi.fn();
    const { rerender } = render(<StateView state="timeout" onAction={onAction} requestId="req-12345678" />);
    await userEvent.click(screen.getByRole("button", { name: id.states.timeout.action }));
    expect(onAction).toHaveBeenCalledOnce();
    expect(screen.getByText(/req-12345678/)).toBeInTheDocument();
    rerender(<StateView state="permission_denied" onAction={onAction} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("StatusPill", () => {
  const STATUSES: ActionStatus[] = [
    "queued",
    "running",
    "waiting_approval",
    "validating",
    "completed",
    "failed",
    "cancelled",
  ];
  it.each(STATUSES)("shows %s as text with an icon", (status) => {
    const { container } = render(<StatusPill status={status} />);
    expect(screen.getByText(id.status[status])).toBeInTheDocument();
    expect(container.querySelector("svg")).not.toBeNull();
  });
});
