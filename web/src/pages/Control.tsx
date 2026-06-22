import {
  useEffect,
  useState,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import {
  api,
  type AgentAssignmentConfig,
  type ControlConfigResponse,
  type ControlPlanResponse,
  type ControlReadinessResponse,
  type RunStatus,
  type TaskConfig,
} from "../lib/api";
import { useSSE } from "../hooks/useSSE";

type BusyAction = "save" | "run" | "stop" | null;
type HeartbeatPreset = "quiet" | "standard" | "active";
type ReasoningEffort = "minimal" | "low" | "medium" | "high" | "xhigh" | "max";
type ReasoningOption = { value: ReasoningEffort; label: string };
type IconName =
  | "alert"
  | "brain"
  | "check"
  | "chevron"
  | "cpu"
  | "file"
  | "globe"
  | "gauge"
  | "map"
  | "message"
  | "pause"
  | "play"
  | "save"
  | "share"
  | "sliders"
  | "spark"
  | "timer"
  | "users";

const inputClass =
  "w-full rounded-md border border-border bg-surface/90 px-3 py-2 text-[13px] outline-none transition-colors placeholder:text-muted-fg/60 hover:border-border-strong focus:border-accent disabled:cursor-not-allowed disabled:bg-muted/40 disabled:text-muted-fg";
const labelClass = "mb-1.5 block font-mono text-[10px] uppercase tracking-wider text-muted-fg";
const panelClass = "rounded-xl border border-border bg-surface/85 shadow-[0_1px_0_rgba(23,32,29,0.04),0_14px_34px_rgba(23,32,29,0.045)]";
const HEARTBEAT_PRESETS: Record<HeartbeatPreset, Record<string, number>> = {
  quiet: { reflect: 3, consolidate: 20, pivot: 8, lint_wiki: 20 },
  standard: { reflect: 1, consolidate: 10, pivot: 5, lint_wiki: 10 },
  active: { reflect: 1, consolidate: 5, pivot: 3, lint_wiki: 5 },
};
const DEFAULT_HEARTBEAT_ACTIONS: Array<Record<string, unknown>> = [
  { name: "reflect", every: 1, trigger: "interval", prompt: "", options: {}, global: false },
  { name: "consolidate", every: 10, trigger: "interval", prompt: "", options: {}, global: true },
  { name: "pivot", every: 5, trigger: "plateau", prompt: "", options: {}, global: false },
  { name: "lint_wiki", every: 10, trigger: "interval", prompt: "", options: {}, global: true },
];
const REASONING_OPTIONS_BY_RUNTIME: Record<string, ReasoningOption[]> = {
  codex: [
    { value: "low", label: "Low" },
    { value: "medium", label: "Med" },
    { value: "high", label: "High" },
    { value: "xhigh", label: "XHigh" },
  ],
  claude_code: [
    { value: "low", label: "Low" },
    { value: "medium", label: "Med" },
    { value: "high", label: "High" },
    { value: "xhigh", label: "XHigh" },
    { value: "max", label: "Max" },
  ],
  opencode: [
    { value: "minimal", label: "Min" },
    { value: "low", label: "Low" },
    { value: "medium", label: "Med" },
    { value: "high", label: "High" },
    { value: "max", label: "Max" },
  ],
};

function cloneConfig(config: TaskConfig): TaskConfig {
  return JSON.parse(JSON.stringify(config)) as TaskConfig;
}

function parseNumber(value: string, fallback = 0): number {
  if (value.trim() === "") return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseGpuIds(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function messageFromError(error: unknown): string {
  return error instanceof Error ? error.message : "request failed";
}

function runtimeMinutes(config: TaskConfig): number {
  return Math.round((config.run?.max_runtime_seconds ?? 0) / 60);
}

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

function isPlanLocked(status: RunStatus | null): boolean {
  if (!status) return true;
  return Boolean(
    status.manager_alive ||
      status.total_attempts > 0 ||
      status.eval_count > 0 ||
      status.agents.length > 0 ||
      status.run_state?.started_at,
  );
}

function displayRunState(status: RunStatus | null): string {
  if (status?.manager_alive) return "running";
  if (status?.run_state?.stopped_reason === "manual") return "paused";
  return "stopped";
}

function explorationModeLabel(islandCount: number): string {
  if (islandCount > 1) return `${islandCount} search groups`;
  return "Single search group";
}

function missingReadinessLabels(readiness: ControlReadinessResponse | null): string[] {
  if (!readiness || readiness.status !== "missing") return [];
  return readiness.checks
    .filter((check) => check.status === "missing")
    .map((check) => check.label || check.id);
}

function heartbeatPreset(config: TaskConfig): HeartbeatPreset | "custom" {
  const actions = config.agents.heartbeat ?? [];
  const everyByName = new Map<string, number>();
  for (const action of actions) {
    const name = typeof action.name === "string" ? action.name : "";
    const every = typeof action.every === "number" ? action.every : undefined;
    if (name && every !== undefined) everyByName.set(name, every);
  }
  for (const [preset, values] of Object.entries(HEARTBEAT_PRESETS) as Array<
    [HeartbeatPreset, Record<string, number>]
  >) {
    const matches = Object.entries(values).every(([name, every]) => everyByName.get(name) === every);
    if (matches) return preset;
  }
  return "custom";
}

function applyHeartbeatPreset(config: TaskConfig, preset: HeartbeatPreset) {
  const values = HEARTBEAT_PRESETS[preset];
  const actions = [...(config.agents.heartbeat ?? [])];
  for (const defaultAction of DEFAULT_HEARTBEAT_ACTIONS) {
    const name = defaultAction.name as string;
    const index = actions.findIndex((action) => action.name === name);
    const next = { ...(index >= 0 ? actions[index] : defaultAction), every: values[name] };
    if (index >= 0) actions[index] = next;
    else actions.push(next);
  }
  config.agents.heartbeat = actions;
}

function tuneEvaluatorConcurrency(config: TaskConfig) {
  const pool = config.grader.parallel?.resources;
  if (!pool) return;
  const gpuCount = Math.max(pool.gpu_count ?? 0, pool.gpu_ids?.length ?? 0);
  const cpuCores = pool.cpu_cores ?? 0;
  const derivedWorkers = gpuCount > 0 ? gpuCount : cpuCores > 0 ? cpuCores : undefined;
  if (derivedWorkers !== undefined) {
    config.grader.parallel ??= {};
    config.grader.parallel.max_workers = Math.max(1, Math.round(derivedWorkers));
  }
}

function updateEvaluatorResources(config: TaskConfig, updater: (resources: Record<string, unknown>) => void) {
  config.grader.parallel ??= {};
  config.grader.parallel.resources ??= {};
  updater(config.grader.parallel.resources as Record<string, unknown>);
  tuneEvaluatorConcurrency(config);
}

function reasoningOptionsForRuntime(runtime?: string): ReasoningOption[] {
  return REASONING_OPTIONS_BY_RUNTIME[runtime || ""] ?? REASONING_OPTIONS_BY_RUNTIME.claude_code;
}

function assignmentReasoning(
  assignment: AgentAssignmentConfig,
  fallback: ReasoningEffort,
  runtime?: string,
): ReasoningEffort {
  const value = assignment.runtime_options?.model_reasoning_effort;
  const options = reasoningOptionsForRuntime(runtime);
  if (typeof value === "string" && options.some((option) => option.value === value)) {
    return value as ReasoningEffort;
  }
  if (options.some((option) => option.value === fallback)) return fallback;
  if (options.some((option) => option.value === "medium")) return "medium";
  return options[0]?.value ?? "medium";
}

function reasoningEffort(config: TaskConfig): ReasoningEffort {
  return assignmentReasoning({ runtime_options: config.agents.runtime_options }, "medium", config.agents.runtime);
}

function plannedAgentCount(config: TaskConfig): number {
  const assignments = config.agents.assignments;
  if (Array.isArray(assignments) && assignments.length > 0) {
    const total = assignments.reduce((sum, assignment) => {
      const count =
        typeof assignment.count === "number"
          ? assignment.count
          : Number.parseInt(String(assignment.count ?? 1), 10);
      return sum + Math.max(1, Number.isFinite(count) ? count : 1);
    }, 0);
    return Math.max(1, total);
  }
  return Math.max(1, config.agents.count ?? 1);
}

function materializeAgentAssignments(config: TaskConfig): AgentAssignmentConfig[] {
  const fallbackRuntime = config.agents.runtime ?? "claude_code";
  const fallbackModel = config.agents.model ?? "";
  const fallbackOptions = { ...(config.agents.runtime_options ?? {}) };
  const assignments = config.agents.assignments ?? [];
  const materialized: AgentAssignmentConfig[] = [];

  if (assignments.length > 0) {
    for (const assignment of assignments) {
      const count =
        typeof assignment.count === "number"
          ? assignment.count
          : Number.parseInt(String(assignment.count ?? 1), 10);
      const safeCount = Math.max(1, Number.isFinite(count) ? count : 1);
      for (let i = 0; i < safeCount; i += 1) {
        materialized.push({
          runtime: assignment.runtime || fallbackRuntime,
          model: assignment.model || fallbackModel,
          count: 1,
          runtime_options: {
            ...fallbackOptions,
            ...(assignment.runtime_options ?? {}),
          },
        });
      }
    }
  } else {
    const count = Math.max(1, config.agents.count ?? 1);
    for (let i = 0; i < count; i += 1) {
      materialized.push({
        runtime: fallbackRuntime,
        model: fallbackModel,
        count: 1,
        runtime_options: { ...fallbackOptions },
      });
    }
  }

  return materialized;
}

function agentDisplayId(index: number, islandCount: number): { agentId: string; islandId: string | null } {
  if (islandCount <= 1) return { agentId: `agent-${index + 1}`, islandId: null };
  const islandId = String(index % islandCount);
  const islandSeq = Math.floor(index / islandCount) + 1;
  return { agentId: `${islandId}-agent-${islandSeq}`, islandId };
}

function agentsPerIslandLabel(agentCount: number, islandCount: number): string {
  if (islandCount <= 1) return `${agentCount} in one group`;
  const counts = Array.from({ length: islandCount }, () => 0);
  for (let i = 0; i < agentCount; i += 1) {
    counts[i % islandCount] += 1;
  }
  if (counts.every((count) => count === counts[0])) return `${counts[0]} per group`;
  return counts.map((count, index) => `group ${index}: ${count}`).join(" / ");
}

function runLimitLabel(minutes: number): string {
  return minutes > 0 ? `${minutes} min` : "No time limit";
}

function agentBackendSummary(assignments: AgentAssignmentConfig[], fallbackRuntime?: string): string {
  const runtimes = new Set(assignments.map((item) => item.runtime || fallbackRuntime || "claude_code"));
  if (runtimes.size === 1) return [...runtimes][0] ?? "claude_code";
  return "Mixed backends";
}

function readinessSummary(readiness: ControlReadinessResponse | null): string {
  if (!readiness) return "Checking workspace";
  if (readiness.status === "ready") return "Workspace ready";
  if (readiness.status === "warning") return "Ready with warnings";
  const missing = missingReadinessLabels(readiness);
  return missing.length > 0 ? `Needs Codex: ${missing.join(", ")}` : "Needs Codex preparation";
}

function updateAgentAssignment(
  config: TaskConfig,
  index: number,
  updater: (assignment: AgentAssignmentConfig) => void,
) {
  const assignments = materializeAgentAssignments(config);
  const assignment = assignments[index];
  if (!assignment) return;
  updater(assignment);
  config.agents.assignments = assignments;
  config.agents.count = assignments.length;
}

function Icon({ name, className = "h-4 w-4" }: { name: IconName; className?: string }) {
  const paths: Record<IconName, ReactNode> = {
    alert: (
      <>
        <path d="M12 9v4" />
        <path d="M12 17h.01" />
        <path d="M10.3 4.4 2.5 18a2 2 0 0 0 1.7 3h15.6a2 2 0 0 0 1.7-3L13.7 4.4a2 2 0 0 0-3.4 0Z" />
      </>
    ),
    brain: (
      <>
        <path d="M8 6.5a3 3 0 0 0-3 3v5A3.5 3.5 0 0 0 8.5 18H10V6.5Z" />
        <path d="M16 6.5a3 3 0 0 1 3 3v5a3.5 3.5 0 0 1-3.5 3.5H14V6.5Z" />
        <path d="M10 9H7" />
        <path d="M14 9h3" />
        <path d="M10 14H7" />
        <path d="M14 14h3" />
      </>
    ),
    check: (
      <>
        <path d="M20 6 9 17l-5-5" />
      </>
    ),
    chevron: <path d="m9 18 6-6-6-6" />,
    cpu: (
      <>
        <rect x="7" y="7" width="10" height="10" rx="2" />
        <path d="M9 1v3" />
        <path d="M15 1v3" />
        <path d="M9 20v3" />
        <path d="M15 20v3" />
        <path d="M20 9h3" />
        <path d="M20 15h3" />
        <path d="M1 9h3" />
        <path d="M1 15h3" />
      </>
    ),
    file: (
      <>
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />
        <path d="M14 2v6h6" />
        <path d="M8 13h8" />
        <path d="M8 17h5" />
      </>
    ),
    globe: (
      <>
        <circle cx="12" cy="12" r="10" />
        <path d="M2 12h20" />
        <path d="M12 2a15 15 0 0 1 0 20" />
        <path d="M12 2a15 15 0 0 0 0 20" />
      </>
    ),
    gauge: (
      <>
        <path d="M4 14a8 8 0 1 1 16 0" />
        <path d="M12 14 16 9" />
        <path d="M4 18h16" />
      </>
    ),
    map: (
      <>
        <path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3Z" />
        <path d="M9 3v15" />
        <path d="M15 6v15" />
      </>
    ),
    message: (
      <>
        <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z" />
        <path d="M8 9h8" />
        <path d="M8 13h5" />
      </>
    ),
    pause: (
      <>
        <path d="M8 5v14" />
        <path d="M16 5v14" />
      </>
    ),
    play: <path d="m7 4 14 8-14 8Z" />,
    save: (
      <>
        <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z" />
        <path d="M17 21v-8H7v8" />
        <path d="M7 3v5h8" />
      </>
    ),
    share: (
      <>
        <circle cx="18" cy="5" r="3" />
        <circle cx="6" cy="12" r="3" />
        <circle cx="18" cy="19" r="3" />
        <path d="m8.6 13.5 6.8 4" />
        <path d="m15.4 6.5-6.8 4" />
      </>
    ),
    sliders: (
      <>
        <path d="M4 21v-7" />
        <path d="M4 10V3" />
        <path d="M12 21v-9" />
        <path d="M12 8V3" />
        <path d="M20 21v-5" />
        <path d="M20 12V3" />
        <path d="M2 14h4" />
        <path d="M10 8h4" />
        <path d="M18 16h4" />
      </>
    ),
    spark: (
      <>
        <path d="M12 2v6" />
        <path d="M12 16v6" />
        <path d="M2 12h6" />
        <path d="M16 12h6" />
        <path d="m5 5 4 4" />
        <path d="m15 15 4 4" />
        <path d="m19 5-4 4" />
        <path d="m9 15-4 4" />
      </>
    ),
    timer: (
      <>
        <circle cx="12" cy="13" r="8" />
        <path d="M12 13 16 9" />
        <path d="M9 2h6" />
      </>
    ),
    users: (
      <>
        <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M22 21v-2a4 4 0 0 0-3-3.9" />
        <path d="M16 3.1a4 4 0 0 1 0 7.8" />
      </>
    ),
  };
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      {paths[name]}
    </svg>
  );
}

function IconBadge({ name, tone = "neutral" }: { name: IconName; tone?: "neutral" | "good" | "warn" | "danger" }) {
  const toneClass =
    tone === "good"
      ? "border-success/35 bg-success-soft text-success"
      : tone === "warn"
        ? "border-warning/35 bg-warning-soft text-warning"
        : tone === "danger"
          ? "border-danger/35 bg-danger-soft text-danger"
          : "border-accent/20 bg-accent-soft text-accent-fg";
  return (
    <span className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border ${toneClass}`}>
      <Icon name={name} />
    </span>
  );
}

function Section({ title, icon, children }: { title: string; icon?: IconName; children: ReactNode }) {
  return (
    <section className={`${panelClass} p-4`}>
      <div className="mb-4 flex items-center justify-between gap-3 border-b border-border/70 pb-3">
        <div className="flex items-center gap-2">
          {icon && <IconBadge name={icon} />}
          <h2 className="font-mono text-[10px] uppercase tracking-wider text-muted-fg">{title}</h2>
        </div>
      </div>
      <div className="grid gap-4">{children}</div>
    </section>
  );
}

function CollapsibleSection({
  title,
  summary,
  icon,
  children,
}: {
  title: string;
  summary?: ReactNode;
  icon?: IconName;
  children: ReactNode;
}) {
  return (
    <details className={`${panelClass} group overflow-hidden`}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 marker:hidden hover:bg-muted/25">
        <div className="flex min-w-0 items-center gap-3">
          {icon && <IconBadge name={icon} />}
          <div className="min-w-0">
            <h2 className="font-mono text-[10px] uppercase tracking-wider text-muted-fg">{title}</h2>
            {summary && <div className="mt-1 truncate font-mono text-[11px] text-muted-fg">{summary}</div>}
          </div>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border bg-surface/70 px-2 py-1 font-mono text-[10px] text-muted-fg group-open:hidden">
          <Icon name="chevron" className="h-3 w-3" />
          Open
        </span>
        <span className="hidden shrink-0 rounded-md border border-border bg-surface/70 px-2 py-1 font-mono text-[10px] text-muted-fg group-open:inline">
          Close
        </span>
      </summary>
      <div className="border-t border-border p-4">
        <div className="grid gap-4">{children}</div>
      </div>
    </details>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className={labelClass}>{label}</span>
      {children}
    </label>
  );
}

function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${inputClass} ${props.className ?? ""}`} />;
}

function SelectInput(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${inputClass} ${props.className ?? ""}`} />;
}

function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={`${inputClass} min-h-40 resize-y text-[13px] leading-5 ${props.className ?? ""}`}
    />
  );
}

function ReadOnlyTextArea({ value }: { value: string }) {
  return (
    <textarea
      readOnly
      value={value}
      className={`${inputClass} min-h-32 resize-y bg-muted/30 text-[13px] leading-5 text-muted-fg`}
    />
  );
}

function Toggle({
  label,
  checked,
  onChange,
  disabled = false,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`grid min-h-[44px] w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-4 rounded-lg border border-border px-3 py-2 text-left transition-colors hover:bg-surface/70 focus:border-accent disabled:cursor-not-allowed ${
        disabled ? "opacity-50" : ""
      }`}
    >
      <span className="min-w-0 truncate font-mono text-[11px] uppercase tracking-widest text-muted-fg">{label}</span>
      <span
        className={`relative h-5 w-9 shrink-0 rounded-full border transition-colors ${
          checked ? "border-accent bg-accent" : "border-border bg-muted"
        }`}
      >
        <span
          className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
            checked ? "translate-x-4" : ""
          }`}
        />
      </span>
    </button>
  );
}

function ReadOnlyValue({ value }: { value: string }) {
  return (
    <div className="flex min-h-[38px] w-full items-center rounded-md border border-border/70 bg-muted/25 px-3 py-2 font-mono text-[12px] text-foreground">
      {value}
    </div>
  );
}

function HelpText({ children }: { children: ReactNode }) {
  return <p className="font-body text-[12px] leading-relaxed text-muted-fg">{children}</p>;
}

function SummaryItem({ label, value, icon }: { label: string; value: string; icon?: IconName }) {
  return (
    <div className="flex min-w-0 items-start gap-3">
      {icon && <IconBadge name={icon} />}
      <div className="min-w-0">
        <p className="font-mono text-[10px] uppercase tracking-wider text-muted-fg">{label}</p>
        <p className="mt-1 truncate text-[13px] font-medium text-foreground" title={value}>
          {value}
        </p>
      </div>
    </div>
  );
}

function SettingLine({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-2 border-t border-border/60 py-3 first:border-t-0 md:grid-cols-[170px_1fr] md:items-center">
      <p className="font-mono text-[10px] uppercase tracking-wider text-muted-fg">{label}</p>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function planBadgeClass(status: string): string {
  if (status === "ready") return "border-success/40 bg-success-soft text-success";
  if (status === "partial") return "border-warning/40 bg-warning-soft text-warning";
  return "border-danger/40 bg-danger-soft text-danger";
}

function AgentPlanPanel({ plan }: { plan: ControlPlanResponse | null }) {
  return (
    <CollapsibleSection
      icon="map"
      title="Prepared agents"
      summary={
        plan
          ? `${plan.status} · ${plan.brief_count}/${plan.planned_agents} routes · ${plan.island_count} group(s)`
          : "loading"
      }
    >
      <div className="flex justify-end">
        <span
          className={`rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest ${planBadgeClass(plan?.status ?? "missing")}`}
        >
          {plan?.status ?? "loading"}
        </span>
      </div>

      {plan ? (
        <div className="grid gap-3">
          <div className="grid grid-cols-3 gap-2">
            <Metric label="Briefs" value={`${plan.brief_count}/${plan.planned_agents}`} />
            <Metric label="Islands" value={String(plan.island_count)} />
            <Metric label="Missing" value={String(plan.missing_briefs)} />
          </div>

          {plan.islands.length === 0 ? (
            <div className="rounded-lg border border-border bg-muted/20 px-3 py-2 font-mono text-[11px] text-muted-fg">
              No generated agent briefs found
            </div>
          ) : (
            <div className="grid gap-3">
              {plan.islands.map((island) => (
                <div
                  key={island.island_id ?? "single"}
                  className="rounded-lg border border-border bg-muted/20 p-3"
                >
                  <div className="mb-2 flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-mono text-[10px] uppercase tracking-widest text-muted-fg">
                        {island.island_id == null ? "Single Island" : `Island ${island.island_id}`}
                      </p>
                      {island.theme && (
                        <p className="mt-1 truncate text-[13px] font-medium">
                          {island.theme.title}
                        </p>
                      )}
                    </div>
                    <span className="rounded-md border border-border px-2 py-1 font-mono text-[9px] uppercase tracking-widest text-muted-fg">
                      {island.agents.length} agents
                    </span>
                  </div>
                  {island.theme?.summary && (
                    <p className="mb-3 line-clamp-2 font-body text-[12px] leading-relaxed text-muted-fg">
                      {island.theme.summary}
                    </p>
                  )}
                  <div className="grid gap-2">
                    {island.agents.map((agent) => (
                      <div key={agent.path} className="rounded-md border border-border bg-background px-3 py-2">
                        <div className="flex items-center justify-between gap-3">
                          <p className="truncate text-[13px] font-medium">{agent.title}</p>
                          <span className="shrink-0 font-mono text-[10px] text-muted-fg">
                            {agent.agent_id}
                          </span>
                        </div>
                        {agent.summary && (
                          <p className="mt-1 line-clamp-2 font-body text-[12px] leading-relaxed text-muted-fg">
                            {agent.summary}
                          </p>
                        )}
                        <p className="mt-1 truncate font-mono text-[10px] text-muted-fg">
                          {agent.relative_path}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="rounded-lg border border-border bg-muted/20 px-3 py-2 font-mono text-[11px] text-muted-fg">
          Loading generated plan
        </div>
      )}
    </CollapsibleSection>
  );
}

function ReasoningSlider<T extends string>({
  value,
  options,
  onChange,
  disabled = false,
}: {
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  const activeIndex = Math.max(
    0,
    options.findIndex((option) => option.value === value),
  );
  return (
    <div className="rounded-lg border border-border bg-muted/25 px-3 py-2">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="truncate text-[13px] font-medium text-foreground">
          {options[activeIndex]?.label ?? value}
        </span>
        <span className="shrink-0 font-mono text-[10px] uppercase tracking-wider text-muted-fg">
          {activeIndex + 1}/{options.length}
        </span>
      </div>
      <input
        type="range"
        min={0}
        max={Math.max(0, options.length - 1)}
        step={1}
        value={activeIndex}
        disabled={disabled}
        aria-label="Thinking depth"
        onChange={(event) => {
          const next = options[Number(event.target.value)];
          if (next) onChange(next.value);
        }}
        className="h-2 w-full cursor-pointer accent-accent disabled:cursor-not-allowed disabled:opacity-50"
      />
      <div
        className="mt-1 grid gap-1 font-mono text-[9px] uppercase tracking-wider text-muted-fg"
        style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}
      >
        {options.map((option, index) => (
          <button
            key={option.value}
            type="button"
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={`truncate text-center disabled:cursor-not-allowed disabled:opacity-50 ${
              index === activeIndex ? "text-accent-fg" : "hover:text-foreground"
            }`}
            title={option.label}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function Control() {
  const [meta, setMeta] = useState<ControlConfigResponse | null>(null);
  const [config, setConfig] = useState<TaskConfig | null>(null);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [readiness, setReadiness] = useState<ControlReadinessResponse | null>(null);
  const [plan, setPlan] = useState<ControlPlanResponse | null>(null);
  const [instruction, setInstruction] = useState<string>("");
  const [busy, setBusy] = useState<BusyAction>(null);
  const [notice, setNotice] = useState<string>("");

  const refresh = () => {
    api.controlConfig()
      .then((data) => {
        setMeta(data);
        setConfig(data.config);
      })
      .catch((error) => setNotice(messageFromError(error)));
    api.status().then(setStatus).catch(() => {});
    api.controlReadiness().then(setReadiness).catch(() => {});
    api.controlPlan().then(setPlan).catch(() => {});
    api.controlInstruction()
      .then((data) => setInstruction(data.instruction))
      .catch(() => {});
  };

  useEffect(refresh, []);
  useEffect(() => {
    const id = window.setInterval(() => {
      api.status().then(setStatus).catch(() => {});
    }, 5000);
    return () => window.clearInterval(id);
  }, []);
  useSSE({
    "run:switched": refresh,
    "run:update": () => api.status().then(setStatus).catch(() => {}),
    "attempt:new": () => {
      api.status().then(setStatus).catch(() => {});
      api.controlReadiness().then(setReadiness).catch(() => {});
      api.controlPlan().then(setPlan).catch(() => {});
    },
    "attempt:update": () => {
      api.status().then(setStatus).catch(() => {});
      api.controlReadiness().then(setReadiness).catch(() => {});
      api.controlPlan().then(setPlan).catch(() => {});
    },
    "eval:update": () => api.status().then(setStatus).catch(() => {}),
  });

  const updateConfig = (updater: (draft: TaskConfig) => void) => {
    setConfig((current) => {
      if (!current) return current;
      const draft = cloneConfig(current);
      updater(draft);
      return draft;
    });
  };

  const saveConfig = async (): Promise<TaskConfig | null> => {
    if (!config) return null;
    const response = await api.saveControlConfig(config);
    if (response.config) {
      setConfig(response.config);
      api.controlReadiness().then(setReadiness).catch(() => {});
      return response.config;
    }
    api.controlReadiness().then(setReadiness).catch(() => {});
    return config;
  };

  const saveInstruction = async () => {
    await api.saveControlInstruction(instruction);
  };

  const onSave = async () => {
    setBusy("save");
    setNotice("");
    try {
      await saveConfig();
      await saveInstruction();
      setNotice("Configuration saved");
    } catch (error) {
      setNotice(messageFromError(error));
    } finally {
      setBusy(null);
    }
  };

  const onRun = async () => {
    const missing = missingReadinessLabels(readiness);
    if (missing.length > 0) {
      setNotice(`Run blocked until Codex prepares: ${missing.join(", ")}`);
      return;
    }
    setBusy("run");
    setNotice("");
    try {
      await saveConfig();
      await saveInstruction();
      const response = await api.controlResume();
      setNotice(response.message ?? (response.ok ? "Run requested" : "Run request failed"));
      setTimeout(refresh, 700);
    } catch (error) {
      setNotice(messageFromError(error));
    } finally {
      setBusy(null);
    }
  };

  const onStop = async () => {
    setBusy("stop");
    setNotice("");
    try {
      const response = await api.controlStop();
      setNotice(response.message ?? "Pause requested");
      setTimeout(refresh, 700);
    } catch (error) {
      setNotice(messageFromError(error));
    } finally {
      setBusy(null);
    }
  };

  if (!config) {
    return (
      <div className="overflow-y-auto p-6">
        <div className="border border-border rounded-xl p-5 font-mono text-[12px] text-muted-fg">
          Loading control state
        </div>
      </div>
    );
  }

  const activeAgents = status?.agents.filter((agent) => agent.status === "active").length ?? 0;
  const runLabel = meta ? `${meta.task} / ${meta.run}` : "loading";
  const missingReadiness = missingReadinessLabels(readiness);
  const runBlocked = missingReadiness.length > 0;
  const runTitle = runBlocked
    ? `Run blocked until Codex prepares: ${missingReadiness.join(", ")}`
    : "Start or resume this run";
  const configuredRuntime = runtimeMinutes(config);
  const remainingLabel = formatDuration(status?.run_state?.remaining_seconds);
  const planLocked = isPlanLocked(status);
  const islandCount = config.islands?.count ?? 1;
  const plannedAgents = plannedAgentCount(config);
  const currentHeartbeatPreset = heartbeatPreset(config);
  const currentReasoning = reasoningEffort(config);
  const agentAssignments = materializeAgentAssignments(config);
  const explorationMode = islandCount > 1 ? "multi" : "single";
  const displayState = displayRunState(status);
  const isFreshRun =
    !status?.manager_alive &&
    (status?.total_attempts ?? 0) === 0 &&
    (status?.eval_count ?? 0) === 0;
  const profileNames = Object.keys(config.grader.profiles ?? {});
  const profileOptions =
    profileNames.length > 0
      ? profileNames
      : [config.grader.profile ?? "default"].filter((name, index, arr) => name && arr.indexOf(name) === index);
  const selectedProfileName = config.grader.profile ?? profileOptions[0] ?? "default";
  const selectedProfileLabel = config.grader.profiles?.[selectedProfileName]?.label || selectedProfileName;
  const backendSummary = agentBackendSummary(agentAssignments, config.agents.runtime);
  const runSummaryItems: Array<{ icon?: IconName; label: string; value: string }> = isFreshRun
    ? [
        { icon: "users" as IconName, label: "Prepared agents", value: `${plannedAgents} agents · ${explorationModeLabel(islandCount)}` },
        { icon: "timer" as IconName, label: "Run limit", value: runLimitLabel(configuredRuntime) },
        { icon: "gauge" as IconName, label: "Scoring method", value: selectedProfileLabel },
        { icon: "globe" as IconName, label: "Internet", value: config.agents.research ? "Allowed" : "Off" },
      ]
    : [
        { label: "Agents", value: `${activeAgents}/${status?.agents.length ?? config.agents.count}` },
        { label: "Attempts", value: String(status?.total_attempts ?? 0) },
        { label: "Evaluations", value: String(status?.eval_count ?? 0) },
        { label: "Remaining", value: remainingLabel },
        { label: "Best", value: status?.best_score == null ? "-" : status.best_score.toFixed(4) },
      ];

  return (
    <div className="control-scroll h-full min-h-0 overflow-y-scroll p-4 sm:p-5">
      <div className="mx-auto grid max-w-[1480px] gap-5">
        <section className={`${panelClass} overflow-hidden`}>
          <div className="grid gap-4 p-4 min-[980px]:grid-cols-[minmax(220px,0.9fr)_minmax(260px,1.1fr)_minmax(300px,auto)] min-[980px]:items-center">
            <div className="flex min-w-0 items-start gap-3">
              <IconBadge name="gauge" tone={status?.manager_alive ? "good" : displayState === "paused" ? "warn" : "neutral"} />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="font-display text-2xl">Run Control</h1>
                  <span
                    className={`rounded-full border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider ${
                      status?.manager_alive
                        ? "border-success/40 bg-success-soft text-success"
                        : displayState === "paused"
                          ? "border-warning/40 bg-warning-soft text-warning"
                          : "border-border bg-muted/40 text-muted-fg"
                    }`}
                  >
                    {displayState}
                  </span>
                </div>
                <p className="mt-1 max-w-[360px] truncate font-mono text-[11px] text-muted-fg" title={runLabel}>
                  {runLabel}
                </p>
              </div>
            </div>

            <div
              className={`flex min-w-0 gap-3 rounded-lg border px-3 py-3 ${
                runBlocked
                  ? "border-danger/35 bg-danger-soft text-danger"
                  : "border-success/35 bg-success-soft text-success"
              }`}
            >
              <IconBadge name={runBlocked ? "alert" : "check"} tone={runBlocked ? "danger" : "good"} />
              <div className="min-w-0">
                <p className="text-[13px] font-semibold">
                  {runBlocked ? "Cannot run yet" : "Ready to run"}
                </p>
                <p className="mt-1 truncate font-mono text-[11px]">{readinessSummary(readiness)}</p>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3 min-[980px]:w-[360px]">
              <button
                onClick={onRun}
                disabled={busy !== null || status?.manager_alive || runBlocked}
                title={runTitle}
                className="inline-flex min-h-[42px] items-center justify-center gap-2 rounded-md bg-accent px-3 py-2 text-[13px] font-semibold text-white shadow-sm transition-colors hover:bg-accent-fg disabled:cursor-not-allowed disabled:opacity-35"
              >
                <Icon name="play" className="h-3.5 w-3.5" />
                Save and run
              </button>
              <button
                onClick={onStop}
                disabled={busy !== null || !status?.manager_alive}
                title="Request a pause and preserve the timestamp for resume"
                className="inline-flex min-h-[42px] items-center justify-center gap-2 rounded-md border border-border bg-surface/70 px-3 py-2 text-[13px] font-medium transition-colors hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-35"
              >
                <Icon name="pause" className="h-3.5 w-3.5" />
                Pause
              </button>
              <button
                onClick={onSave}
                disabled={busy !== null}
                className="inline-flex min-h-[42px] items-center justify-center gap-2 rounded-md border border-border bg-surface/70 px-3 py-2 text-[13px] font-medium transition-colors hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-35"
              >
                <Icon name="save" className="h-3.5 w-3.5" />
                Save
              </button>
            </div>
          </div>

          <div className="grid gap-2 border-t border-border/70 bg-surface/45 px-4 py-3 [grid-template-columns:repeat(auto-fit,minmax(min(170px,100%),1fr))]">
            {runSummaryItems.map((item) => (
              <SummaryItem key={`${item.label}:${item.value}`} icon={item.icon} label={item.label} value={item.value} />
            ))}
          </div>

          <div className="border-t border-border/60 px-4 py-2">
            <p className="font-mono text-[10px] leading-4 text-muted-fg">
              {notice || "Run saves current settings before launch."}
            </p>
          </div>
        </section>

        <div className="grid min-w-0 content-start gap-5">
          <div className="grid gap-5 [grid-template-columns:repeat(auto-fit,minmax(min(430px,100%),1fr))]">
            <Section title="Run Setup" icon="users">
              <div className="grid gap-3 rounded-lg border border-border/70 bg-muted/20 p-3">
                <SummaryItem
                  icon="map"
                  label="Prepared team"
                  value={`${plannedAgents} agents · ${explorationModeLabel(islandCount)} · groups ${agentsPerIslandLabel(plannedAgents, islandCount)}`}
                />
                <SummaryItem icon="sliders" label="Agent backend" value={backendSummary} />
              </div>

              {explorationMode === "multi" && (
                <SettingLine label="Share findings">
                  <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_140px]">
                    <Toggle
                      label="Between groups"
                      checked={config.islands?.migration?.enabled ?? false}
                      onChange={(checked) =>
                        updateConfig((draft) => {
                          draft.islands ??= {};
                          draft.islands.migration ??= {};
                          draft.islands.migration.enabled = checked;
                        })
                      }
                    />
                    <TextInput
                      type="number"
                      min={1}
                      disabled={!(config.islands?.migration?.enabled ?? false)}
                      value={config.islands?.migration?.every ?? 50}
                      aria-label="Share findings every evals"
                      title="How many global evaluations between sharing useful results across groups"
                      onChange={(event) =>
                        updateConfig((draft) => {
                          draft.islands ??= {};
                          draft.islands.migration ??= {};
                          draft.islands.migration.every = Math.max(
                            1,
                            Math.round(parseNumber(event.target.value, 50)),
                          );
                        })
                      }
                    />
                  </div>
                </SettingLine>
              )}

              <SettingLine label="Self-check">
                <SelectInput
                  value={currentHeartbeatPreset}
                  onChange={(event) =>
                    updateConfig((draft) => {
                      const value = event.target.value;
                      if (value === "quiet" || value === "standard" || value === "active") {
                        applyHeartbeatPreset(draft, value);
                      }
                    })
                  }
                >
                  <option value="quiet">Quiet</option>
                  <option value="standard">Standard</option>
                  <option value="active">Active</option>
                  {currentHeartbeatPreset === "custom" && <option value="custom">Custom</option>}
                </SelectInput>
              </SettingLine>

              <SettingLine label="Internet access">
                <Toggle
                  label="Allow web search"
                  checked={config.agents.research ?? false}
                  onChange={(checked) =>
                    updateConfig((draft) => {
                      draft.agents.research = checked;
                      draft.agents.warmstart ??= {};
                      draft.agents.warmstart.enabled = checked;
                    })
                  }
                />
              </SettingLine>

              <CollapsibleSection icon="sliders" title="Customize agents" summary={`${plannedAgents} prepared agents · ${backendSummary}`}>
                <div className="grid gap-1.5">
                  <div className="hidden grid-cols-[minmax(92px,1fr)_130px_minmax(120px,1fr)_160px] gap-2 px-2 font-mono text-[10px] uppercase tracking-wider text-muted-fg md:grid">
                    <span>Agent</span>
                    <span>Backend</span>
                    <span>Model</span>
                    <span>Thinking depth</span>
                  </div>
                  {agentAssignments.map((assignment, index) => {
                    const { agentId, islandId } = agentDisplayId(index, islandCount);
                    const runtime = assignment.runtime || config.agents.runtime || "claude_code";
                    const reasoningOptions = reasoningOptionsForRuntime(runtime);
                    return (
                      <div
                        key={`${agentId}-${index}`}
                        className="grid gap-2 rounded-lg border border-border/80 bg-surface/65 p-2 transition-colors hover:bg-surface/90 md:grid-cols-[minmax(92px,1fr)_130px_minmax(120px,1fr)_160px] md:items-center"
                      >
                        <div className="flex min-w-0 items-center justify-between gap-2 md:block">
                          <div>
                            <p className="truncate text-[13px] font-medium">{agentId}</p>
                            {islandId !== null && (
                              <p className="mt-0.5 font-mono text-[10px] uppercase tracking-wider text-muted-fg">
                                Group {islandId}
                              </p>
                            )}
                          </div>
                        </div>
                        <SelectInput
                          disabled={planLocked}
                          aria-label={`${agentId} backend`}
                          value={runtime}
                          onChange={(event) =>
                            updateConfig((draft) => {
                              updateAgentAssignment(draft, index, (item) => {
                                item.runtime = event.target.value;
                              });
                            })
                          }
                        >
                          <option value="codex">codex</option>
                          <option value="claude_code">claude_code</option>
                          <option value="opencode">opencode</option>
                        </SelectInput>
                        <TextInput
                          disabled={planLocked}
                          aria-label={`${agentId} model`}
                          value={assignment.model || config.agents.model || ""}
                          onChange={(event) =>
                            updateConfig((draft) => {
                              updateAgentAssignment(draft, index, (item) => {
                                item.model = event.target.value;
                              });
                            })
                          }
                        />
                        <ReasoningSlider
                          disabled={planLocked}
                          value={assignmentReasoning(assignment, currentReasoning, runtime)}
                          options={reasoningOptions}
                          onChange={(value) =>
                            updateConfig((draft) => {
                              updateAgentAssignment(draft, index, (item) => {
                                item.runtime_options ??= {};
                                item.runtime_options.model_reasoning_effort = value;
                              });
                            })
                          }
                        />
                      </div>
                    );
                  })}
                </div>
              </CollapsibleSection>
            </Section>

            <Section title="Run Limits" icon="timer">
              <HelpText>
                Set the run duration and the scoring method. Hardware limits are advanced controls.
              </HelpText>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <Field label="Run length min">
                  <TextInput
                    type="number"
                    min={0}
                    step={5}
                    value={configuredRuntime}
                    onChange={(event) =>
                      updateConfig((draft) => {
                        draft.run ??= {};
                        draft.run.max_runtime_seconds = Math.max(
                          0,
                          Math.round(parseNumber(event.target.value, 0) * 60),
                        );
                      })
                    }
                  />
                </Field>
                <Field label="Scoring method">
                  <SelectInput
                    value={config.grader.profile ?? profileOptions[0] ?? "default"}
                    onChange={(event) =>
                      updateConfig((draft) => {
                        draft.grader.profile = event.target.value;
                      })
                    }
                  >
                    {profileOptions.map((name) => (
                      <option key={name} value={name}>
                        {config.grader.profiles?.[name]?.label || name}
                      </option>
                    ))}
                  </SelectInput>
                </Field>
              </div>
              <CollapsibleSection
                icon="cpu"
                title="Evaluation resources"
                summary={`${config.grader.parallel?.resources?.cpu_cores ?? 0} CPU · ${config.grader.parallel?.resources?.gpu_count ?? 0} GPU`}
              >
                <HelpText>
                  Leave values at 0 when Codex should prepare a task-specific default.
                </HelpText>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                  <Field label="CPU cores">
                    <TextInput
                      type="number"
                      min={0}
                      value={config.grader.parallel?.resources?.cpu_cores ?? 0}
                      onChange={(event) =>
                        updateConfig((draft) => {
                          updateEvaluatorResources(draft, (resources) => {
                            resources.cpu_cores = Math.max(
                              0,
                              Math.round(parseNumber(event.target.value, 0)),
                            );
                          });
                        })
                      }
                    />
                  </Field>
                  <Field label="Memory GB">
                    <TextInput
                      type="number"
                      min={0}
                      step={1}
                      value={config.grader.parallel?.resources?.memory_gb ?? 0}
                      onChange={(event) =>
                        updateConfig((draft) => {
                          updateEvaluatorResources(draft, (resources) => {
                            resources.memory_gb = Math.max(0, parseNumber(event.target.value, 0));
                          });
                        })
                      }
                    />
                  </Field>
                  <Field label="GPUs">
                    <TextInput
                      type="number"
                      min={0}
                      value={config.grader.parallel?.resources?.gpu_count ?? 0}
                      onChange={(event) =>
                        updateConfig((draft) => {
                          updateEvaluatorResources(draft, (resources) => {
                            resources.gpu_count = Math.max(
                              0,
                              Math.round(parseNumber(event.target.value, 0)),
                            );
                          });
                        })
                      }
                    />
                  </Field>
                </div>
                <Field label="GPU IDs">
                  <TextInput
                    value={(config.grader.parallel?.resources?.gpu_ids ?? []).join(",")}
                    placeholder="0,1,2"
                    onChange={(event) =>
                      updateConfig((draft) => {
                        updateEvaluatorResources(draft, (resources) => {
                          const gpuIds = parseGpuIds(event.target.value);
                          resources.gpu_ids = gpuIds;
                          resources.gpu_count = gpuIds.length;
                        });
                      })
                    }
                  />
                </Field>
              </CollapsibleSection>
            </Section>

          </div>
          <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr))]">
            <CollapsibleSection icon="file" title="Task brief" summary={config.task.name}>
              <Field label="Name">
                <ReadOnlyValue value={config.task.name} />
              </Field>
              {meta && (
                <Field label="Workspace">
                  <ReadOnlyValue value={meta.run_dir} />
                </Field>
              )}
              <Field label="Agent instructions">
                <ReadOnlyTextArea value={config.task.description} />
              </Field>
            </CollapsibleSection>

            <AgentPlanPanel plan={plan} />

            <CollapsibleSection icon="message" title="Message on resume" summary="Optional instruction sent to agents when resuming">
              <Field label="Instruction">
                <TextArea
                  value={instruction}
                  className="min-h-28"
                  onChange={(event) => setInstruction(event.target.value)}
                />
              </Field>
            </CollapsibleSection>
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border/70 bg-muted/20 px-3 py-2">
      <p className="font-mono text-[10px] uppercase tracking-wider text-muted-fg">{label}</p>
      <p className="mt-1 truncate font-mono text-[17px] leading-6 text-foreground">{value}</p>
    </div>
  );
}
