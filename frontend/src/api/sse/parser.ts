/** One dispatched server-sent event. `id` is the id line of this frame only: null marks an ephemeral event. */
export type SseFrame = { id: string | null; event: string; data: string };

/**
 * Incremental text/event-stream parser (WHATWG HTML §9.2.6): lines end in CRLF, LF or CR, possibly split
 * across chunks; comments (": ping") and frames without data are not dispatched.
 */
export class SseParser {
  private buffer = "";
  private pendingCr = false;
  private data: string[] = [];
  private event = "";
  private id: string | null = null;

  push(chunk: string): SseFrame[] {
    let text = chunk;
    if (this.pendingCr && text.startsWith("\n")) text = text.slice(1); // the LF of a CRLF cut by the network
    this.pendingCr = false;
    this.buffer += text;
    const frames: SseFrame[] = [];
    let start = 0;
    for (let i = 0; i < this.buffer.length; i++) {
      const char = this.buffer[i];
      if (char !== "\n" && char !== "\r") continue;
      this.line(this.buffer.slice(start, i), frames);
      if (char === "\r") {
        if (i + 1 === this.buffer.length) this.pendingCr = true;
        else if (this.buffer[i + 1] === "\n") i++;
      }
      start = i + 1;
    }
    this.buffer = this.buffer.slice(start);
    return frames;
  }

  private line(line: string, frames: SseFrame[]): void {
    if (line === "") {
      if (this.data.length > 0)
        frames.push({ id: this.id, event: this.event || "message", data: this.data.join("\n") });
      this.data = [];
      this.event = "";
      this.id = null;
      return;
    }
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") this.data.push(value);
    else if (field === "event") this.event = value;
    else if (field === "id" && !value.includes("\u0000")) this.id = value;
  }
}
