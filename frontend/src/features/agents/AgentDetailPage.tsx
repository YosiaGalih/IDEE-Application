import { ArrowLeft } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { PERMISSION_FIELDS, type Agent, type AgentDetail } from "@/api/agents";
import { ApiError } from "@/api/errors";
import { useAgent } from "@/api/hooks/useAgents";
import { uxStateForError } from "@/api/uxState";
import { Badge } from "@/components/ui/Badge";
import { Card, CardTitle } from "@/components/ui/Card";
import { StateView } from "@/components/ui/StateView";
import { AGENTS_PATH, taskPath } from "@/lib/paths";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { AgentStatusBadge, reasonsText } from "./agentStatus";

/** `/agents/:agentId`: what one agent is for, what it may touch, and its recent runs (FR-044–046). */
export function AgentDetailPage() {
  const { t } = useTranslation();
  const { agentId = "" } = useParams();
  const query = useAgent(agentId);
  useDocumentTitle(query.data ? `${query.data.agent.name} · ${t("screens.agents")}` : t("screens.agents"));

  let body: React.ReactNode;
  if (query.isPending) {
    body = <StateView state="loading" />;
  } else if (query.isError) {
    body = (
      <StateView
        state={uxStateForError(query.error)}
        requestId={query.error instanceof ApiError ? query.error.requestId : null}
        onAction={() => void query.refetch()}
        titleAs="h1"
      />
    );
  } else {
    body = <Detail detail={query.data} />;
  }
  return (
    <div className="flex flex-col gap-4 p-6">
      <Link to={AGENTS_PATH} className="flex items-center gap-1 text-sm text-fg-muted hover:text-fg">
        <ArrowLeft aria-hidden="true" className="size-4" />
        {t("agents.detail.back")}
      </Link>
      {body}
    </div>
  );
}

function Detail({ detail }: { detail: AgentDetail }) {
  const { t } = useTranslation();
  const { agent } = detail;
  const reasons = reasonsText(t, agent);
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold text-fg">
          {t("agents.detail.heading", { name: agent.name, id: agent.agent_id })}
        </h1>
        <span className="flex flex-wrap items-center gap-1.5">
          <AgentStatusBadge status={agent.status} />
          <Badge tone="info">{t(`agents.modes.${agent.mvp_mode}`)}</Badge>
          <Badge tone="neutral">{t("agents.card.priority", { priority: agent.priority })}</Badge>
          <Badge tone="neutral">{t("agents.detail.version", { version: agent.version })}</Badge>
        </span>
        {reasons ? <p className="text-sm text-fg-muted">{reasons}</p> : null}
        {agent.runtime_agent ? (
          <p className="text-sm text-fg-muted">
            {t("agents.detail.runtimeAgent", { agent: agent.runtime_agent })}
          </p>
        ) : null}
      </header>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardTitle>{t("agents.detail.purpose")}</CardTitle>
          <p className="mt-2 text-sm text-fg">{agent.purpose}</p>
          <h3 className="mt-4 text-sm font-semibold text-fg">{t("agents.detail.description")}</h3>
          <p className="mt-1 text-sm text-fg-muted">{agent.description}</p>
          <h3 className="mt-4 text-sm font-semibold text-fg">{t("agents.detail.accessNote")}</h3>
          <p className="mt-1 text-sm text-fg-muted">{agent.access_note}</p>
        </Card>
        <Permissions agent={agent} />
        <Card>
          <CardTitle>{t("agents.detail.tools")}</CardTitle>
          <Tools agent={agent} />
        </Card>
        <Card>
          <CardTitle>{t("agents.detail.limits")}</CardTitle>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <dt className="text-fg-muted">{t("agents.detail.maxIterations")}</dt>
            <dd>{agent.limits.max_iterations}</dd>
            <dt className="text-fg-muted">{t("agents.detail.timeout")}</dt>
            <dd>{t("agents.detail.seconds", { count: agent.limits.timeout_seconds })}</dd>
            <dt className="text-fg-muted">{t("agents.detail.modelTier")}</dt>
            <dd>{t(`agents.tiers.${agent.model_tier}`, { defaultValue: agent.model_tier })}</dd>
            <dt className="text-fg-muted">{t("agents.detail.input")}</dt>
            <dd>{agent.input_contract ? <code>{agent.input_contract}</code> : t("agents.detail.none")}</dd>
            <dt className="text-fg-muted">{t("agents.detail.output")}</dt>
            <dd>{agent.output_contract ? <code>{agent.output_contract}</code> : t("agents.detail.none")}</dd>
          </dl>
          <h3 className="mt-4 text-sm font-semibold text-fg">{t("agents.detail.triggers")}</h3>
          <TokenList values={agent.triggers} />
          <h3 className="mt-4 text-sm font-semibold text-fg">{t("agents.detail.capabilities")}</h3>
          <TokenList values={agent.capabilities} />
        </Card>
      </div>
      <Runs detail={detail} />
    </div>
  );
}

function TokenList({ values }: { values: string[] }) {
  return (
    <ul className="mt-1 flex flex-wrap gap-1.5">
      {values.map((value) => (
        <li key={value}>
          <code className="rounded bg-surface-muted px-1.5 py-0.5 text-xs">{value}</code>
        </li>
      ))}
    </ul>
  );
}

function Permissions({ agent }: { agent: Agent }) {
  const { t } = useTranslation();
  const titleId = `agent-permissions-${agent.agent_id}`;
  return (
    <Card>
      <CardTitle id={titleId}>{t("agents.detail.permissions")}</CardTitle>
      <table aria-labelledby={titleId} className="mt-2 w-full text-sm">
        <thead className="sr-only">
          <tr>
            <th scope="col">{t("agents.detail.permission")}</th>
            <th scope="col">{t("agents.detail.value")}</th>
          </tr>
        </thead>
        <tbody>
          {PERMISSION_FIELDS.map((field) => (
            <tr key={field} className="border-b border-line last:border-0">
              <th scope="row" className="py-1 pr-4 text-left font-normal text-fg-muted">
                {t(`agents.permissionFields.${field}`)}
              </th>
              <td className="py-1">
                <code>{agent.permission_profile[field]}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Tools({ agent }: { agent: Agent }) {
  const { t } = useTranslation();
  if (agent.tools.length === 0)
    return <p className="mt-2 text-sm text-fg-muted">{t("agents.detail.noTools")}</p>;
  const unavailable = new Set(agent.unavailable_tools);
  return (
    <ul aria-label={t("agents.detail.tools")} className="mt-2 flex flex-col gap-1 text-sm">
      {agent.tools.map((tool) => (
        <li key={tool} className="flex items-center gap-2">
          <code>{tool}</code>
          {unavailable.has(tool) ? <Badge tone="warning">{t("agents.detail.toolUnavailable")}</Badge> : null}
        </li>
      ))}
    </ul>
  );
}

function Runs({ detail }: { detail: AgentDetail }) {
  const { t, i18n } = useTranslation();
  const { recent_runs: runs, stats } = detail;
  const percent = new Intl.NumberFormat(i18n.language, { style: "percent", maximumFractionDigits: 0 });
  const seconds = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 });
  const when = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" });
  const titleId = "agent-runs-title";
  return (
    <Card>
      <CardTitle id={titleId}>{t("agents.detail.runs")}</CardTitle>
      <p className="mt-1 text-xs text-fg-muted">{t("agents.detail.runsScope")}</p>
      <dl aria-label={t("agents.detail.stats")} className="mt-3 grid grid-cols-3 gap-2 text-sm">
        <div>
          <dt className="text-fg-muted">{t("agents.detail.statRuns")}</dt>
          <dd>{stats.runs}</dd>
        </div>
        <div>
          <dt className="text-fg-muted">{t("agents.detail.statSuccess")}</dt>
          <dd>
            {stats.success_rate === null
              ? t("agents.detail.notAvailable")
              : percent.format(stats.success_rate)}
          </dd>
        </div>
        <div>
          <dt className="text-fg-muted">{t("agents.detail.statDuration")}</dt>
          <dd>
            {stats.avg_duration_ms === null
              ? t("agents.detail.notAvailable")
              : t("agents.detail.durationValue", { seconds: seconds.format(stats.avg_duration_ms / 1000) })}
          </dd>
        </div>
      </dl>
      {runs.length === 0 ? (
        <p className="mt-3 text-sm text-fg-muted">{t("agents.detail.noRuns")}</p>
      ) : (
        <table aria-labelledby={titleId} className="mt-3 w-full text-sm">
          <thead>
            <tr className="text-left text-fg-muted">
              <th scope="col" className="py-1">
                {t("agents.detail.started")}
              </th>
              <th scope="col" className="py-1">
                {t("agents.detail.task")}
              </th>
              <th scope="col" className="py-1">
                {t("agents.detail.runStatusColumn")}
              </th>
              <th scope="col" className="py-1">
                {t("agents.detail.duration")}
              </th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} className="border-t border-line">
                <td className="py-1">{when.format(new Date(run.started_at))}</td>
                <td className="py-1">
                  <Link to={taskPath(run.task_id)} className="underline">
                    <code>{run.task_id.slice(0, 8)}</code>
                  </Link>
                </td>
                <td className="py-1">{t(`agents.runStatus.${run.status}`)}</td>
                <td className="py-1">
                  {run.duration_ms === null
                    ? t("agents.detail.notAvailable")
                    : t("agents.detail.durationValue", { seconds: seconds.format(run.duration_ms / 1000) })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
