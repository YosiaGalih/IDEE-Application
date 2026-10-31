import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { z } from "zod";
import { openEventStream, type StreamOptions, type StreamStatus } from "../sse/eventStream";

/** `data` of every task event frame (API_CONTRACTS §5). Ephemeral events carry no seq. */
export const TaskEventSchema = z.object({
  task_id: z.uuid(),
  seq: z.number().int().nonnegative().nullable(),
  type: z.string().min(1),
  ts: z.string(),
  payload: z.record(z.string(), z.unknown()),
});
export type TaskEvent = z.infer<typeof TaskEventSchema>;

type Options = Pick<
  StreamOptions,
  "fetchImpl" | "heartbeatTimeoutMs" | "initialBackoffMs" | "maxBackoffMs" | "jitter"
> & {
  afterSeq?: number | null;
  /** Every valid event, ephemeral ones (agent.delta, test.output) included. */
  onEvent?: (event: TaskEvent) => void;
};

export type TaskEventsState = {
  /** Persistent events (with a seq), in order, each once. */
  events: TaskEvent[];
  status: StreamStatus;
  lastSeq: number | null;
  /** Frames that were not a valid task event: shown, never silently dropped. */
  invalidFrames: number;
};

export function useTaskEvents(taskId: string | null, options: Options = {}): TaskEventsState {
  const [state, setState] = useState<TaskEventsState>({
    events: [],
    status: "closed",
    lastSeq: options.afterSeq ?? null,
    invalidFrames: 0,
  });
  const optionsRef = useRef(options);
  useLayoutEffect(() => {
    optionsRef.current = options; // the latest callbacks, without reopening the stream
  });

  useEffect(() => {
    if (!taskId) return;
    const current = optionsRef.current;
    const invalid = () => setState((s) => ({ ...s, invalidFrames: s.invalidFrames + 1 }));
    const stream = openEventStream({
      path: `/tasks/${encodeURIComponent(taskId)}/events`,
      lastSeq: current.afterSeq ?? null,
      ...(current.fetchImpl ? { fetchImpl: current.fetchImpl } : {}),
      ...(current.heartbeatTimeoutMs ? { heartbeatTimeoutMs: current.heartbeatTimeoutMs } : {}),
      ...(current.initialBackoffMs ? { initialBackoffMs: current.initialBackoffMs } : {}),
      ...(current.maxBackoffMs ? { maxBackoffMs: current.maxBackoffMs } : {}),
      ...(current.jitter ? { jitter: current.jitter } : {}),
      onStatus: (status) => setState((s) => ({ ...s, status })),
      onProtocolError: invalid,
      onFrame: (frame) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(frame.data);
        } catch {
          invalid();
          return;
        }
        const result = TaskEventSchema.safeParse(parsed);
        if (!result.success || result.data.task_id !== taskId || result.data.seq !== frame.seq) {
          invalid();
          return;
        }
        const event = result.data;
        optionsRef.current.onEvent?.(event);
        if (frame.seq !== null) {
          setState((s) => ({ ...s, events: [...s.events, event], lastSeq: frame.seq }));
        }
      },
    });
    return () => stream.close();
  }, [taskId]);

  return state;
}
