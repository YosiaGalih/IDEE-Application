import { Archive, ArchiveRestore, Plus, Search } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { generatePath, Link, useSearchParams } from "react-router";
import { ApiError } from "@/api/errors";
import { useProjects, useSetProjectArchived } from "@/api/hooks/useProjects";
import type { Project, ProjectListFilter } from "@/api/projects";
import { uxStateForError } from "@/api/uxState";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { StateView } from "@/components/ui/StateView";
import { cn } from "@/lib/cn";
import { describeError } from "@/lib/describeError";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { ProjectStatusBadge } from "./ProjectStatusBadge";

const STATUS_FILTERS = ["active", "archived", "all"] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];
const isStatusFilter = (value: string | null): value is StatusFilter =>
  value !== null && (STATUS_FILTERS as readonly string[]).includes(value);

export const NEW_PROJECT_PATH = "/projects/new";
export const workspacePath = (projectId: string) =>
  generatePath("/projects/:projectId/workspace", { projectId });

/** `/projects`: the projects in the user's scope (FR-012, FR-017), filtered by status and text in the URL. */
export function ProjectsPage() {
  const { t, i18n } = useTranslation();
  useDocumentTitle(t("screens.projects"));
  const [params, setParams] = useSearchParams();
  const rawStatus = params.get("status");
  const filter: ProjectListFilter = {
    status: isStatusFilter(rawStatus) ? rawStatus : "active",
    q: params.get("q") ?? "",
  };
  const [draft, setDraft] = useState(filter.q);
  const projects = useProjects(filter);
  const archive = useSetProjectArchived();
  const dateFormat = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" });

  const update = (changes: Partial<ProjectListFilter>) => {
    const next = { ...filter, ...changes };
    const search = new URLSearchParams();
    if (next.status !== "active") search.set("status", next.status);
    if (next.q.trim()) search.set("q", next.q.trim());
    setParams(search, { replace: true });
  };
  const handleSearch = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    update({ q: draft });
  };
  const handleStatus = (event: React.ChangeEvent<HTMLSelectElement>) => {
    if (isStatusFilter(event.target.value)) update({ status: event.target.value });
  };
  const handleArchive = (project: Project) =>
    archive.mutate({ projectId: project.id, archived: project.status === "active" });

  const isFiltered = filter.q.trim() !== "" || filter.status !== "active";
  let body: React.ReactNode;
  if (projects.isPending) {
    body = <StateView state="loading" />;
  } else if (projects.isError) {
    body = (
      <StateView
        state={uxStateForError(projects.error)}
        requestId={projects.error instanceof ApiError ? projects.error.requestId : null}
        onAction={() => void projects.refetch()}
      />
    );
  } else if (projects.data.length === 0) {
    body = (
      <div className="flex flex-col items-center">
        <StateView
          state="empty"
          title={isFiltered ? t("projects.empty.filteredTitle") : t("projects.empty.title")}
          detail={isFiltered ? t("projects.empty.filteredBody") : t("projects.empty.body")}
        />
        {isFiltered ? null : (
          <Button asChild>
            <Link to={NEW_PROJECT_PATH}>
              <Plus aria-hidden="true" className="size-4" />
              {t("projects.newProject")}
            </Link>
          </Button>
        )}
      </div>
    );
  } else {
    body = (
      <div
        // A new filter keeps the previous rows until its answer arrives: say they are being replaced.
        aria-busy={projects.isPlaceholderData}
        className={cn(
          "overflow-x-auto rounded-[var(--radius-card)] border border-line bg-surface transition-opacity",
          projects.isPlaceholderData && "opacity-60",
        )}
      >
        <table className="w-full text-left text-sm">
          <caption className="sr-only">{t("projects.table.caption")}</caption>
          <thead className="border-b border-line text-xs uppercase tracking-wide text-fg-muted">
            <tr>
              <th scope="col" className="px-4 py-2">
                {t("projects.table.name")}
              </th>
              <th scope="col" className="px-4 py-2">
                {t("projects.table.stack")}
              </th>
              <th scope="col" className="px-4 py-2">
                {t("projects.table.status")}
              </th>
              <th scope="col" className="px-4 py-2">
                {t("projects.table.updated")}
              </th>
              <th scope="col" className="px-4 py-2 text-right">
                {t("projects.table.actions")}
              </th>
            </tr>
          </thead>
          <tbody>
            {projects.data.map((project) => {
              const languages = project.stack.languages ?? [];
              const canWrite = project.permissions.includes("project:write");
              const canToggle = canWrite && (project.status === "active" || project.status === "archived");
              const isArchived = project.status === "archived";
              return (
                <tr key={project.id} className="border-b border-line last:border-0">
                  <td className="px-4 py-3">
                    <Link
                      to={workspacePath(project.id)}
                      className="font-medium text-brand-600 hover:underline"
                    >
                      {project.name}
                    </Link>
                    <div className="font-mono text-xs text-fg-muted">{project.slug}</div>
                  </td>
                  <td className="px-4 py-3 text-fg-muted">
                    {languages.length > 0 ? languages.join(", ") : t("projects.noStack")}
                  </td>
                  <td className="px-4 py-3">
                    <ProjectStatusBadge status={project.status} />
                  </td>
                  <td className="px-4 py-3 text-fg-muted">
                    <time dateTime={project.updated_at}>
                      {dateFormat.format(new Date(project.updated_at))}
                    </time>
                  </td>
                  <td className="px-4 py-3 text-right">
                    {canToggle ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={archive.isPending}
                        aria-label={t(isArchived ? "projects.unarchiveOf" : "projects.archiveOf", {
                          name: project.name,
                        })}
                        onClick={() => handleArchive(project)}
                      >
                        {isArchived ? (
                          <ArchiveRestore aria-hidden="true" className="size-4" />
                        ) : (
                          <Archive aria-hidden="true" className="size-4" />
                        )}
                        {t(isArchived ? "projects.unarchive" : "projects.archive")}
                      </Button>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold text-fg" data-testid="screen-title">
          {t("screens.projects")}
        </h1>
        <Button asChild>
          <Link to={NEW_PROJECT_PATH}>
            <Plus aria-hidden="true" className="size-4" />
            {t("projects.newProject")}
          </Link>
        </Button>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <form role="search" onSubmit={handleSearch} className="flex flex-1 items-end gap-2">
          <div className="flex-1 space-y-1.5">
            <Label htmlFor="projects-search">{t("projects.search.label")}</Label>
            <Input
              id="projects-search"
              type="search"
              value={draft}
              maxLength={120}
              placeholder={t("projects.search.placeholder")}
              onChange={(event) => setDraft(event.target.value)}
            />
          </div>
          <Button type="submit" variant="secondary">
            <Search aria-hidden="true" className="size-4" />
            {t("projects.search.submit")}
          </Button>
        </form>
        <div className="space-y-1.5">
          <Label htmlFor="projects-status">{t("projects.statusFilter.label")}</Label>
          <select
            id="projects-status"
            value={filter.status}
            onChange={handleStatus}
            className="h-10 rounded-[var(--radius-control)] border border-line bg-surface px-3 text-sm text-fg"
          >
            {STATUS_FILTERS.map((status) => (
              <option key={status} value={status}>
                {t(`projects.statusFilter.${status}`)}
              </option>
            ))}
          </select>
        </div>
      </div>
      {archive.isError ? (
        <ErrorSummary text={describeError(archive.error, t)} focusKey={archive.submittedAt} />
      ) : null}
      {body}
    </div>
  );
}
