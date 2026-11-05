import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { DiffFile, GitDiff } from "@/api/git";
import id from "@/i18n/locales/id.json";
import { GitDiffView } from "../GitDiffView";

const text = id.workspace.gitPanel.diff;

function file(overrides: Partial<DiffFile> = {}): DiffFile {
  return {
    path: "app.py",
    old_path: null,
    status: "modified",
    additions: 1,
    deletions: 1,
    is_binary: false,
    hunks: [
      {
        header: "@@ -1,2 +1,2 @@",
        old_start: 1,
        old_lines: 2,
        new_start: 1,
        new_lines: 2,
        lines: [
          { type: "context", old_no: 1, new_no: 1, text: "x = 1" },
          { type: "del", old_no: 2, new_no: null, text: "y = 1" },
          { type: "add", old_no: null, new_no: 2, text: "y = 2" },
        ],
      },
    ],
    ...overrides,
  };
}

function diff(files: DiffFile[], overrides: Partial<GitDiff> = {}): GitDiff {
  return {
    base: "c".repeat(40),
    head: null,
    files,
    stats: {
      files: files.length,
      additions: files.reduce((sum, item) => sum + item.additions, 0),
      deletions: files.reduce((sum, item) => sum + item.deletions, 0),
    },
    truncated: false,
    ...overrides,
  };
}

describe("GitDiffView", () => {
  it("shows hunks with old and new line numbers and a sign per line", () => {
    render(<GitDiffView diff={diff([file()])} />);
    expect(screen.getByText("@@ -1,2 +1,2 @@")).toBeInTheDocument();
    const added = screen.getByText("y = 2").closest("tr");
    const removed = screen.getByText("y = 1").closest("tr");
    expect(added).toHaveTextContent("+");
    expect(added).toHaveTextContent("2");
    expect(removed).toHaveTextContent("−");
    expect(screen.getByText(text.status.modified)).toBeInTheDocument();
    expect(screen.getByText("+1 −1", { exact: false })).toBeInTheDocument();
  });

  it("renders diff lines and paths as text", () => {
    const { container } = render(
      <GitDiffView
        diff={diff([
          file({
            path: "<b>x</b>.py",
            hunks: [
              {
                header: "@@ -0,0 +1 @@",
                old_start: 0,
                old_lines: 0,
                new_start: 1,
                new_lines: 1,
                lines: [{ type: "add", old_no: null, new_no: 1, text: "<img src=x onerror=alert(1)>" }],
              },
            ],
          }),
        ])}
      />,
    );
    expect(screen.getByText("<b>x</b>.py")).toBeInTheDocument();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(container.querySelector("img, b")).toBeNull();
  });

  it("says a binary file has no shown content", () => {
    render(<GitDiffView diff={diff([file({ path: "logo.png", is_binary: true, hunks: [] })])} />);
    expect(screen.getByText(text.binary)).toBeInTheDocument();
  });

  it("never calls a cut diff empty", () => {
    render(<GitDiffView diff={diff([], { truncated: true })} />);
    expect(screen.getByText(text.truncated)).toBeInTheDocument();
    expect(screen.queryByText(text.empty)).toBeNull();
  });

  it("says a whole diff with no files is empty", () => {
    render(<GitDiffView diff={diff([])} />);
    expect(screen.getByText(text.empty)).toBeInTheDocument();
  });

  it("shows the old path of a moved file and a deleted file's status", () => {
    render(
      <GitDiffView
        diff={diff([
          file({ path: "new.py", old_path: "old.py" }),
          file({ path: "gone.py", status: "deleted", additions: 0 }),
        ])}
      />,
    );
    const moved = screen.getByRole("region", { name: "new.py" });
    expect(within(moved).getByText(text.movedFrom.replace("{{path}}", "old.py"))).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "gone.py" })).getByText(text.status.deleted),
    ).toBeInTheDocument();
  });
});
