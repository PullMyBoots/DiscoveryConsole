import { useEffect, useState } from "react";
import {
  api,
  type AgentStatus,
  type Attempt,
  type EvalJob,
  type EvalJobsResponse,
  type RunStatus,
  type TaskConfig,
} from "../lib/api";
import { useSSE } from "../hooks/useSSE";
import ScoreChart from "../components/ScoreChart";
import ChartModal from "../components/ChartModal";
import StatusBadge from "../components/StatusBadge";
import { scoreComponents, scoreLabel, scoreMetricNames, scoreValue } from "../lib/scores";
import { EmptyState, PageTitle, Panel, inputClass } from "../components/Ui";

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

function formatScore(value?: number | null): string {
  if (value == null) return "---";
  if (Math.abs(value) >= 1000 || Math.abs(value) < 0.0001) {
    return value.toPrecision(4);
  }
  return String(Number(value.toFixed(5)));
}

function loopState(agent: AgentStatus, evalJob?: EvalJob): string {
  const runtime = String(agent.runtime_state ?? "").toLowerCase();
  if (evalJob || agent.status === "evaluating" || agent.status === "waiting" || runtime.includes("eval")) {
    return "等待eval";
  }
  if (agent.status === "reflect_loop" || runtime.includes("reflect")) return "reflect_loop";
  if (agent.status === "stopped" || agent.status === "paused" || runtime === "stopped") return "stopped";
  return "work_loop";
}

export default function Overview() {
  const [config, setConfig] = useState<TaskConfig | null>(null);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [evalState, setEvalState] = useState<EvalJobsResponse | null>(null);
  const [scoreMetric, setScoreMetric] = useState("score");
  const [selectedAttempt, setSelectedAttempt] = useState<Attempt | null>(null);
  const [chartExpanded, setChartExpanded] = useState(false);

  const refresh = () => {
    api.config().then(setConfig).catch(() => {});
    api.attempts().then(setAttempts).catch(() => {});
    api.status().then(setStatus).catch(() => {});
    api.evals().then(setEvalState).catch(() => {});
  };

  useEffect(refresh, []);
  useSSE({
    "attempt:new": refresh,
    "attempt:update": refresh,
    "eval:update": refresh,
    "eval:progress": () => api.evals().then(setEvalState).catch(() => {}),
    "run:update": () => api.status().then(setStatus).catch(() => {}),
  });

  const direction = config?.grader?.direction === "minimize" ? "minimize" : "maximize";
  const metricOptions = ["score", ...scoreMetricNames(attempts)];
  const selectedMetric = metricOptions.includes(scoreMetric) ? scoreMetric : "score";
  const scored = attempts.filter((attempt) => scoreValue(attempt, selectedMetric) !== null);
  const agents = status ? [...status.agents].sort((a, b) => a.agent_id.localeCompare(b.agent_id)) : [];
  const evalJobByAgent = new Map<string, EvalJob>();
  for (const job of evalState?.jobs ?? []) {
    const current = evalJobByAgent.get(job.agent_id);
    if (!current || current.queue_status === "waiting") {
      evalJobByAgent.set(job.agent_id, job);
    }
  }

  return (
    <>
      <div className="control-scroll h-full min-h-0 overflow-y-scroll p-4 sm:p-5">
        <div className="mx-auto grid max-w-[1480px] gap-5">
          <PageTitle
            icon="chart"
            title="Overview"
            subtitle={`${scored.length} scored attempts · ${agents.length} agents`}
          />

          <Panel
            title="Score trajectory"
            icon="chart"
            action={
              <div className="flex items-center gap-2">
                <select
                  value={selectedMetric}
                  onChange={(event) => {
                    setScoreMetric(event.target.value);
                    setSelectedAttempt(null);
                  }}
                  className={`${inputClass} w-[210px] py-1.5 font-mono text-[11px]`}
                >
                  {metricOptions.map((metric) => (
                    <option key={metric} value={metric}>
                      {scoreLabel(metric)}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => setChartExpanded(true)}
                  className="rounded-md border border-border bg-surface/70 px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-fg transition-colors hover:text-foreground"
                >
                  Expand
                </button>
              </div>
            }
          >
            <ScoreChart
              attempts={attempts}
              height={320}
              direction={direction}
              metric={selectedMetric}
              onSelectAttempt={setSelectedAttempt}
            />
            {selectedAttempt && <SelectedAttemptPanel attempt={selectedAttempt} metric={selectedMetric} />}
          </Panel>

          <Panel title="Agent states" icon="users">
            {agents.length === 0 ? (
              <EmptyState icon="users" title="No agents running" body="Agent state appears here after the run starts." />
            ) : (
              <div className="grid gap-3 xl:grid-cols-2">
                {agents.map((agent) => {
                  const activeEvalJob = evalJobByAgent.get(agent.agent_id);
                  return (
                    <div key={agent.agent_id} className="rounded-lg border border-border bg-surface/85 p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate font-mono text-[13px] font-semibold">{agent.agent_id}</p>
                          <p className="mt-1 font-mono text-[11px] text-muted-fg">
                            {loopState(agent, activeEvalJob)} · {formatDuration(agent.status_duration_seconds)}
                          </p>
                        </div>
                        <StatusBadge status={agent.status} />
                      </div>

                      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                        <AgentMetric label="Attempts" value={String(agent.attempts)} />
                        <AgentMetric label="Best" value={formatScore(agent.best_score)} />
                        <AgentMetric label="Active" value={formatDuration(agent.active_seconds)} />
                        <AgentMetric label="Idle" value={formatDuration(agent.last_activity_age_seconds)} />
                      </div>

                      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-muted-fg">
                        <span>{formatTokens(agent.usage?.total_tokens)} tok</span>
                        <span>{formatCost(agent.usage?.total_cost_usd)}</span>
                        <span>cache {formatPercent(agent.usage?.cache_hit_rate)}</span>
                      </div>

                      {activeEvalJob && (
                        <div className="mt-3 rounded-md border border-border bg-muted/25 px-3 py-2">
                          <p className="font-mono text-[10px] uppercase tracking-wider text-muted-fg">
                            {activeEvalJob.queue_status}
                          </p>
                          <p className="mt-1 truncate text-[12px] text-foreground">{activeEvalJob.title}</p>
                          {activeEvalJob.progress?.message && (
                            <p className="mt-1 truncate font-mono text-[10px] text-muted-fg">
                              {activeEvalJob.progress.message}
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </Panel>
        </div>
      </div>

      {chartExpanded && (
        <ChartModal
          attempts={attempts}
          direction={direction}
          metric={selectedMetric}
          onSelectAttempt={setSelectedAttempt}
          onClose={() => setChartExpanded(false)}
        />
      )}
    </>
  );
}

function AgentMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-border/70 bg-muted/20 px-3 py-2">
      <p className="font-mono text-[9px] uppercase tracking-widest text-muted-fg">{label}</p>
      <p className="mt-1 truncate font-mono text-[13px] text-foreground">{value}</p>
    </div>
  );
}

function SelectedAttemptPanel({ attempt, metric }: { attempt: Attempt; metric: string }) {
  const components = Object.entries(scoreComponents(attempt));
  const currentValue = scoreValue(attempt, metric);
  return (
    <div className="mt-4 rounded-xl border border-border bg-muted/30 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-display text-[13px] font-semibold">
            {attempt.title || attempt.commit_hash.slice(0, 8)}
          </p>
          <p className="font-mono text-[10px] text-muted-fg">
            {attempt.agent_id} · {attempt.commit_hash.slice(0, 8)}
          </p>
        </div>
        <div className="text-right">
          <p className="font-mono text-[10px] uppercase tracking-widest text-muted-fg">{scoreLabel(metric)}</p>
          <p className="font-mono text-[14px] font-medium">{currentValue == null ? "---" : String(currentValue)}</p>
        </div>
      </div>
      {components.length > 0 && (
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {components.map(([name, component]) => (
            <div key={name} className="rounded-lg border border-border bg-background px-3 py-2">
              <p className="font-mono text-[11px] font-medium">{name}</p>
              <p className="mt-1 font-mono text-[10px] text-muted-fg">
                {typeof component === "number" ? formatScore(component) : JSON.stringify(component)}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
