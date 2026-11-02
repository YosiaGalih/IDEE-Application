import {
  AlertTriangle,
  Archive,
  ArchiveRestore,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Plus,
  Search,
  Upload,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";
import { ApiError } from "@/api/errors";
import { useProjects, useSetProjectArchived } from "@/api/hooks/useProjects";
import type { Project, ProjectListFilter } from "@/api/projects";
import { uxStateForError } from "@/api/uxState";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { StateView } from "@/components/ui/StateView";
import { cn } from "@/lib/cn";
import { describeError } from "@/lib/describeError";
import { importPath, NEW_PROJECT_PATH, workspacePath } from "@/lib/paths";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { importErrorText, isImportError } from "./importText";
import { ProjectStatusBadge } from "./ProjectStatusBadge";

const STATUS_FILTERS = ["active", "archived", "all"] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];
const isStatusFilter = (value: string | null): value is StatusFilter =>
  value !== null && (STATUS_FILTERS as readonly string[]).includes(value);
const pageOf = (value: string | null) => {
  const page = Number(value);
  return Number.isSafeInteger(page) && page >= 1 ? page : 1;
};

/** `/projects`: the projects in the user's scope (FR-012, FR-017), filtered by status and text in the URL. */
export function ProjectsPage() {
  const { t, i18n } = useTranslation();
  useDocumentTitle(t("screens.projects"));
  const [params, setParams] = useSearchParams();
  const rawStatus = params.get("status");
  const filter: ProjectListFilter = {
    status: isStatusFilter(rawStatus) ? rawStatus : "active",
    q: params.get("q") ?? "",
    page: pageOf(params.get("page")),
  };
  // The search box follows the URL (Back, the sidebar link) and keeps what is being typed in between.
  const [draft, setDraft] = useState(filter.q);
  const [shownQ, setShownQ] = useState(filter.q);
  if (shownQ !== filter.q) {
    setShownQ(filter.q);
    setDraft(filter.q);
  }
  const [announcement, setAnnouncement] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const projects = useProjects(filter);
  const archive = useSetProjectArchived();
  const dateFormat = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" });

  const update = (changes: Partial<ProjectListFilter>) => {
    // A new filter starts at its first page; only a page change keeps the rest.
    const next = { ...filter, page: 1, ...changes };
    const search = new URLSearchParams();
    if (next.status !== "active") search.set("status", next.status);
    if (next.q.trim()) search.set("q", next.q.trim());
    if (next.page > 1) search.set("page", String(next.page));
    setParams(search, { replace: true });
  };
  const meta = projects.data?.meta;
  const pages = meta ? Math.max(1, Math.ceil(meta.total / meta.page_size)) : 1;
  const isPastTheEnd =
    !projects.isPlaceholderData && meta !== undefined && filter.page > pages && meta.total > 0;
  useEffect(() => {
    // A page emptied by an archive (or an old link): go to the last page that has rows.
    if (isPastTheEnd)
      setParams(
        (current) => {
          const search = new URLSearchParams(current);
          if (pages > 1) search.set("page", String(pages));
          else search.delete("page");
          return search;
        },
        { replace: true },
      );
  }, [isPastTheEnd, pages, setParams]);
  const handleSearch = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    update({ q: draft });
  };
  const handleStatus = (event: React.ChangeEvent<HTMLSelectElement>) => {
    // What is in the search box applies too: the list never disagrees with what the box shows.
    if (isStatusFilter(event.target.value)) update({ status: event.target.value, q: draft });
  };
  const handleArchive = (project: Project) => {
    const archived = project.status === "active";
    setAnnouncement(null);
    archive.mutate(
      { projectId: project.id, archived },
      {
        onSuccess: () => {
          setAnnouncement(t(archived ? "projects.archived" : "projects.unarchived", { name: project.name }));
          headingRef.current?.focus(); // the row and its button may be gone from this filter
        },
      },
    );
  };

  const isFiltered = filter.q.trim() !== "" || filter.status !== "active";
  let body: React.ReactNode;
  if (
    projects.isPending ||
    isPastTheEnd ||
    (projects.isPlaceholderData && projects.data.items.length === 0)
  ) {
    // Also while a new filter loads over an empty old one: never "no projects" before the answer.
    body = <StateView state="loading" />;
  } else if (projects.isError) {
    body = (
      <StateView
        state={uxStateForError(projects.error)}
        requestId={projects.error instanceof ApiError ? projects.error.requestId : null}
        onAction={() => void projects.refetch()}
      />
    );
  } else if (projects.data.items.length === 0) {
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
            {projects.data.items.map((project) => {
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
                    <div className="flex flex-wrap gap-1">
                      <ProjectStatusBadge status={project.status} />
                      <RepositoryBadge repository={project.repository} />
                    </div>
                  </td>
                  <td className="px-4 py-3 text-fg-muted">
                    <time dateTime={project.updated_at}>
                      {dateFormat.format(new Date(project.updated_at))}
                    </time>
                  </td>
                  <td className="space-x-1 px-4 py-3 text-right">
                    {canWrite && needsArchive(project) ? (
                      <Button asChild variant="ghost" size="sm">
                        <Link
                          to={importPath(project.id)}
                          aria-label={t("projects.import.actionOf", { name: project.name })}
                        >
                          <Upload aria-hidden="true" className="size-4" />
                          {t("projects.import.action")}
                        </Link>
                      </Button>
                    ) : null}
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
        {meta && meta.total > meta.page_size ? (
          <nav
            aria-label={t("projects.pagination.label")}
            className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2 text-sm text-fg-muted"
          >
            <span>{t("projects.pagination.status", { page: filter.page, pages, total: meta.total })}</span>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={filter.page <= 1}
                onClick={() => update({ page: filter.page - 1 })}
              >
                <ChevronLeft aria-hidden="true" className="size-4" />
                {t("projects.pagination.previous")}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={filter.page >= pages}
                onClick={() => update({ page: filter.page + 1 })}
              >
                {t("projects.pagination.next")}
                <ChevronRight aria-hidden="true" className="size-4" />
              </Button>
            </div>
          </nav>
        ) : null}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-2xl font-semibold text-fg focus:outline-none"
          data-testid="screen-title"
        >
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
        <ErrorSummary
          text={describeError(archive.error, t, {
            byCode: { INVALID_TRANSITION: t("projects.archiveConflict") },
            unknownOutcome: t("projects.archiveOutcomeUnknown"),
          })}
          focusKey={archive.submittedAt}
        />
      ) : null}
      {announcement ? <Alert tone="success">{announcement}</Alert> : null}
      {body}
    </div>
  );
}

/** An upload project whose import failed or stopped: the user can send an archive again. */
const needsArchive = (project: Project) =>
  project.status === "active" &&
  project.repository?.provider === "upload" &&
  project.repository.sync_state === "error" &&
  isImportError(project.repository.error);

function RepositoryBadge({ repository }: { repository: Project["repository"] }) {
  const { t } = useTranslation();
  if (repository?.sync_state === "importing") {
    return (
      <Badge tone="info">
        <Loader2 aria-hidden="true" className="size-3.5" />
        {t("projects.import.running")}
      </Badge>
    );
  }
  if (repository?.sync_state !== "error") return null;
  if (isImportError(repository.error)) {
    const incomplete = repository.error === "import_incomplete";
    return (
      <Badge tone="warning" title={importErrorText(repository.error, t)}>
        <AlertTriangle aria-hidden="true" className="size-3.5" />
        {t(incomplete ? "projects.import.incomplete" : "projects.import.failedTitle")}
      </Badge>
    );
  }
  return (
    <Badge tone="warning" title={repository.error ?? undefined}>
      <AlertTriangle aria-hidden="true" className="size-3.5" />
      {t("projects.repositoryProblem")}
    </Badge>
  );
}
