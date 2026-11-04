import { ListTodo, Plus, Search } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import type { TaskSummary } from "@/api/dashboard";
import { ApiError } from "@/api/errors";
import { useDashboard } from "@/api/hooks/useDashboard";
import { useMe } from "@/api/hooks/useMe";
import type { Project } from "@/api/projects";
import { uxStateForError } from "@/api/uxState";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { StateView } from "@/components/ui/StateView";
import { NEW_PROJECT_PATH } from "@/lib/paths";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import {
  AgentActivity,
  AttentionQueue,
  MetricCards,
  RecentProjects,
  RecentTasks,
  UsageSummary,
} from "./DashboardSections";

const matches = (needle: string, ...haystack: string[]) =>
  haystack.some((value) => value.toLocaleLowerCase().includes(needle));
const taskMatches = (needle: string) => (task: TaskSummary) => matches(needle, task.title, task.project_name);
const projectMatches = (needle: string) => (project: Project) => matches(needle, project.name, project.slug);

/** `/`: the dashboard (FR-007–011). Counts and lists come from the server, in the user's scope. */
export function DashboardPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("screens.dashboard"));
  const dashboard = useDashboard();
  const me = useMe();
  const [query, setQuery] = useState("");
  const searchId = useId();
  const newTaskHintId = useId();
  const needle = query.trim().toLocaleLowerCase();
  const name = me.data?.user.display_name;

  let body: React.ReactNode;
  if (dashboard.isPending) {
    body = <StateView state="loading" />;
  } else if (dashboard.isError) {
    body = (
      <StateView
        state={uxStateForError(dashboard.error)}
        requestId={dashboard.error instanceof ApiError ? dashboard.error.requestId : null}
        onAction={() => void dashboard.refetch()}
      />
    );
  } else {
    const { data, failedQueueTotal } = dashboard.data;
    const isNew =
      data.recent_projects.length === 0 &&
      data.recent_tasks.length === 0 &&
      data.metrics.active_projects === 0;
    // Filtering is local: the box narrows what the dashboard already has, it never asks the server again.
    const recentTasks = needle ? data.recent_tasks.filter(taskMatches(needle)) : data.recent_tasks;
    const queue = needle ? data.failed_queue.filter(taskMatches(needle)) : data.failed_queue;
    const projects = needle ? data.recent_projects.filter(projectMatches(needle)) : data.recent_projects;
    const isFiltered = needle !== "";
    body = (
      <div className="flex flex-col gap-6">
        <MetricCards metrics={data.metrics} />
        {isNew ? (
          <div className="flex flex-col items-center rounded-[var(--radius-card)] border border-line bg-surface">
            <StateView state="empty" title={t("dashboard.empty.title")} detail={t("dashboard.empty.body")} />
            <Button asChild className="mb-6">
              <Link to={NEW_PROJECT_PATH}>
                <Plus aria-hidden="true" className="size-4" />
                {t("dashboard.empty.action")}
              </Link>
            </Button>
          </div>
        ) : (
          <>
            <div className="max-w-md">
              <Label htmlFor={searchId}>{t("dashboard.searchLabel")}</Label>
              <div className="relative mt-1">
                <Search
                  aria-hidden="true"
                  className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-muted"
                />
                <Input
                  id={searchId}
                  type="search"
                  value={query}
                  placeholder={t("dashboard.searchPlaceholder")}
                  onChange={(event) => setQuery(event.target.value)}
                  className="pl-9"
                />
              </div>
            </div>
            <div className="grid gap-6 xl:grid-cols-2">
              <AttentionQueue
                tasks={queue}
                total={failedQueueTotal}
                shown={data.failed_queue.length}
                isFiltered={isFiltered}
              />
              <RecentTasks tasks={recentTasks} isFiltered={isFiltered} />
              <RecentProjects projects={projects} isFiltered={isFiltered} />
              <UsageSummary usage={data.usage} />
              <AgentActivity />
            </div>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6 p-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
            {t("dashboard.eyebrow")}
          </p>
          <h1 className="text-2xl font-semibold text-fg" data-testid="screen-title">
            {name ? t("dashboard.greeting", { name }) : t("dashboard.greetingAnonymous")}
          </h1>
          <p className="text-sm text-fg-muted">{t("dashboard.summary")}</p>
        </div>
        <div
          role="group"
          aria-label={t("dashboard.quickCreate")}
          className="flex flex-wrap items-center gap-2"
        >
          <Button asChild>
            <Link to={NEW_PROJECT_PATH}>
              <Plus aria-hidden="true" className="size-4" />
              {t("dashboard.newProject")}
            </Link>
          </Button>
          <Button variant="secondary" disabled aria-describedby={newTaskHintId}>
            <ListTodo aria-hidden="true" className="size-4" />
            {t("dashboard.newTask")}
          </Button>
          <p id={newTaskHintId} className="w-full text-right text-xs text-fg-muted">
            {t("dashboard.newTaskUnavailable")}
          </p>
        </div>
      </header>
      {body}
    </div>
  );
}
