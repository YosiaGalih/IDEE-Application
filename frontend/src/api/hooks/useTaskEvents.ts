import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { z } from "zod";
import { apiPath } from "../client";
import type { ApiError } from "../errors";
import { openEventStream, type StreamOptions, type StreamStatus } from "../sse/eventStream";
import { uxStateForError, type UxState } from "../uxState";

/** `data` of every task event frame (API_CONTRACTS §5). Ephemeral events carry no seq. */
export const TaskEventSchema = z.object({
  task_id: z.guid(),
  seq: z.number().int().nonnegative().nullable(),
  type: z.string().min(1),
  ts: z.string(),
  payload: z.record(z.string(), z.unknown()),
});
export type TaskEvent = z.infer<typeof TaskEventSchema>;

/** A frame that was not a valid event of this task: kept with its reason, never silently dropped. */
export type InvalidFrame = { seq: number | null; reason: string };
const MAX_INVALID_KEPT = 50;
/** Persistent events kept in memory: the newest ones; older ones are counted in `dropped`. */
export const MAX_EVENTS_KEPT = 500;

type Options = Pick<
  StreamOptions,
  "fetchImpl" | "heartbeatTimeoutMs" | "initialBackoffMs" | "maxBackoffMs" | "jitter" | "disconnectedAfter"
> & {
  afterSeq?: number | null;
  /** Every valid event, ephemeral ones (agent.delta, test.output) included. */
  onEvent?: (event: TaskEvent) => void;
};

type Inner = {
  taskId: string | null;
  events: TaskEvent[];
  status: StreamStatus;
  error: ApiError | null;
  lastSeq: number | null;
  /** Events discarded from the front to stay within MAX_EVENTS_KEPT. */
  dropped: number;
  invalidFrames: number;
  lastInvalid: InvalidFrame[];
};

export type TaskEventsState = Omit<Inner, "taskId"> & {
  /** The screen state to show for the stream, null while it is connecting or open. */
  uxState: UxState | null;
  /** Opens the stream again from the last delivered seq (after `failed` or `ended`, or to retry now). */
  reconnect: () => void;
};

function initial(taskId: string | null, afterSeq: number | null): Inner {
  return {
    taskId,
    events: [],
    status: "closed",
    error: null,
    lastSeq: afterSeq,
    dropped: 0,
    invalidFrames: 0,
    lastInvalid: [],
  };
}

function streamUxState(status: StreamStatus, error: ApiError | null): UxState | null {
  if (status === "failed") return error ? uxStateForError(error) : "error";
  if (status === "disconnected") return "disconnected";
  return null;
}

export function useTaskEvents(taskId: string | null, options: Options = {}): TaskEventsState {
  const [state, setState] = useState<Inner>(() => initial(taskId, options.afterSeq ?? null));
  const [generation, setGeneration] = useState(0);
  const optionsRef = useRef(options);
  /** The resume point of `lastSeqTaskRef`'s task: written only by that task's active stream. */
  const lastSeqRef = useRef<number | null>(options.afterSeq ?? null);
  const lastSeqTaskRef = useRef<string | null>(taskId);
  useLayoutEffect(() => {
    optionsRef.current = options; // the latest callbacks, without reopening the stream
  });

  // Another task: start from nothing, never show the previous task's events (adjusting state during render).
  let current = state;
  if (state.taskId !== taskId) {
    current = initial(taskId, options.afterSeq ?? null);
    setState(current);
  }

  useEffect(() => {
    // Another task, or no task (which also clears the state), resumes from its own afterSeq: a task that
    // comes back after null starts from nothing, like its state. Reset here, before the early return and
    // where the stream opens: until this effect runs the previous stream is still open and may write the
    // ref (React runs layout effects earlier).
    if (lastSeqTaskRef.current !== taskId) {
      lastSeqTaskRef.current = taskId;
      lastSeqRef.current = optionsRef.current.afterSeq ?? null;
    }
    if (!taskId) return;
    const opts = optionsRef.current;
    /** False once this effect is cleaned up: a late callback of its stream then changes nothing. */
    let live = true;
    const update = (change: (s: Inner) => Inner) => {
      if (live) setState((s) => (s.taskId === taskId ? change(s) : s));
    };
    const invalid = (seq: number | null, reason: string) =>
      update((s) => ({
        ...s,
        invalidFrames: s.invalidFrames + 1,
        lastInvalid: [...s.lastInvalid, { seq, reason }].slice(-MAX_INVALID_KEPT),
      }));

    const stream = openEventStream({
      path: apiPath("tasks", taskId, "events"),
      lastSeq: lastSeqRef.current,
      ...(opts.fetchImpl ? { fetchImpl: opts.fetchImpl } : {}),
      ...(opts.heartbeatTimeoutMs ? { heartbeatTimeoutMs: opts.heartbeatTimeoutMs } : {}),
      ...(opts.initialBackoffMs ? { initialBackoffMs: opts.initialBackoffMs } : {}),
      ...(opts.maxBackoffMs ? { maxBackoffMs: opts.maxBackoffMs } : {}),
      ...(opts.disconnectedAfter ? { disconnectedAfter: opts.disconnectedAfter } : {}),
      ...(opts.jitter ? { jitter: opts.jitter } : {}),
      onStatus: (status, error) => update((s) => ({ ...s, status, error: status === "open" ? null : error })),
      onProtocolError: (message) => invalid(null, message),
      onFrame: (frame) => {
        if (!live) return;
        let parsed: unknown;
        try {
          parsed = JSON.parse(frame.data);
        } catch {
          invalid(frame.seq, "data is not JSON");
          return;
        }
        const result = TaskEventSchema.safeParse(parsed);
        if (!result.success) {
          invalid(frame.seq, `not a task event: ${result.error.issues[0]?.message ?? "invalid"}`);
          return;
        }
        const event = result.data;
        if (event.task_id !== taskId) {
          invalid(frame.seq, "event of another task");
          return;
        }
        if (event.seq !== frame.seq) {
          invalid(frame.seq, `seq in data (${event.seq ?? "none"}) differs from the event id`);
          return;
        }
        if (frame.seq !== null) {
          lastSeqRef.current = frame.seq;
          update((s) => {
            const all = [...s.events, event];
            const over = Math.max(0, all.length - MAX_EVENTS_KEPT);
            return {
              ...s,
              events: over > 0 ? all.slice(over) : all,
              dropped: s.dropped + over,
              lastSeq: frame.seq,
            };
          });
        }
        try {
          optionsRef.current.onEvent?.(event); // after the event is kept: a throwing handler loses nothing
        } catch (cause) {
          invalid(frame.seq, `onEvent threw: ${String(cause)}`);
        }
      },
    });
    return () => {
      live = false;
      stream.close();
    };
  }, [taskId, generation]);

  const reconnect = useCallback(() => setGeneration((g) => g + 1), []);
  return {
    events: current.events,
    status: current.status,
    error: current.error,
    lastSeq: current.lastSeq,
    dropped: current.dropped,
    invalidFrames: current.invalidFrames,
    lastInvalid: current.lastInvalid,
    uxState: streamUxState(current.status, current.error),
    reconnect,
  };
}
