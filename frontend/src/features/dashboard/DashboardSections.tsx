import {
  Activity,
  CheckCircle2,
  CircleOff,
  Folder,
  FolderOpen,
  Hourglass,
  Loader2,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import type { Dashboard, TaskSummary } from "@/api/dashboard";
import type { Project } from "@/api/projects";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { StatusPill } from "@/components/ui/StatusPill";
import { PROJECTS_PATH, taskPath, workspacePath } from "@/lib/paths";

type Metric = keyof Dashboard["metrics"];
const METRICS: { key: Metric; icon: LucideIcon }[] = [
  { key: "active_projects", icon: Folder },
  { key: "running_tasks", icon: Loader2 },
  { key: "completed_tasks", icon: CheckCircle2 },
  { key: "failed_tasks", icon: XCircle },
  { key: "waiting_approval", icon: Hourglass },
];

type SectionProps = { title: string; aside?: ReactNode; children: ReactNode; className?: string };

/** A card that is a named region: its heading labels it for assistive technology. */
function Section({ title, aside, children, className }: SectionProps) {
  const headingId = useId();
  return (
    <Card aria-labelledby={headingId} className={className}>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <CardTitle id={headingId}>{title}</CardTitle>
        {aside}
      </div>
      {children}
    </Card>
  );
}

export function MetricCards({ metrics }: { metrics: Dashboard["metrics"] }) {
  const { t } = useTranslation();
  const headingId = useId();
  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="mb-3 text-base font-semibold text-fg">
        {t("dashboard.metrics.title")}
      </h2>
      <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {METRICS.map(({ key, icon: Icon }) => (
          <li key={key} className="rounded-[var(--radius-card)] border border-line bg-surface p-4 shadow-sm">
            <div className="flex flex-col gap-2">
              <span className="flex items-center gap-2 text-sm text-fg-muted">
                <Icon aria-hidden="true" className="size-4" />
                {t(`dashboard.metrics.${key}`)}
              </span>
              <span className="text-2xl font-semibold text-fg">{metrics[key]}</span>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function useDateTime() {
  const { i18n } = useTranslation();
  return new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" });
}

type TaskListProps = { tasks: TaskSummary[]; emptyText: string; isQueue?: boolean };

function TaskList({ tasks, emptyText, isQueue = false }: TaskListProps) {
  const { t } = useTranslation();
  const format = useDateTime();
  if (tasks.length === 0) return <p className="text-sm text-fg-muted">{emptyText}</p>;
  return (
    <ul className="divide-y divide-line">
      {tasks.map((task) => (
        <li key={task.id} className="flex items-start justify-between gap-3 py-3 first:pt-0 last:pb-0">
          <div className="min-w-0">
            {isQueue ? (
              <p className="font-medium text-fg">{task.title}</p>
            ) : (
              <Link to={taskPath(task.id)} className="font-medium text-brand-600 hover:underline">
                {task.title}
              </Link>
            )}
            <p className="text-xs text-fg-muted">
              {task.project_name} ·{" "}
              <time dateTime={task.updated_at}>
                {t("dashboard.updated", { time: format.format(new Date(task.updated_at)) })}
              </time>
            </p>
            {isQueue && task.status_reason ? <p className="text-sm text-fg">{task.status_reason}</p> : null}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <StatusPill status={task.status} />
            {isQueue ? (
              <Button asChild variant="secondary" size="sm">
                <Link to={taskPath(task.id)} aria-label={t("dashboard.queue.openOf", { title: task.title })}>
                  {t("dashboard.queue.open")}
                </Link>
              </Button>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

export function RecentTasks({ tasks, isFiltered }: { tasks: TaskSummary[]; isFiltered: boolean }) {
  const { t } = useTranslation();
  return (
    <Section title={t("dashboard.recentTasks.title")}>
      <TaskList
        tasks={tasks}
        emptyText={t(isFiltered ? "dashboard.recentTasks.filteredEmpty" : "dashboard.recentTasks.empty")}
      />
    </Section>
  );
}

type QueueProps = { tasks: TaskSummary[]; total: number; shown: number; isFiltered: boolean };

export function AttentionQueue({ tasks, total, shown, isFiltered }: QueueProps) {
  const { t } = useTranslation();
  // The server sends up to ten: say how many more there are, never pretend the list is complete.
  const more = Math.max(0, total - shown);
  return (
    <Section
      title={t("dashboard.queue.title")}
      aside={
        more > 0 ? (
          <span className="text-sm text-fg-muted">{t("dashboard.queue.more", { count: more })}</span>
        ) : null
      }
    >
      <TaskList
        isQueue
        tasks={tasks}
        emptyText={t(isFiltered ? "dashboard.queue.filteredEmpty" : "dashboard.queue.empty")}
      />
    </Section>
  );
}

/** A value the platform does not record yet: a label, never 0 (AC-16). */
function Unavailable() {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center gap-1 font-medium text-fg-muted">
      <CircleOff aria-hidden="true" className="size-4" />
      {t("dashboard.unavailable")}
    </span>
  );
}

export function AgentActivity() {
  const { t } = useTranslation();
  return (
    <Section title={t("dashboard.agents.title")} aside={<Unavailable />}>
      <p className="flex items-start gap-2 text-sm text-fg-muted">
        <Activity aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        {t("dashboard.agents.body")}
      </p>
    </Section>
  );
}

export function UsageSummary({ usage }: { usage: Dashboard["usage"] }) {
  const { t, i18n } = useTranslation();
  const format = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeZone: "UTC" });
  // The period is [start, end): its last day is the one before `end`.
  const lastDay = new Date(new Date(usage.period.end).getTime() - 1);
  const values = [
    { key: "inputTokens", value: usage.input_tokens },
    { key: "outputTokens", value: usage.output_tokens },
    { key: "cost", value: usage.estimated_cost },
  ] as const;
  return (
    <Section
      title={t("dashboard.usage.title")}
      aside={
        <span className="text-sm text-fg-muted">
          {t("dashboard.usage.period", {
            start: format.format(new Date(usage.period.start)),
            end: format.format(lastDay),
          })}
        </span>
      }
    >
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {values.map(({ key, value }) => (
          <div key={key} className="rounded-[var(--radius-control)] border border-line p-3">
            <dt className="text-sm text-fg-muted">{t(`dashboard.usage.${key}`)}</dt>
            {/* Until usage is recorded every value is unknown; a recorded value comes with WP-3.1. */}
            <dd className="mt-1">
              {usage.cost_status === "unavailable" || value === null ? <Unavailable /> : value}
            </dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-sm text-fg-muted">{t("dashboard.usage.body")}</p>
    </Section>
  );
}

export function RecentProjects({ projects, isFiltered }: { projects: Project[]; isFiltered: boolean }) {
  const { t } = useTranslation();
  return (
    <Section
      title={t("dashboard.recentProjects.title")}
      aside={
        <Link to={PROJECTS_PATH} className="text-sm text-brand-600 hover:underline">
          {t("dashboard.recentProjects.seeAll")}
        </Link>
      }
    >
      {projects.length === 0 ? (
        <p className="text-sm text-fg-muted">
          {t(isFiltered ? "dashboard.recentProjects.filteredEmpty" : "dashboard.recentProjects.empty")}
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {projects.map((project) => {
            const languages = project.stack.languages ?? [];
            return (
              <li
                key={project.id}
                className="flex items-start justify-between gap-3 py-3 first:pt-0 last:pb-0"
              >
                <div className="flex min-w-0 items-start gap-2">
                  <FolderOpen aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fg-muted" />
                  <div className="min-w-0">
                    <Link
                      to={workspacePath(project.id)}
                      className="font-medium text-brand-600 hover:underline"
                    >
                      {project.name}
                    </Link>
                    <p className="font-mono text-xs text-fg-muted">{project.slug}</p>
                    <p className="text-xs text-fg-muted">
                      {languages.length > 0 ? languages.join(", ") : t("dashboard.recentProjects.noStack")} ·{" "}
                      {project.default_branch}
                    </p>
                  </div>
                </div>
                <Badge tone="success">
                  <CheckCircle2 aria-hidden="true" className="size-3.5" />
                  {t("dashboard.recentProjects.active")}
                </Badge>
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}
