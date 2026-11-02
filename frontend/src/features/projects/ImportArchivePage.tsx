import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";
import { ApiError } from "@/api/errors";
import { useProject } from "@/api/hooks/useProjects";
import { uxStateForError } from "@/api/uxState";
import { Button } from "@/components/ui/Button";
import { StateView } from "@/components/ui/StateView";
import { workspacePath } from "@/lib/paths";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { ArchiveImport } from "./ArchiveImport";
import { useArchiveImport } from "./useArchiveImport";

/** `/projects/:projectId/import`: upload (again) into a project made from an archive whose import failed or stopped. */
export function ImportArchivePage() {
  const { t } = useTranslation();
  useDocumentTitle(t("screens.importArchive"));
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const project = useProject(projectId);
  const importer = useArchiveImport({ onReady: (done) => void navigate(workspacePath(done.id)) });

  let body: React.ReactNode;
  if (project.isPending) {
    body = <StateView state="loading" />;
  } else if (project.isError) {
    body = (
      <StateView
        state={uxStateForError(project.error)}
        requestId={project.error instanceof ApiError ? project.error.requestId : null}
        onAction={() => void project.refetch()}
      />
    );
  } else if (!project.data.permissions.includes("project:write")) {
    body = <StateView state="permission_denied" />;
  } else if (
    project.data.repository?.provider !== "upload" ||
    project.data.repository.sync_state === "ready"
  ) {
    body = (
      <div className="space-y-3">
        <p className="text-sm text-fg">{t("projects.import.notWaiting")}</p>
        <Button asChild variant="secondary">
          <Link to={workspacePath(project.data.id)}>{t("projects.import.openWorkspace")}</Link>
        </Button>
      </div>
    );
  } else {
    body = <ArchiveImport projectId={project.data.id} importer={importer} />;
  }

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold text-fg" data-testid="screen-title">
          {t("projects.import.title")}
        </h1>
        <p className="mt-1 text-sm text-fg-muted">{project.data?.name ?? t("projects.import.subtitle")}</p>
      </div>
      {body}
    </div>
  );
}
