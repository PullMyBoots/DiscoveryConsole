import {
  useEffect,
  useState,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";
import {
  api,
  type AgentAssignmentConfig,
  type ControlConfigResponse,
  type ControlReadinessResponse,
  type RunStatus,
  type TaskConfig,
} from "../lib/api";
import { useSSE } from "../hooks/useSSE";

type BusyAction = "save" | "run" | "stop" | null;
type ReasoningEffort = "minimal" | "low" | "medium" | "high" | "xhigh" | "max";
type ReasoningOption = { value: ReasoningEffort; label: string };
type IconName = "alert" | "check" | "cpu" | "file" | "globe" | "gauge" | "pause" | "play" | "save" | "sliders" | "timer" | "users";

const inputClass =
  "w-full rounded-md border border-border bg-surface/90 px-3 py-2 text-[13px] outline-none transition-colors placeholder:text-muted-fg/60 hover:border-border-strong focus:border-accent disabled:cursor-not-allowed disabled:bg-muted/40 disabled:text-muted-fg";
const labelClass = "mb-1.5 block font-mono text-[10px] uppercase tracking-wider text-muted-fg";
const panelClass = "min-w-0 rounded-xl border border-border bg-surface/85 shadow-[0_1px_0_rgba(23,32,29,0.04),0_14px_34px_rgba(23,32,29,0.045)]";

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

function formatScore(value?: number | null): string {
  if (value == null) return "-";
  return Number.isFinite(value) ? value.toPrecision(5) : String(value);
}

function formatTokens(value?: number | null): string {
  if (value == null) return "-";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(Math.round(value));
}

function displayRunState(status: RunStatus | null): string {
  if (status?.manager_alive) return "running";
  if (status?.run_state?.stopped_reason === "manual") return "paused";
  return "stopped";
}

function isRunningAgentStatus(status: string): boolean {
  return ["active", "evaluating", "waiting", "reflect_loop"].includes(status);
}

function isPlanLocked(status: RunStatus | null): boolean {
  if (!status) return false;
  return Boolean(
    status.manager_alive ||
      status.total_attempts > 0 ||
      status.eval_count > 0 ||
      status.agents.length > 0 ||
      status.run_state?.started_at,
  );
}

function missingReadinessLabels(readiness: ControlReadinessResponse | null): string[] {
  if (!readiness || readiness.status !== "missing") return [];
  return readiness.checks
    .filter((check) => check.status === "missing")
    .map((check) => check.label || check.id);
}

function readinessSummary(readiness: ControlReadinessResponse | null): string {
  if (!readiness) return "Checking workspace";
  if (readiness.status === "ready") return "Workspace ready";
  if (readiness.status === "warning") return "Ready with warnings";
  const missing = missingReadinessLabels(readiness);
  return missing.length > 0 ? `Needs Codex: ${missing.join(", ")}` : "Needs Codex preparation";
}

function resourceValue(config: TaskConfig, key: string): number {
  const resources = (config.grader.parallel?.resources ?? {}) as Record<string, unknown>;
  const value = resources[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
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

function withForcedResearch(config: TaskConfig): TaskConfig {
  const next = cloneConfig(config);
  next.agents.research = true;
  return next;
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
    check: <path d="M20 6 9 17l-5-5" />,
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

function Section({
  title,
  icon,
  children,
  className = "",
}: {
  title: string;
  icon: IconName;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`${panelClass} p-4 ${className}`}>
      <div className="mb-4 flex items-center gap-2 border-b border-border/70 pb-3">
        <IconBadge name={icon} />
        <h2 className="font-mono text-[10px] uppercase tracking-wider text-muted-fg">{title}</h2>
      </div>
      <div className="grid gap-4">{children}</div>
    </section>
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

function HelpText({ children }: { children: ReactNode }) {
  return <p className="font-body text-[12px] leading-relaxed text-muted-fg">{children}</p>;
}

function SummaryItem({ label, value, icon }: { label: string; value: string; icon?: IconName }) {
  return (
    <div className="flex min-w-0 items-start gap-3 rounded-lg border border-border/70 bg-muted/20 px-3 py-2">
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

function StatusPill({ state }: { state: string }) {
  const cls =
    state === "running"
      ? "border-success/40 bg-success-soft text-success"
      : state === "paused"
        ? "border-warning/40 bg-warning-soft text-warning"
        : "border-border bg-muted/40 text-muted-fg";
  return (
    <span className={`rounded-full border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider ${cls}`}>
      {state}
    </span>
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
  const [busy, setBusy] = useState<BusyAction>(null);
  const [notice, setNotice] = useState<string>("");

  const refresh = () => {
    api.controlConfig()
      .then((data) => {
        setMeta(data);
        setConfig(withForcedResearch(data.config));
      })
      .catch((error) => setNotice(messageFromError(error)));
    api.status().then(setStatus).catch(() => {});
    api.controlReadiness().then(setReadiness).catch(() => {});
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
    },
    "attempt:update": () => {
      api.status().then(setStatus).catch(() => {});
      api.controlReadiness().then(setReadiness).catch(() => {});
    },
    "eval:update": () => api.status().then(setStatus).catch(() => {}),
  });

  const updateConfig = (updater: (draft: TaskConfig) => void) => {
    setConfig((current) => {
      if (!current) return current;
      const draft = cloneConfig(current);
      updater(draft);
      return withForcedResearch(draft);
    });
  };

  const saveConfig = async (): Promise<TaskConfig | null> => {
    if (!config) return null;
    const next = withForcedResearch(config);
    const response = await api.saveControlConfig(next);
    if (response.config) {
      const saved = withForcedResearch(response.config);
      setConfig(saved);
      api.controlReadiness().then(setReadiness).catch(() => {});
      return saved;
    }
    api.controlReadiness().then(setReadiness).catch(() => {});
    return next;
  };

  const onSave = async () => {
    setBusy("save");
    setNotice("");
    try {
      await saveConfig();
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
        <div className="rounded-xl border border-border p-5 font-mono text-[12px] text-muted-fg">
          Loading control state
        </div>
      </div>
    );
  }

  const displayState = displayRunState(status);
  const runLabel = meta ? `${meta.task} / ${meta.run}` : "loading";
  const missingReadiness = missingReadinessLabels(readiness);
  const runBlocked = missingReadiness.length > 0;
  const planLocked = isPlanLocked(status);
  const configuredRuntime = runtimeMinutes(config);
  const currentReasoning = reasoningEffort(config);
  const agentAssignments = materializeAgentAssignments(config);
  const profileNames = Object.keys(config.grader.profiles ?? {});
  const profileOptions =
    profileNames.length > 0
      ? profileNames
      : [config.grader.profile ?? "default"].filter((name, index, arr) => name && arr.indexOf(name) === index);
  const selectedProfileName = config.grader.profile ?? profileOptions[0] ?? "default";
  const activeAgents = status?.agents.filter((agent) => isRunningAgentStatus(agent.status)).length ?? 0;
  const totalAgents = status?.agents.length ?? config.agents.count;

  return (
    <div className="control-scroll h-full min-h-0 overflow-y-scroll p-4 sm:p-5">
      <div className="mx-auto grid max-w-[1480px] gap-5">
        <section className={`${panelClass} overflow-hidden`}>
          <div className="grid gap-4 p-4 lg:grid-cols-[minmax(260px,1fr)_auto] lg:items-center">
            <div className="flex min-w-0 items-start gap-3">
              <IconBadge name="gauge" tone={status?.manager_alive ? "good" : displayState === "paused" ? "warn" : "neutral"} />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="font-display text-2xl">Control</h1>
                  <StatusPill state={displayState} />
                </div>
                <p className="mt-1 truncate font-mono text-[11px] text-muted-fg" title={runLabel}>
                  {runLabel}
                </p>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3 lg:w-[360px]">
              <button
                onClick={onRun}
                disabled={busy !== null || status?.manager_alive || runBlocked}
                title={runBlocked ? `Run blocked: ${missingReadiness.join(", ")}` : "Start or resume this run"}
                className="inline-flex min-h-[42px] items-center justify-center gap-2 rounded-md bg-accent px-3 py-2 text-[13px] font-semibold text-white shadow-sm transition-colors hover:bg-accent-fg disabled:cursor-not-allowed disabled:opacity-35"
              >
                <Icon name="play" className="h-3.5 w-3.5" />
                Start
              </button>
              <button
                onClick={onStop}
                disabled={busy !== null || !status?.manager_alive}
                className="inline-flex min-h-[42px] items-center justify-center gap-2 rounded-md border border-border bg-surface/70 px-3 py-2 text-[13px] font-medium transition-colors hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-35"
              >
                <Icon name="pause" className="h-3.5 w-3.5" />
                Stop
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
          <div className="border-t border-border/60 px-4 py-2">
            <p className="font-mono text-[10px] leading-4 text-muted-fg">
              {notice || "Open-space scripts and data are callable/read-only for agents. Web research is forced on."}
            </p>
          </div>
        </section>

        <div className="grid gap-5 xl:grid-cols-[minmax(300px,0.8fr)_minmax(520px,1.2fr)]">
          <Section title="Resource budget" icon="cpu">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="CPU cores">
                <TextInput
                  type="number"
                  min={0}
                  value={resourceValue(config, "cpu_cores")}
                  onChange={(event) =>
                    updateConfig((draft) => {
                      updateEvaluatorResources(draft, (resources) => {
                        resources.cpu_cores = Math.max(0, Math.round(parseNumber(event.target.value, 0)));
                      });
                    })
                  }
                />
              </Field>
              <Field label="GPUs">
                <TextInput
                  type="number"
                  min={0}
                  value={resourceValue(config, "gpu_count")}
                  onChange={(event) =>
                    updateConfig((draft) => {
                      updateEvaluatorResources(draft, (resources) => {
                        resources.gpu_count = Math.max(0, Math.round(parseNumber(event.target.value, 0)));
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
                  value={resourceValue(config, "memory_gb")}
                  onChange={(event) =>
                    updateConfig((draft) => {
                      updateEvaluatorResources(draft, (resources) => {
                        resources.memory_gb = Math.max(0, parseNumber(event.target.value, 0));
                      });
                    })
                  }
                />
              </Field>
              <Field label="Storage GB">
                <TextInput
                  type="number"
                  min={0}
                  step={1}
                  value={resourceValue(config, "storage_gb")}
                  onChange={(event) =>
                    updateConfig((draft) => {
                      updateEvaluatorResources(draft, (resources) => {
                        resources.storage_gb = Math.max(0, parseNumber(event.target.value, 0));
                      });
                    })
                  }
                />
              </Field>
            </div>
            <HelpText>
              These limits are attached to the eval runner resource pool. A zero value means the task default is used.
            </HelpText>
          </Section>

          <Section title="Agents and eval" icon="sliders">
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_170px]">
              <Field label="Eval script">
                <TextInput
                  value={config.grader.entrypoint ?? ""}
                  placeholder="path/to/eval.py"
                  onChange={(event) =>
                    updateConfig((draft) => {
                      draft.grader.entrypoint = event.target.value;
                    })
                  }
                />
              </Field>
              <Field label="Eval profile">
                <SelectInput
                  value={selectedProfileName}
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

            <div className="grid gap-2">
              <div className="hidden grid-cols-[minmax(82px,0.65fr)_130px_minmax(120px,1fr)_160px] gap-2 px-2 font-mono text-[10px] uppercase tracking-wider text-muted-fg md:grid">
                <span>Agent</span>
                <span>Backend</span>
                <span>Model</span>
                <span>Reasoning</span>
              </div>
              {agentAssignments.map((assignment, index) => {
                const agentId = `agent-${index + 1}`;
                const runtime = assignment.runtime || config.agents.runtime || "claude_code";
                const reasoningOptions = reasoningOptionsForRuntime(runtime);
                return (
                  <div
                    key={`${agentId}-${index}`}
                    className="grid gap-2 rounded-lg border border-border/80 bg-surface/65 p-2 md:grid-cols-[minmax(82px,0.65fr)_130px_minmax(120px,1fr)_160px] md:items-center"
                  >
                    <p className="truncate text-[13px] font-medium">{agentId}</p>
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
          </Section>

          <Section title="Run" icon="timer" className="xl:col-span-2">
            <Field label="Total run time min">
              <TextInput
                type="number"
                min={0}
                step={5}
                value={configuredRuntime}
                onChange={(event) =>
                  updateConfig((draft) => {
                    draft.run ??= {};
                    draft.run.max_runtime_seconds = Math.max(0, Math.round(parseNumber(event.target.value, 0) * 60));
                  })
                }
              />
            </Field>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              <SummaryItem icon="users" label="Agents" value={`${activeAgents}/${totalAgents}`} />
              <SummaryItem icon="file" label="Attempts" value={String(status?.total_attempts ?? 0)} />
              <SummaryItem icon="gauge" label="Evaluations" value={String(status?.eval_count ?? 0)} />
              <SummaryItem icon="timer" label="Remaining" value={formatDuration(status?.run_state?.remaining_seconds)} />
              <SummaryItem icon="check" label="Best score" value={formatScore(status?.best_score)} />
              <SummaryItem icon="globe" label="Token usage" value={formatTokens(status?.usage?.total_tokens)} />
            </div>
            <div
              className={`rounded-lg border px-3 py-3 ${
                runBlocked
                  ? "border-danger/35 bg-danger-soft text-danger"
                  : "border-success/35 bg-success-soft text-success"
              }`}
            >
              <div className="flex items-start gap-2">
                <Icon name={runBlocked ? "alert" : "check"} className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="min-w-0">
                  <p className="text-[13px] font-semibold">{runBlocked ? "Cannot run yet" : "Ready to run"}</p>
                  <p className="mt-1 truncate font-mono text-[11px]">{readinessSummary(readiness)}</p>
                </div>
              </div>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}
