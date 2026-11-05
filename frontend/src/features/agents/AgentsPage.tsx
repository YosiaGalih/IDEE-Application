import { ShieldCheck, ShieldAlert } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import {
  AGENT_GROUPS,
  AGENT_MODES,
  AGENT_STATUSES,
  type Agent,
  type AgentMode,
  type AgentStatus,
} from "@/api/agents";
import { ApiError } from "@/api/errors";
import { useAgents } from "@/api/hooks/useAgents";
import { uxStateForError } from "@/api/uxState";
import { Badge } from "@/components/ui/Badge";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { StateView } from "@/components/ui/StateView";
import { cn } from "@/lib/cn";
import { agentPath } from "@/lib/paths";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { AgentStatusBadge, reasonsText } from "./agentStatus";

const matches = (agent: Agent, needle: string) =>
  [agent.agent_id, agent.slug, agent.name, ...agent.tools].some((value) =>
    value.toLocaleLowerCase().includes(needle),
  );

/** `/agents`: the agent catalog (FR-044, FR-045). Status always says why an agent cannot run yet. */
export function AgentsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("screens.agents"));
  const agents = useAgents();
  const [mode, setMode] = useState<AgentMode | null>(null);
  const [status, setStatus] = useState<AgentStatus | "">("");
  const [query, setQuery] = useState("");
  const searchId = useId();
  const statusId = useId();

  let body: React.ReactNode;
  if (agents.isPending) {
    body = <StateView state="loading" />;
  } else if (agents.isError) {
    body = (
      <StateView
        state={uxStateForError(agents.error)}
        requestId={agents.error instanceof ApiError ? agents.error.requestId : null}
        onAction={() => void agents.refetch()}
      />
    );
  } else {
    const { agents: all, counts } = agents.data;
    const needle = query.trim().toLocaleLowerCase();
    const shown = all.filter(
      (agent) =>
        (mode === null || agent.mvp_mode === mode) &&
        (status === "" || agent.status === status) &&
        (needle === "" || matches(agent, needle)),
    );
    // Derived from the data, never asserted: one agent able to approve turns the statement into a warning.
    const approvers = all.filter((agent) => agent.permission_profile.approval_grant !== "deny").length;
    body = (
      <div className="flex flex-col gap-5">
        <p className="text-sm text-fg-muted">
          {t("agents.subtitle", {
            count: all.length,
            ready: counts.status.ready,
            notReady: counts.status.not_ready,
            disabled: counts.status.disabled,
            planned: counts.status.planned,
          })}
        </p>
        {approvers > 0 ? (
          <p role="alert" className="flex items-center gap-2 text-sm font-medium text-status-danger">
            <ShieldAlert aria-hidden="true" className="size-4" />
            {t("agents.approvalWarning", { count: approvers })}
          </p>
        ) : all.length > 0 ? (
          // An empty catalog proves nothing about approvals, so it gets no statement.
          <p className="flex items-center gap-2 text-sm text-status-success">
            <ShieldCheck aria-hidden="true" className="size-4" />
            {t("agents.approvalStatement")}
          </p>
        ) : null}
        <div role="group" aria-label={t("agents.filters.label")} className="flex flex-wrap items-end gap-3">
          <div className="flex flex-wrap gap-2">
            <ModeChip
              label={t("agents.filters.all")}
              count={all.length}
              isPressed={mode === null}
              onPress={() => setMode(null)}
            />
            {AGENT_MODES.map((value) => (
              <ModeChip
                key={value}
                label={t(`agents.modes.${value}`)}
                count={counts.mode[value]}
                isPressed={mode === value}
                onPress={() => setMode(value)}
              />
            ))}
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor={statusId}>{t("agents.filters.status")}</Label>
            <select
              id={statusId}
              value={status}
              onChange={(event) => setStatus(event.target.value as AgentStatus | "")}
              className="h-9 rounded-md border border-line bg-surface px-2 text-sm"
            >
              <option value="">{t("agents.filters.anyStatus")}</option>
              {AGENT_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {t(`agents.status.${value}`)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex min-w-60 flex-1 flex-col gap-1">
            <Label htmlFor={searchId}>{t("agents.filters.search")}</Label>
            <Input
              id={searchId}
              type="search"
              value={query}
              placeholder={t("agents.filters.searchPlaceholder")}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        </div>
        {shown.length === 0 ? (
          <StateView state="empty" title={t("agents.noMatch.title")} detail={t("agents.noMatch.body")} />
        ) : (
          AGENT_GROUPS.map((group) => {
            const members = shown.filter((agent) => agent.group === group);
            if (members.length === 0) return null;
            const headingId = `agents-group-${group}`;
            return (
              <section key={group} aria-labelledby={headingId} className="flex flex-col gap-2">
                <h2 id={headingId} className="text-base font-semibold text-fg">
                  {t("agents.groupHeading", { group: t(`agents.groups.${group}`), count: members.length })}
                </h2>
                <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {members.map((agent) => (
                    <li key={agent.agent_id}>
                      <AgentCard agent={agent} />
                    </li>
                  ))}
                </ul>
              </section>
            );
          })
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 p-6">
      <h1 className="text-xl font-semibold text-fg">{t("screens.agents")}</h1>
      {body}
    </div>
  );
}

type ChipProps = { label: string; count: number; isPressed: boolean; onPress: () => void };

function ModeChip({ label, count, isPressed, onPress }: ChipProps) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      aria-pressed={isPressed}
      onClick={onPress}
      className={cn(
        "rounded-full border px-3 py-1 text-sm",
        isPressed ? "border-brand bg-brand text-on-brand" : "border-line bg-surface text-fg",
      )}
    >
      {t("agents.filters.chip", { label, count })}
    </button>
  );
}

function AgentCard({ agent }: { agent: Agent }) {
  const { t } = useTranslation();
  const reasons = reasonsText(t, agent);
  return (
    <Link
      to={agentPath(agent.agent_id)}
      className="flex h-full flex-col gap-2 rounded-[var(--radius-card)] border border-line bg-surface p-3 hover:border-brand"
    >
      <span className="flex flex-wrap items-center gap-1.5">
        <Badge tone="neutral">{agent.agent_id}</Badge>
        <Badge tone="neutral">{t("agents.card.priority", { priority: agent.priority })}</Badge>
        <Badge tone="info">{t(`agents.modes.${agent.mvp_mode}`)}</Badge>
      </span>
      <span className="font-medium text-fg">{agent.name}</span>
      <AgentStatusBadge status={agent.status} />
      {reasons ? <span className="text-xs text-fg-muted">{reasons}</span> : null}
    </Link>
  );
}
