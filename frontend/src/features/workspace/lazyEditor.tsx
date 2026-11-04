import { AlertTriangle } from "lucide-react";
import { Component, useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { StateView } from "@/components/ui/StateView";
import type * as EditorModule from "./CodeEditor";
import type { DiffProps, EditorProps } from "./CodeEditor";

/**
 * Monaco weighs megabytes, so it stays out of the route chunk: the workspace (tree, tabs, search) shows
 * at once and the editor loads beside it. The chunk is fetched once; every editor and diff shares it.
 * A failed load stays inside the editor area: the explorer and search keep working, and since nothing can
 * have been edited before the editor existed, reloading the page loses nothing.
 */
type Module = typeof EditorModule;
let loaded: Module | null = null;
let pending: Promise<Module> | null = null;

function load(): Promise<Module> {
  pending ??= import("./CodeEditor").then(
    (module) => {
      loaded = module;
      return module;
    },
    (error: unknown) => {
      pending = null;
      throw error;
    },
  );
  return pending;
}

function useEditorModule(): { module: Module | null; hasFailed: boolean } {
  const [module, setModule] = useState<Module | null>(loaded);
  const [hasFailed, setHasFailed] = useState(false);
  useEffect(() => {
    if (module) return undefined;
    let isCurrent = true;
    load().then(
      (ready) => isCurrent && setModule(ready),
      () => isCurrent && setHasFailed(true),
    );
    return () => {
      isCurrent = false;
    };
  }, [module]);
  return { module, hasFailed };
}

/** The file stays readable (and copyable) when the editor cannot run. */
function EditorUnavailable({ text }: { text: string | null }) {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 bg-surface p-3 text-sm">
      <div role="alert" className="flex flex-wrap items-center gap-2 text-status-danger">
        <AlertTriangle aria-hidden="true" className="size-4" />
        {t("workspace.editor.unavailable")}
        <Button size="sm" variant="secondary" onClick={() => window.location.reload()}>
          {t("workspace.editor.reloadPage")}
        </Button>
      </div>
      {text !== null ? (
        <pre className="min-h-0 flex-1 overflow-auto rounded border border-line p-2 font-mono text-xs text-fg">
          {text}
        </pre>
      ) : null}
    </div>
  );
}

type BoundaryProps = { fallback: ReactNode; children: ReactNode };

/** Monaco throwing while it renders stays here instead of replacing the whole workspace. */
class EditorBoundary extends Component<BoundaryProps, { hasFailed: boolean }> {
  override state = { hasFailed: false };
  static getDerivedStateFromError() {
    return { hasFailed: true };
  }
  override render() {
    return this.state.hasFailed ? this.props.fallback : this.props.children;
  }
}

export function EditorSlot(props: EditorProps) {
  const { module, hasFailed } = useEditorModule();
  const fallback = <EditorUnavailable text={props.value} />;
  if (hasFailed) return fallback;
  if (!module) return <StateView state="loading" />;
  return (
    <EditorBoundary fallback={fallback}>
      <module.CodeEditor {...props} />
    </EditorBoundary>
  );
}

export function DiffSlot(props: DiffProps) {
  const { module, hasFailed } = useEditorModule();
  const fallback = <EditorUnavailable text={null} />;
  if (hasFailed) return fallback;
  if (!module) return <StateView state="loading" />;
  return (
    <EditorBoundary fallback={fallback}>
      <module.DiffView {...props} />
    </EditorBoundary>
  );
}

/** A model exists only once Monaco has loaded; a tab closed before that has nothing to free. */
export function disposeModel(projectId: string, path: string): void {
  loaded?.disposeModel(projectId, path);
}

/** Leaving the workspace frees every model of the project (their text and undo history). */
export function disposeProjectModels(projectId: string): void {
  loaded?.disposeProjectModels(projectId);
}
