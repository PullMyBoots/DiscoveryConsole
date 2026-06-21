import { useEffect, useState } from "react";
import { api, type Attempt, type TaskConfig, type RunStatus, type LogData, type LogEntry, type EvalJob, type EvalJobsResponse } from "../lib/api";
import { useSSE } from "../hooks/useSSE";
import { useReplay } from "../hooks/useReplay";
import ScoreChart from "../components/ScoreChart";
import ChartModal from "../components/ChartModal";
import AttemptRow from "../components/AttemptRow";
import StatusBadge from "../components/StatusBadge";
import ReplayBar from "../components/ReplayBar";
import { scoreComponents, scoreLabel, scoreMetricNames, scoreValue } from "../lib/scores";
import { EmptyState, PageTitle, Panel, inputClass } from "../components/Ui";

type SortKey = "score" | "agent_id" | "timestamp";

function formatDuration(seconds?: number | null): string {
  if (seconds == null) return "-";
  const safe = Math.max(0, Math.round(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const secs = safe % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function formatTokens(value?: number | null): string {
  if (value == null) return "-";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(Math.round(value));
}

function formatCost(value?: number | null): string {
  if (value == null || value <= 0) return "-";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

function formatPercent(value?: number | null): string {
  if (value == null) return "-";
  return `${Math.round(value * 100)}%`;
}

function formatScoreValue(value?: number | null): string {
  if (value == null) return "---";
  if (Math.abs(value) >= 1000 || Math.abs(value) < 0.0001) {
    return value.toPrecision(4);
  }
  return String(Number(value.toFixed(5)));
}

function formatEvalResourcePool(evalState: EvalJobsResponse | null): string {
  if (!evalState) return "";
  const pool = evalState.resource_pool ?? {};
  const parts = [`${evalState.max_workers} worker${evalState.max_workers === 1 ? "" : "s"}`];
  const cpu = Number((pool as Record<string, unknown>).cpu_cores ?? 0);
  const memory = Number((pool as Record<string, unknown>).memory_gb ?? 0);
  const gpu = Number((pool as Record<string, unknown>).gpu_count ?? 0);
  const gpuIdsRaw = (pool as Record<string, unknown>).gpu_ids;
  const gpuIds = Array.isArray(gpuIdsRaw) ? gpuIdsRaw.map(String).filter(Boolean) : [];
  if (gpu > 0) parts.push(`${gpu} GPU${gpu === 1 ? "" : "s"}`);
  if (gpuIds.length > 0) parts.push(`[${gpuIds.join(",")}]`);
  if (cpu > 0) parts.push(`${cpu} CPU`);
  if (memory > 0) parts.push(`${Number.isInteger(memory) ? memory : memory.toFixed(1)} GB`);
  return parts.join(" · ");
}

export default function Overview() {
  const [config, setConfig] = useState<TaskConfig | null>(null);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [evalJobs, setEvalJobs] = useState<EvalJob[]>([]);
  const [evalState, setEvalState] = useState<EvalJobsResponse | null>(null);
  const [agentLogs, setAgentLogs] = useState<Record<string, LogData>>({});
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortAsc, setSortAsc] = useState(false);
  const [expandedHash, setExpandedHash] = useState<string | null>(null);
  const [chartExpanded, setChartExpanded] = useState(false);
  const [tableExpanded, setTableExpanded] = useState(false);
  const [scoreMetric, setScoreMetric] = useState("score");
  const [selectedAttempt, setSelectedAttempt] = useState<Attempt | null>(null);
  const [agentActions, setAgentActions] = useState<Record<string, string>>({});
  const [agentPrompts, setAgentPrompts] = useState<Record<string, string>>({});
  const [agentPromptActions, setAgentPromptActions] = useState<Record<string, boolean>>({});

  const refresh = () => {
    api.config().then(setConfig).catch(() => {});
    api.attempts().then(setAttempts).catch(() => {});
    api.status().then(setStatus).catch(() => {});
    api.evals().then((data) => {
      setEvalState(data);
      setEvalJobs(data.jobs);
    }).catch(() => {});
  };

  const refreshLogs = () => {
    if (!status) return;
    for (const agent of status.agents) {
      api.logs(agent.agent_id).then((data) => {
        setAgentLogs((prev) => ({ ...prev, [agent.agent_id]: data }));
      }).catch(() => {});
    }
  };

  const refreshEvals = () => {
    api.evals().then((data) => {
      setEvalState(data);
      setEvalJobs(data.jobs);
    }).catch(() => {});
  };

  const requestAgentState = (agentId: string, desired: "running" | "stopped") => {
    setAgentActions((prev) => ({ ...prev, [agentId]: desired }));
    const action = desired === "stopped" ? api.agentStop(agentId) : api.agentResume(agentId);
    action
      .then(() => api.status().then(setStatus))
      .catch(() => {})
      .finally(() => {
        setAgentActions((prev) => {
          const next = { ...prev };
          delete next[agentId];
          return next;
        });
      });
  };

  const sendAgentPrompt = (agentId: string) => {
    const prompt = (agentPrompts[agentId] || "").trim();
    if (!prompt) return;
    setAgentPromptActions((prev) => ({ ...prev, [agentId]: true }));
    api.agentPrompt(agentId, prompt)
      .then(() => {
        setAgentPrompts((prev) => ({ ...prev, [agentId]: "" }));
        return api.status().then(setStatus);
      })
      .catch(() => {})
      .finally(() => {
        setAgentPromptActions((prev) => {
          const next = { ...prev };
          delete next[agentId];
          return next;
        });
      });
  };

  useEffect(refresh, []);
  useEffect(refreshLogs, [status]);
  useSSE({
    "attempt:new": refresh,
    "attempt:update": refresh,
    "eval:update": refresh,
    "eval:progress": refreshEvals,
    "note:update": refresh,
    "log:update": refreshLogs,
  });

  const replay = useReplay(attempts);
  const displayAttempts = replay.visibleAttempts;

  const metricOptions = ["score", ...scoreMetricNames(displayAttempts)];
  const selectedMetric = metricOptions.includes(scoreMetric) ? scoreMetric : "score";
  const scored = displayAttempts.filter((a) => scoreValue(a, selectedMetric) !== null);

  const allSorted = [...displayAttempts].sort((a, b) => {
    if (sortKey === "score") {
      const av = scoreValue(a, selectedMetric);
      const bv = scoreValue(b, selectedMetric);
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
    }
    let cmp = 0;
    switch (sortKey) {
      case "score":
        cmp = scoreValue(a, selectedMetric)! - scoreValue(b, selectedMetric)!;
        break;
      case "agent_id":
        cmp = a.agent_id.localeCompare(b.agent_id);
        break;
      case "timestamp":
        cmp = a.timestamp.localeCompare(b.timestamp);
        break;
    }
    return sortAsc ? cmp : -cmp;
  });

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else {
      setSortKey(key);
      setSortAsc(key === "timestamp" || key === "agent_id");
    }
  };

  const direction =
    config?.grader?.direction === "minimize" ? "minimize" : "maximize";

  // Get last 3 tool_call/text entries across all turns for an agent
  const getRecentEntries = (agentId: string): { turnIndex: number; entry: LogEntry }[] => {
    const data = agentLogs[agentId];
    if (!data || data.turns.length === 0) return [];
    const results: { turnIndex: number; entry: LogEntry }[] = [];
    for (let i = data.turns.length - 1; i >= 0 && results.length < 3; i--) {
      const turn = data.turns[i];
      for (let j = turn.entries.length - 1; j >= 0 && results.length < 3; j--) {
        const e = turn.entries[j];
        if (e.type === "tool_call" || e.type === "text") {
          results.push({ turnIndex: turn.index, entry: e });
        }
      }
    }
    return results;
  };

  const replayAgents = (() => {
    if (!replay.active || displayAttempts.length === 0) return [];
    const minimize = direction === "minimize";
    const map = new Map<string, { count: number; best: number | null; lastStatus: string }>();
    for (const a of displayAttempts) {
      const cur = map.get(a.agent_id);
      if (!cur) {
        map.set(a.agent_id, { count: 1, best: a.score, lastStatus: a.status });
      } else {
        cur.count++;
        cur.lastStatus = a.status;
        if (a.score != null && (cur.best == null || (minimize ? a.score < cur.best : a.score > cur.best))) {
          cur.best = a.score;
        }
      }
    }
    return [...map.entries()].map(([id, d]) => ({
      agent_id: id,
      attempts: d.count,
      best_score: d.best,
      last_status: d.lastStatus,
      recent: displayAttempts.filter(a => a.agent_id === id).slice(-3),
    }));
  })();

  const agentGroups = (() => {
    if (!status) return [];
    const groups = new Map<string, typeof status.agents>();
    for (const agent of status.agents) {
      const key = agent.island_id ?? "shared";
      const current = groups.get(key) ?? [];
      current.push(agent);
      groups.set(key, current);
    }
    return [...groups.entries()]
      .map(([key, agents]) => ({
        key,
        label: key === "shared" ? "Shared" : `Island ${key}`,
        agents: [...agents].sort((a, b) => a.agent_id.localeCompare(b.agent_id)),
      }))
      .sort((a, b) => {
        if (a.key === "shared") return 1;
        if (b.key === "shared") return -1;
        return a.key.localeCompare(b.key, undefined, { numeric: true });
      });
  })();
  const showAgentGroups =
    agentGroups.length > 1 || agentGroups.some((group) => group.key !== "shared");
  const attemptsByAgent = (() => {
    const map = new Map<string, Attempt[]>();
    for (const attempt of displayAttempts) {
      const current = map.get(attempt.agent_id) ?? [];
      current.push(attempt);
      map.set(attempt.agent_id, current);
    }
    for (const entries of map.values()) {
      entries.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
    }
    return map;
  })();
  const evalJobByAgent = (() => {
    const map = new Map<string, EvalJob>();
    for (const job of evalJobs) {
      const current = map.get(job.agent_id);
      if (!current) {
        map.set(job.agent_id, job);
        continue;
      }
      if (current.queue_status === "waiting" && job.queue_status === "evaluating") {
        map.set(job.agent_id, job);
      }
    }
    return map;
  })();

  return (
    <>
      <div className="control-scroll h-full min-h-0 overflow-y-scroll p-4 sm:p-5">
        <div className="mx-auto grid max-w-[1480px] gap-5">
        <PageTitle icon="chart" title="Overview" subtitle="Scores, attempts, agents, and evaluation queue" />
        {/* Score chart — always shown */}
        <div className="mb-5">
          <Panel>
          <div className="relative">
            <div className="absolute top-2.5 right-2.5 flex items-center gap-1 z-10">
              {!replay.active && attempts.length > 0 && (
                <button
                  onClick={replay.start}
                  className="w-6 h-6 flex items-center justify-center rounded-md hover:bg-muted transition-colors duration-100 text-muted-fg hover:text-foreground"
                  title="Replay evolution"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                </button>
              )}
              <button
                onClick={() => setChartExpanded(true)}
                className="w-6 h-6 flex items-center justify-center rounded-md hover:bg-muted transition-colors duration-100 text-muted-fg hover:text-foreground"
                title="Expand chart"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                </svg>
              </button>
            </div>
            <ScoreChart
              attempts={displayAttempts}
              height={200}
              direction={direction}
              animationDuration={replay.active ? 200 : undefined}
              metric={selectedMetric}
              onSelectAttempt={setSelectedAttempt}
            />
          </div>
          </Panel>
        </div>

        <div className="grid gap-5">
          <div className="grid gap-5">
        <div className="grid gap-3">
          <div className="flex items-center justify-between gap-3">
            <label className="flex min-w-0 flex-1 items-center gap-2">
              <span className="shrink-0 font-mono text-[10px] uppercase tracking-widest text-muted-fg">
                Metric
              </span>
              <select
                value={selectedMetric}
                onChange={(event) => {
                  setScoreMetric(event.target.value);
                  setSelectedAttempt(null);
                }}
                className={`${inputClass} min-w-0 flex-1 py-1.5 font-mono text-[11px]`}
              >
                {metricOptions.map((metric) => (
                  <option key={metric} value={metric}>
                    {scoreLabel(metric)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {selectedAttempt && (
            <SelectedAttemptPanel attempt={selectedAttempt} metric={selectedMetric} />
          )}
        </div>

        {chartExpanded && (
          <ChartModal
            attempts={displayAttempts}
            direction={direction}
            metric={selectedMetric}
            onSelectAttempt={setSelectedAttempt}
            onClose={() => setChartExpanded(false)}
          />
        )}

        {/* Leaderboard */}
        <div>
          <Panel
            title="Attempts"
            icon="activity"
            action={
              <p className="font-mono text-[11px] text-muted-fg">
                {scored.length} scored / {displayAttempts.length} total
              </p>
            }
          >
          <div className="overflow-hidden rounded-xl border border-border bg-surface/85">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border bg-muted/50">
                  <Th label="#" />
                  <Th
                    label={selectedMetric === "score" ? "Score" : selectedMetric}
                    active={sortKey === "score"}
                    asc={sortAsc}
                    onClick={() => toggleSort("score")}
                  />
                  <Th
                    label="Agent"
                    active={sortKey === "agent_id"}
                    asc={sortAsc}
                    onClick={() => toggleSort("agent_id")}
                  />
                  <Th label="Status" />
                  <Th
                    label="Time"
                    active={sortKey === "timestamp"}
                    asc={sortAsc}
                    onClick={() => toggleSort("timestamp")}
                  />
                </tr>
              </thead>
              <tbody>
                {(tableExpanded ? allSorted : allSorted.slice(0, 5)).map((a, i) => (
                  <AttemptRow
                    key={a.commit_hash}
                    attempt={a}
                    rank={i + 1}
                    expanded={expandedHash === a.commit_hash}
                    onToggle={() =>
                      setExpandedHash(
                        expandedHash === a.commit_hash ? null : a.commit_hash
                      )
                    }
                    metric={selectedMetric}
                    highlight={
                      replay.active &&
                      replay.latestAttempt?.commit_hash === a.commit_hash
                    }
                  />
                ))}
              </tbody>
            </table>

            {allSorted.length === 0 && (
              <div className="p-4">
                <EmptyState icon="activity" title="No attempts yet" />
              </div>
            )}

            {allSorted.length > 5 && (
              <button
                onClick={() => setTableExpanded(!tableExpanded)}
                className="w-full py-2 font-mono text-[10px] tracking-widest uppercase text-muted-fg hover:text-foreground hover:bg-muted/50 transition-colors duration-100 border-t border-border"
              >
                {tableExpanded
                  ? "Show less"
                  : `Show all ${allSorted.length} attempts`}
              </button>
            )}
          </div>
          </Panel>
        </div>

          </div>

          <div className="grid content-start gap-5">
        {replay.active ? (
          <>
            {/* Replay: Agent cards derived from visible attempts */}
            {replayAgents.length > 0 && (
              <div>
                <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-3">
                  Agents
                </p>
                <div className="space-y-2">
                  {replayAgents.map((agent) => (
                    <div
                      key={agent.agent_id}
                      className="p-3 border border-border rounded-lg bg-muted/50"
                    >
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="font-mono text-[12px] font-medium">
                          {agent.agent_id}
                        </span>
                        <StatusBadge status={agent.last_status} />
                      </div>
                      <div className="font-mono text-[11px] text-muted-fg flex flex-wrap gap-x-3 gap-y-1">
                        <span>{agent.attempts} att</span>
                        <span>
                          best{" "}
                          {agent.best_score != null
                            ? agent.best_score.toFixed(4)
                            : "---"}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Replay: Recent attempts per agent */}
            {replayAgents.length > 0 && (
              <div>
                <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-3">
                  Recent Activity
                </p>
                <div className="space-y-3">
                  {replayAgents.map((agent) => (
                    <div
                      key={agent.agent_id}
                      className="p-3 border border-border rounded-lg"
                    >
                      <div className="flex items-center gap-2 mb-2">
                        <span className="font-mono text-[11px] font-medium">
                          {agent.agent_id}
                        </span>
                      </div>
                      <div className="space-y-1.5">
                        {agent.recent.map((a) => (
                          <div key={a.commit_hash} className="flex items-center gap-2">
                            <span className="font-mono text-[11px] font-medium shrink-0 min-w-[52px]">
                              {a.score != null ? a.score.toFixed(4) : "---"}
                            </span>
                            <StatusBadge status={a.status} />
                            <span className="font-body text-[11px] text-muted-fg truncate">
                              {a.title}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {displayAttempts.length === 0 && (
              <p className="py-8 text-center font-mono text-xs text-muted-fg">
                Replaying...
              </p>
            )}
          </>
        ) : (
          <>
            {status?.run_state && <RunClock status={status} />}

            {/* Eval queue */}
            <EvalQueue jobs={evalJobs} evalState={evalState} />

            {/* Agent cards */}
            {status && status.agents.length > 0 && (
              <div>
                <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-3">
                  Agents
                </p>
                <div className="space-y-4">
                  {agentGroups.map((group) => (
                    <div key={group.key}>
                      {showAgentGroups && (
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <p className="font-mono text-[10px] uppercase tracking-widest text-muted-fg">
                            {group.label}
                          </p>
                          <span className="font-mono text-[10px] text-muted-fg">
                            {group.agents.length} agent{group.agents.length === 1 ? "" : "s"}
                          </span>
                        </div>
                      )}
                      <div className="space-y-2">
                        {group.agents.map((agent) => {
                          const agentAttempts = attemptsByAgent.get(agent.agent_id) ?? [];
                          const recentAttempts = agentAttempts.slice(-3).reverse();
                          const activeEvalJob = evalJobByAgent.get(agent.agent_id);
                          const stopped =
                            agent.status === "stopped" ||
                            agent.status === "paused" ||
                            agent.runtime_state === "stopped";
                          const pendingDesired =
                            (agent.desired_state === "stopped" && !stopped) ||
                            (agent.desired_state === "running" && stopped);
                          const actionPending =
                            Boolean(agentActions[agent.agent_id]) || pendingDesired;
                          const nextDesired = stopped ? "running" : "stopped";
                          return (
                            <div
                              key={agent.agent_id}
                              className="p-3 border border-border rounded-lg bg-muted/50"
                            >
                              <div className="flex items-center justify-between gap-2 mb-1.5">
                                <div className="min-w-0">
                                  <span className="font-mono text-[12px] font-medium">
                                    {agent.agent_id}
                                  </span>
                                  {!showAgentGroups && agent.island_id && (
                                    <span className="ml-2 font-mono text-[10px] text-muted-fg">
                                      island {agent.island_id}
                                    </span>
                                  )}
                                </div>
                                <div className="flex shrink-0 items-center gap-1.5">
                                  <StatusBadge status={agent.status} />
                                  {status.manager_alive && (
                                    <button
                                      onClick={() => requestAgentState(agent.agent_id, nextDesired)}
                                      disabled={actionPending}
                                      className="rounded-md border border-border bg-background px-2 py-0.5 font-mono text-[9px] uppercase tracking-widest text-muted-fg transition-colors duration-100 hover:text-foreground disabled:opacity-50"
                                      title={
                                        stopped
                                          ? "Resume this agent"
                                          : "Stop this agent"
                                      }
                                    >
                                      {actionPending
                                        ? "Queued"
                                        : stopped
                                          ? "Resume"
                                          : "Stop"}
                                    </button>
                                  )}
                                </div>
                              </div>
                              <div className="font-mono text-[11px] text-muted-fg flex gap-3">
                                <span>{agent.attempts} att</span>
                                <span>{agent.sessions} sess</span>
                                <span>run {formatDuration(agent.active_seconds)}</span>
                                <span>last {formatDuration(agent.last_activity_age_seconds)}</span>
                                {agent.status_duration_seconds != null && (
                                  <span>{agent.status} {formatDuration(agent.status_duration_seconds)}</span>
                                )}
                                <span>
                                  best{" "}
                                  {agent.best_score != null
                                    ? agent.best_score.toFixed(4)
                                    : "---"}
                                </span>
                              </div>
                              {agent.usage && (
                                <div className="font-mono text-[11px] text-muted-fg flex flex-wrap gap-x-3 gap-y-1 mt-1">
                                  <span>{formatTokens(agent.usage.total_tokens)} tok</span>
                                  <span>{formatCost(agent.usage.total_cost_usd)}</span>
                                  <span>cache {formatPercent(agent.usage.cache_hit_rate)}</span>
                                </div>
                              )}
                              {activeEvalJob && <AgentEvalProgress job={activeEvalJob} />}
                              <AgentAttemptSummary
                                attempts={recentAttempts}
                                metric={selectedMetric}
                              />
                              {status.manager_alive && (
                                <div className="mt-2 flex items-end gap-2">
                                  <textarea
                                    value={agentPrompts[agent.agent_id] || ""}
                                    onChange={(event) =>
                                      setAgentPrompts((prev) => ({
                                        ...prev,
                                        [agent.agent_id]: event.target.value,
                                      }))
                                    }
                                    rows={2}
                                    placeholder="Message this agent"
                                    className="min-h-[46px] flex-1 resize-y rounded-md border border-border bg-background px-2 py-1.5 font-body text-[11px] leading-snug outline-none placeholder:text-muted-fg"
                                  />
                                  <button
                                    onClick={() => sendAgentPrompt(agent.agent_id)}
                                    disabled={
                                      agentPromptActions[agent.agent_id] ||
                                      !(agentPrompts[agent.agent_id] || "").trim()
                                    }
                                    className="h-[30px] shrink-0 rounded-md bg-accent px-2.5 font-mono text-[9px] uppercase tracking-widest text-white transition-colors hover:bg-accent-fg disabled:opacity-40"
                                    title="Interrupt or resume this agent with a message"
                                  >
                                    {agentPromptActions[agent.agent_id] ? "Queued" : "Send"}
                                  </button>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Recent Agent Logs — last 3 entries per agent */}
            {status && status.agents.length > 0 && (
              <div>
                <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-3">
                  Recent Activity
                </p>
                <div className="space-y-3">
                  {status.agents.map((agent) => {
                    const entries = getRecentEntries(agent.agent_id);
                    if (entries.length === 0) return (
                      <div key={agent.agent_id} className="p-3 border border-border rounded-lg">
                        <span className="font-mono text-[11px] font-medium">{agent.agent_id}</span>
                        <p className="font-mono text-[10px] text-muted-fg mt-1">Waiting for activity...</p>
                      </div>
                    );
                    return (
                      <div
                        key={agent.agent_id}
                        className="p-3 border border-border rounded-lg"
                      >
                        <div className="flex items-center gap-2 mb-2">
                          <span className="font-mono text-[11px] font-medium">
                            {agent.agent_id}
                          </span>
                        </div>
                        <div className="space-y-1.5">
                          {entries.map(({ turnIndex, entry }, i) => (
                            <div key={i} className="flex items-center gap-2">
                              <span className="font-mono text-[9px] text-muted-fg shrink-0">
                                T{turnIndex + 1}
                              </span>
                              {entry.type === "tool_call" && (
                                <>
                                  <span className="font-mono text-[10px] bg-accent-soft text-accent-fg px-1.5 py-0.5 rounded-md shrink-0">
                                    {entry.content}
                                  </span>
                                  <span className="font-mono text-[10px] text-muted-fg truncate">
                                    {entry.details?.input_summary}
                                  </span>
                                </>
                              )}
                              {entry.type === "text" && (
                                <p className="font-body text-[11px] text-muted-fg truncate">
                                  {entry.content}
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </>
        )}
          </div>
        </div>
      </div>
    </div>

      <ReplayBar replay={replay} />
    </>
  );
}

function Th({
  label,
  active,
  asc,
  onClick,
}: {
  label: string;
  active?: boolean;
  asc?: boolean;
  onClick?: () => void;
}) {
  return (
    <th
      className={`py-2 px-3 text-left font-mono text-[10px] tracking-widest uppercase ${
        onClick ? "cursor-pointer hover:bg-muted select-none" : ""
      } ${active ? "text-foreground" : "text-muted-fg"}`}
      onClick={onClick}
    >
      {label}
      {active && <span className="ml-1">{asc ? "↑" : "↓"}</span>}
    </th>
  );
}

function SelectedAttemptPanel({ attempt, metric }: { attempt: Attempt; metric: string }) {
  const components = Object.entries(scoreComponents(attempt));
  const currentValue = scoreValue(attempt, metric);
  return (
    <div className="rounded-xl border border-border bg-muted/30 p-3">
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-display text-[13px] font-semibold">
            {attempt.title || attempt.commit_hash.slice(0, 8)}
          </p>
          <p className="font-mono text-[10px] text-muted-fg">
            {attempt.agent_id} · {attempt.commit_hash.slice(0, 8)}
          </p>
        </div>
        <div className="text-right">
          <p className="font-mono text-[10px] uppercase tracking-widest text-muted-fg">
            {scoreLabel(metric)}
          </p>
          <p className="font-mono text-[14px] font-medium">
            {currentValue == null ? "---" : String(currentValue)}
          </p>
        </div>
      </div>
      {components.length > 0 && (
        <div className="grid gap-2">
          {components.map(([name, component]) => (
            <div key={name} className="flex items-start justify-between gap-3 rounded-lg border border-border bg-background px-3 py-2">
              <div className="min-w-0">
                <p className="font-mono text-[11px] font-medium">{name}</p>
                {component.explanation && (
                  <p className="mt-0.5 line-clamp-2 font-body text-[11px] leading-relaxed text-muted-fg">
                    {component.explanation}
                  </p>
                )}
              </div>
              <span className="shrink-0 font-mono text-[12px]">
                {typeof component.value === "number" ? String(component.value) : "---"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AgentEvalProgress({ job }: { job: EvalJob }) {
  const percent = Math.max(0, Math.min(1, job.progress?.percent ?? 0));
  const current = job.progress?.current;
  const total = job.progress?.total;
  const progressLabel =
    current != null && total != null && total > 0
      ? `${current}/${total}`
      : job.queue_status === "evaluating"
        ? "running"
        : "queued";
  return (
    <div className="mt-3 rounded-lg border border-border bg-background px-3 py-2">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-body text-[11px] text-muted-fg">
            {job.title}
          </p>
          <p className="mt-0.5 font-mono text-[10px] text-muted-fg">
            {job.eval_profile || "profile"} · {job.commit_hash.slice(0, 8)}
          </p>
        </div>
        <span
          className={`shrink-0 rounded-md px-2 py-1 font-mono text-[9px] uppercase tracking-widest ${
            job.queue_status === "evaluating"
              ? "bg-accent text-white"
              : "border border-border text-muted-fg"
          }`}
        >
          {job.queue_status === "evaluating" ? "Evaluating" : "Waiting"}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-border">
        <div
          className="h-full bg-accent transition-[width] duration-300"
          style={{ width: `${job.queue_status === "evaluating" ? percent * 100 : 0}%` }}
        />
      </div>
      <div className="mt-1.5 flex items-center gap-3 font-mono text-[10px] text-muted-fg">
        <span>{progressLabel}</span>
        {job.progress?.phase && <span>{job.progress.phase}</span>}
        {job.progress?.message && (
          <span className="min-w-0 flex-1 truncate">{job.progress.message}</span>
        )}
      </div>
    </div>
  );
}

function AgentAttemptSummary({
  attempts,
  metric,
}: {
  attempts: Attempt[];
  metric: string;
}) {
  if (attempts.length === 0) {
    return (
      <div className="mt-3 rounded-lg border border-border bg-background px-3 py-2">
        <p className="font-mono text-[10px] text-muted-fg">No attempts yet</p>
      </div>
    );
  }
  return (
    <div className="mt-3 rounded-lg border border-border bg-background">
      <div className="border-b border-border px-3 py-1.5">
        <p className="font-mono text-[9px] uppercase tracking-widest text-muted-fg">
          Recent Attempts
        </p>
      </div>
      <div>
        {attempts.map((attempt) => {
          const value = scoreValue(attempt, metric);
          return (
            <div
              key={attempt.commit_hash}
              className="flex items-center gap-2 border-b border-border px-3 py-2 last:border-b-0"
            >
              <span className="shrink-0 min-w-[58px] font-mono text-[11px] font-medium">
                {formatScoreValue(value)}
              </span>
              <StatusBadge status={attempt.status} />
              <span className="min-w-0 flex-1 truncate font-body text-[11px] text-muted-fg">
                {attempt.title || attempt.commit_hash.slice(0, 8)}
              </span>
              <span className="shrink-0 font-mono text-[10px] text-muted-fg">
                {attempt.commit_hash.slice(0, 7)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RunClock({ status }: { status: RunStatus }) {
  const state = status.run_state;
  if (!state) return null;
  const maxRuntime = state.max_runtime_seconds ?? 0;
  const usage = status.usage;
  return (
    <div className="border border-border rounded-lg p-3">
      <div className="flex items-center justify-between gap-3 mb-2">
        <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg">
          Run
        </p>
        <StatusBadge status={state.status || (status.manager_alive ? "running" : "stopped")} />
      </div>
      <div className="grid grid-cols-3 gap-2 font-mono text-[11px] text-muted-fg">
        <div>
          <p className="text-[9px] uppercase tracking-widest">Elapsed</p>
          <p className="text-foreground">{formatDuration(state.elapsed_seconds)}</p>
        </div>
        <div>
          <p className="text-[9px] uppercase tracking-widest">Remaining</p>
          <p className="text-foreground">{formatDuration(state.remaining_seconds)}</p>
        </div>
        <div>
          <p className="text-[9px] uppercase tracking-widest">Limit</p>
          <p className="text-foreground">{maxRuntime > 0 ? formatDuration(maxRuntime) : "-"}</p>
        </div>
      </div>
      {usage && (
        <div className="mt-3 grid grid-cols-3 gap-2 border-t border-border pt-3 font-mono text-[11px] text-muted-fg">
          <div>
            <p className="text-[9px] uppercase tracking-widest">Tokens</p>
            <p className="text-foreground">{formatTokens(usage.total_tokens)}</p>
          </div>
          <div>
            <p className="text-[9px] uppercase tracking-widest">Cost</p>
            <p className="text-foreground">{formatCost(usage.total_cost_usd)}</p>
          </div>
          <div>
            <p className="text-[9px] uppercase tracking-widest">Cache hit</p>
            <p className="text-foreground">{formatPercent(usage.cache_hit_rate)}</p>
          </div>
        </div>
      )}
    </div>
  );
}

function EvalQueue({ jobs, evalState }: { jobs: EvalJob[]; evalState: EvalJobsResponse | null }) {
  const resourceSummary = formatEvalResourcePool(evalState);
  if (jobs.length === 0) {
    return (
      <div>
        <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-3">
          Evaluator
        </p>
        <div className="p-3 border border-border rounded-lg">
          <p className="font-mono text-[11px] text-muted-fg">No queued evals</p>
          {resourceSummary && (
            <p className="mt-1 font-mono text-[10px] text-muted-fg">{resourceSummary}</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div>
      <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-3">
        Evaluator ({jobs.length})
      </p>
      {resourceSummary && (
        <p className="mb-3 font-mono text-[10px] text-muted-fg">{resourceSummary}</p>
      )}
      <div className="space-y-2">
        {jobs.map((job) => {
          const percent = Math.max(0, Math.min(1, job.progress?.percent ?? 0));
          const current = job.progress?.current;
          const total = job.progress?.total;
          const progressLabel =
            current != null && total != null && total > 0
              ? `${current}/${total}`
              : job.queue_status === "evaluating"
                ? "running"
                : "queued";
          return (
            <div key={job.commit_hash} className="p-3 border border-border rounded-lg bg-muted/30">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-mono text-[11px] font-medium truncate">
                    {job.agent_id}
                  </p>
                  <p className="font-body text-[11px] text-muted-fg truncate">
                    {job.title}
                  </p>
                </div>
                <span
                  className={`shrink-0 rounded-md px-2 py-1 font-mono text-[9px] uppercase tracking-widest ${
                    job.queue_status === "evaluating"
                      ? "bg-accent text-white"
                      : "border border-border text-muted-fg"
                  }`}
                >
                  {job.queue_status === "evaluating" ? "Evaluating" : "Waiting"}
                </span>
              </div>
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-border">
                <div
                  className="h-full bg-accent transition-[width] duration-300"
                  style={{ width: `${job.queue_status === "evaluating" ? percent * 100 : 0}%` }}
                />
              </div>
              <div className="mt-2 flex items-center gap-3 font-mono text-[10px] text-muted-fg">
                <span>{progressLabel}</span>
                {job.eval_profile && <span>{job.eval_profile}</span>}
                {job.eval_version && <span>{job.eval_version}</span>}
                <span className="ml-auto">{job.commit_hash.slice(0, 8)}</span>
              </div>
              {job.progress?.message && (
                <p className="mt-1 truncate font-body text-[11px] text-muted-fg">
                  {job.progress.message}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
