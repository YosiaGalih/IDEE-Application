import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { EditorProps } from "../CodeEditor";

// A small Monaco: models by URI, one editor instance, and the calls the component makes.
const fake = vi.hoisted(() => {
  type Uri = { scheme: string; authority: string; path: string; toString: () => string };
  class Model {
    setValueCalls = 0;
    isDisposed = false;
    constructor(
      public value: string,
      public uri: Uri,
    ) {}
    getValue() {
      return this.value;
    }
    setValue(value: string) {
      this.setValueCalls += 1;
      this.value = value;
      for (const listener of fake.listeners) listener();
    }
    dispose() {
      this.isDisposed = true;
      fake.models.delete(this.uri.toString());
    }
  }
  const instance = {
    model: null as Model | null,
    addCommand: () => undefined,
    onDidChangeModelContent: (listener: () => void) => {
      fake.listeners.push(listener);
      return { dispose: () => undefined };
    },
    getModel: () => instance.model,
    setModel: (model: Model) => {
      instance.model = model;
    },
    updateOptions: () => undefined,
    revealLineInCenter: vi.fn(),
    setPosition: () => undefined,
    focus: () => undefined,
    dispose: () => undefined,
  };
  return {
    models: new Map<string, Model>(),
    listeners: [] as (() => void)[],
    instance,
    /** What Monaco does when the user types: change the model, tell the listeners. */
    type(text: string) {
      const model = instance.model;
      if (!model) throw new Error("no model");
      model.value = text;
      for (const listener of fake.listeners) listener();
    },
    module: {
      editor: {
        create: () => instance,
        createModel: (value: string, _language: string, uri: Uri) => {
          const model = new Model(value, uri);
          fake.models.set(uri.toString(), model);
          return model;
        },
        getModel: (uri: Uri) => fake.models.get(uri.toString()) ?? null,
        getModels: () => [...fake.models.values()],
      },
      Uri: {
        from: ({ scheme, authority, path }: { scheme: string; authority: string; path: string }) => ({
          scheme,
          authority,
          path,
          toString: () => `${scheme}://${authority}${path}`,
        }),
      },
      KeyMod: { CtrlCmd: 2048 },
      KeyCode: { KeyS: 49 },
    },
  };
});

vi.mock("monaco-editor", () => fake.module);
vi.mock("monaco-editor/editor/editor.worker?worker", () => ({ default: class {} }));
vi.mock("monaco-editor/language/css/css.worker?worker", () => ({ default: class {} }));
vi.mock("monaco-editor/language/html/html.worker?worker", () => ({ default: class {} }));
vi.mock("monaco-editor/language/json/json.worker?worker", () => ({ default: class {} }));
vi.mock("monaco-editor/language/typescript/ts.worker?worker", () => ({ default: class {} }));

const { CodeEditor, disposeProjectModels } = await import("../CodeEditor");

function props(overrides: Partial<EditorProps> = {}): EditorProps {
  return {
    projectId: "p1",
    path: "a.py",
    value: "a",
    version: "1:0",
    language: "python",
    isReadOnly: false,
    ariaLabel: "Editor a.py",
    onChange: () => undefined,
    onSave: () => undefined,
    ...overrides,
  };
}

describe("CodeEditor", () => {
  it("keeps keystrokes React has not rendered yet: it writes into the model only for a new version", () => {
    const onChange = vi.fn();
    const view = render(<CodeEditor {...props({ onChange })} />);
    fake.type("ab");
    fake.type("abc");
    expect(onChange).toHaveBeenLastCalledWith("abc");
    // React commits the first keystroke ("ab") after the second one reached the model: that render
    // must not write "ab" back over "abc".
    view.rerender(<CodeEditor {...props({ onChange, value: "ab" })} />);
    const model = fake.models.get("kk://p1/a.py");
    expect(model?.getValue()).toBe("abc");
    expect(model?.setValueCalls).toBe(0);
    // The server's text (a reload) replaces it, and is not reported as an edit.
    onChange.mockClear();
    view.rerender(<CodeEditor {...props({ onChange, value: "theirs", version: "1:1" })} />);
    expect(model?.getValue()).toBe("theirs");
    expect(onChange).not.toHaveBeenCalled();
    view.unmount();
  });

  it("moves the cursor for a search hit once, and only in the file it was asked for", () => {
    fake.instance.revealLineInCenter.mockClear();
    const reveal = { path: "b.py", line: 5, nonce: 1 };
    const view = render(<CodeEditor {...props({ reveal })} />);
    expect(fake.instance.revealLineInCenter).not.toHaveBeenCalled();
    view.rerender(<CodeEditor {...props({ path: "b.py", reveal })} />);
    expect(fake.instance.revealLineInCenter).toHaveBeenCalledTimes(1);
    // Back and forth between the tabs: the request was handled already.
    view.rerender(<CodeEditor {...props({ path: "a.py", reveal })} />);
    view.rerender(<CodeEditor {...props({ path: "b.py", reveal })} />);
    expect(fake.instance.revealLineInCenter).toHaveBeenCalledTimes(1);
    // The same hit clicked again is a new request.
    view.rerender(<CodeEditor {...props({ path: "b.py", reveal: { ...reveal, nonce: 2 } })} />);
    expect(fake.instance.revealLineInCenter).toHaveBeenCalledTimes(2);
    view.unmount();
  });

  it("frees every model of a project, and only that project's", () => {
    const view = render(<CodeEditor {...props({ projectId: "p1", path: "x.py" })} />);
    view.rerender(<CodeEditor {...props({ projectId: "p1", path: "y.py" })} />);
    view.rerender(<CodeEditor {...props({ projectId: "p2", path: "x.py" })} />);
    view.unmount();
    disposeProjectModels("p1");
    expect(fake.models.has("kk://p1/x.py")).toBe(false);
    expect(fake.models.has("kk://p1/y.py")).toBe(false);
    expect(fake.models.has("kk://p2/x.py")).toBe(true);
  });
});
