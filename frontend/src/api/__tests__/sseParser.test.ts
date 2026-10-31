import { describe, expect, it } from "vitest";
import { SseParser } from "@/api/sse/parser";

function parseAll(...chunks: string[]) {
  const parser = new SseParser();
  return chunks.flatMap((chunk) => parser.push(chunk));
}

describe("SseParser", () => {
  it("reads id, event and data of one frame", () => {
    expect(parseAll('id: 7\nevent: task.log\ndata: {"a":1}\n\n')).toEqual([
      { id: "7", event: "task.log", data: '{"a":1}' },
    ]);
  });

  it("joins multi-line data and defaults the event name", () => {
    expect(parseAll("data: one\ndata: two\n\n")).toEqual([{ id: null, event: "message", data: "one\ntwo" }]);
  });

  it("ignores comments (heartbeats) and frames without data", () => {
    expect(parseAll(": ping\n\nevent: x\n\n")).toEqual([]);
  });

  it("handles CRLF, CR and a CRLF split across chunks", () => {
    expect(parseAll("id: 1\r\ndata: a\r", "\n\r\nid: 2\rdata: b\r\r")).toEqual([
      { id: "1", event: "message", data: "a" },
      { id: "2", event: "message", data: "b" },
    ]);
  });

  it("joins a line cut in the middle by the network", () => {
    expect(parseAll("id: 1", "2\nda", "ta: hel", "lo\n", "\n")).toEqual([
      { id: "12", event: "message", data: "hello" },
    ]);
  });

  it("gives an id only to the frame that carries one (ephemeral events have none)", () => {
    expect(parseAll("id: 3\ndata: a\n\ndata: b\n\n").map((f) => f.id)).toEqual(["3", null]);
  });

  it("strips one leading space only and ignores ids with NUL", () => {
    expect(parseAll("data:  two spaces\nid: a\u0000b\n\n")).toEqual([
      { id: null, event: "message", data: " two spaces" },
    ]);
  });
});
