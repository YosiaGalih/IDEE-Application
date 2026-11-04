/**
 * The only module that imports Monaco (spec WP-2.2a). Bundled locally, with same-origin workers (CSP
 * `worker-src 'self' blob:`), never from a CDN. Repository content is untrusted: links are not clickable.
 */
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";
import CssWorker from "monaco-editor/language/css/css.worker?worker";
import HtmlWorker from "monaco-editor/language/html/html.worker?worker";
import JsonWorker from "monaco-editor/language/json/json.worker?worker";
import TsWorker from "monaco-editor/language/typescript/ts.worker?worker";
import { useEffect, useRef } from "react";
import { monacoLanguage } from "./monacoLanguages";

window.MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    if (label === "json") return new JsonWorker();
    if (label === "css" || label === "scss" || label === "less") return new CssWorker();
    if (label === "html") return new HtmlWorker();
    if (label === "typescript" || label === "javascript") return new TsWorker();
    return new EditorWorker();
  },
};

/** Untrusted content: no clickable links, no hover markdown fetching anything, no telemetry-like features. */
const SAFE_OPTIONS: monaco.editor.IEditorOptions = {
  links: false,
  automaticLayout: true,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  fontSize: 14,
};

const modelUri = (projectId: string, path: string) =>
  monaco.Uri.from({ scheme: "kk", authority: projectId, path: `/${path}` });

/** Frees a closed tab's model (its undo history goes with it). */
export function disposeModel(projectId: string, path: string): void {
  monaco.editor.getModel(modelUri(projectId, path))?.dispose();
}

/** Frees every model of a project, when its workspace goes away. */
export function disposeProjectModels(projectId: string): void {
  for (const model of monaco.editor.getModels()) {
    if (model.uri.scheme === "kk" && model.uri.authority === projectId) model.dispose();
  }
}

/** The text version each model holds: the editor writes `value` into a model only for a new version. */
const appliedVersion = new WeakMap<monaco.editor.ITextModel, string>();

/** A request to move the cursor to a line (a search hit); `nonce` makes the same hit work twice. */
export type RevealRequest = { path: string; line: number; nonce: number };

export type EditorProps = {
  projectId: string;
  path: string;
  value: string;
  /**
   * Changes only when the server's text replaces the editor's (opening, reloading). Between versions
   * Monaco is the source of truth: `value` trails it by a render, and writing it back would undo keystrokes.
   */
  version: string;
  language: string | null;
  isReadOnly: boolean;
  ariaLabel: string;
  onChange: (value: string) => void;
  onSave: () => void;
  reveal?: RevealRequest | null;
};

export function CodeEditor({
  projectId,
  path,
  value,
  version,
  language,
  isReadOnly,
  ariaLabel,
  onChange,
  onSave,
  reveal,
}: EditorProps) {
  const container = useRef<HTMLDivElement>(null);
  const editor = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const callbacks = useRef({ onChange, onSave });
  const applying = useRef(false);
  const revealed = useRef<number | null>(null);
  useEffect(() => {
    // The latest handlers for Monaco's listeners, which are registered once.
    callbacks.current = { onChange, onSave };
  });

  useEffect(() => {
    if (!container.current) return undefined;
    const instance = monaco.editor.create(container.current, {
      ...SAFE_OPTIONS,
      theme: "vs-dark",
      ariaLabel,
    });
    instance.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => callbacks.current.onSave());
    const listener = instance.onDidChangeModelContent(() => {
      if (applying.current) return;
      const model = instance.getModel();
      if (model) callbacks.current.onChange(model.getValue());
    });
    editor.current = instance;
    return () => {
      listener.dispose();
      instance.dispose();
      editor.current = null;
    };
    // The editor lives as long as the component; models change with the tab.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const instance = editor.current;
    if (!instance) return;
    const uri = modelUri(projectId, path);
    let model = monaco.editor.getModel(uri);
    if (!model) {
      model = monaco.editor.createModel(value, monacoLanguage(language), uri);
      appliedVersion.set(model, version);
    }
    if (instance.getModel() !== model) instance.setModel(model);
    if (appliedVersion.get(model) !== version) {
      // Our own setValue (a reload) is not an edit by the user.
      applying.current = true;
      model.setValue(value);
      applying.current = false;
      appliedVersion.set(model, version);
    }
  }, [projectId, path, value, version, language]);

  useEffect(() => {
    editor.current?.updateOptions({ readOnly: isReadOnly, ariaLabel });
  }, [isReadOnly, ariaLabel]);

  useEffect(() => {
    const instance = editor.current;
    // Only for the file it was asked for, and once: switching tabs does not move another file's cursor.
    if (!instance || !reveal || reveal.path !== path || revealed.current === reveal.nonce) return;
    revealed.current = reveal.nonce;
    instance.revealLineInCenter(reveal.line);
    instance.setPosition({ lineNumber: reveal.line, column: 1 });
    instance.focus();
  }, [reveal, path]);

  return <div ref={container} className="h-full min-h-[20rem] w-full" />;
}

export type DiffProps = { original: string; modified: string; language: string | null; ariaLabel: string };

/** Server version (left) against the user's version (right), read only: for a save conflict. */
export function DiffView({ original, modified, language, ariaLabel }: DiffProps) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current) return undefined;
    const diff = monaco.editor.createDiffEditor(container.current, {
      ...SAFE_OPTIONS,
      theme: "vs-dark",
      readOnly: true,
      originalEditable: false,
      ariaLabel,
    });
    const models = {
      original: monaco.editor.createModel(original, monacoLanguage(language)),
      modified: monaco.editor.createModel(modified, monacoLanguage(language)),
    };
    diff.setModel(models);
    return () => {
      diff.dispose();
      models.original.dispose();
      models.modified.dispose();
    };
  }, [original, modified, language, ariaLabel]);
  return <div ref={container} className="h-80 w-full" />;
}
