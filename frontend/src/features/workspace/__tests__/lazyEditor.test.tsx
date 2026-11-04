import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import id from "@/i18n/locales/id.json";
import { EditorSlot } from "../lazyEditor";

// The editor chunk does not arrive (network, or a deploy replaced the hashed files).
vi.mock("../CodeEditor", () => {
  throw new Error("chunk failed to load");
});

it("keeps the file readable inside the editor area when the editor cannot be loaded", async () => {
  render(
    <EditorSlot
      projectId="p1"
      path="a.py"
      value={"print('still here')\n"}
      version="1:0"
      language="python"
      isReadOnly={false}
      ariaLabel="Editor a.py"
      onChange={() => undefined}
      onSave={() => undefined}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(id.workspace.editor.unavailable);
  expect(screen.getByRole("button", { name: id.workspace.editor.reloadPage })).toBeInTheDocument();
  expect(screen.getByText("print('still here')")).toBeInTheDocument();
});
