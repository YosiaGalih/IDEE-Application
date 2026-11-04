/** Every task status (ARCHITECTURE §5.1, DATA_MODEL `tasks.status`); only the backend state machine writes it. */
export const TASK_STATUSES = [
  "draft",
  "needs_clarification",
  "planned",
  "queued",
  "running",
  "validating",
  "needs_changes",
  "waiting_approval",
  "interrupted",
  "completed",
  "failed",
  "cancelled",
  "rejected",
] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];
