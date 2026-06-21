const BASE = "/api";

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = "";
    try {
      const payload = await res.json();
      if (payload && typeof payload.message === "string") detail = payload.message;
      else if (payload && typeof payload.error === "string") detail = payload.error;
    } catch {
      // Keep the generic status message when the server did not return JSON.
    }
    throw new Error(detail || `API error: ${res.status}`);
  }
  return res.json();
}

/* Types */

export interface Attempt {
  commit_hash: string;
  agent_id: string;
  title: string;
  score: number | null;
  status: string;
  parent_hash: string | null;
  timestamp: string;
  feedback: string;
  metadata?: Record<string, unknown>;
}

export interface EvalProgress {
  type?: string;
  job_id?: string;
  phase?: string;
  current?: number;
  total?: number;
  percent?: number | null;
  message?: string;
  timestamp?: string;
  eval_version?: string;
  eval_profile?: string;
}

export interface ResourceConfig {
  cpu_cores?: number;
  memory_gb?: number;
  gpu_count?: number;
  gpu_ids?: string[];
}

export interface AgentAssignmentConfig {
  runtime?: string;
  model?: string;
  count?: number;
  runtime_options?: Record<string, unknown>;
}

export interface EvalJob {
  commit_hash: string;
  agent_id: string;
  title: string;
  timestamp: string;
  queue_status: "waiting" | "evaluating";
  island_id?: string | null;
  eval_version?: string;
  eval_profile?: string;
  resources?: ResourceConfig | Record<string, unknown>;
  progress?: EvalProgress | null;
}

export interface EvalJobsResponse {
  max_workers: number;
  resource_pool?: ResourceConfig | Record<string, unknown>;
  jobs: EvalJob[];
}

export interface Note {
  date: string;
  title: string;
  body: string;
  creator?: string;
  filename?: string;
  relative_path?: string;
  category?: string;
  index: number;
}

export interface Skill {
  name: string;
  description: string;
  creator: string;
  created: string;
  path: string;
}

export interface KnowledgeSource {
  title: string;
  relative_path?: string;
  category?: string;
  source?: string;
  origin_url?: string;
  url?: string;
  added_by?: string;
  added_at?: string;
  status?: string;
  version?: string;
  island_id?: string;
  size_bytes?: number;
  modified?: number;
  [key: string]: unknown;
}

export interface KnowledgeResponse {
  sources: KnowledgeSource[];
}

export interface EvalSpecResponse {
  content: string;
  path: string;
  exists: boolean;
  updated_at?: string;
  ok?: boolean;
  writer?: string;
}

export interface ReviewAttemptSummary {
  commit_hash: string;
  agent_id: string;
  title: string;
  score: number | null;
  status: string;
  timestamp: string;
  budget_class: string;
  is_baseline: boolean;
  eval_version?: string | null;
  eval_profile?: string | null;
  score_components?: Record<string, unknown>;
}

export interface ReviewFlag {
  severity: "high" | "medium" | "low" | string;
  label: string;
  detail: string;
}

export interface ReviewSummary {
  task: {
    name: string;
    eval_version: string;
    eval_profile: string;
    direction: string;
  };
  run_state: RunState | Record<string, unknown>;
  attempts: {
    total: number;
    scored: number;
    real_scored: number;
    pending: number;
    crashed: number;
    timeout: number;
    tune: number;
    grader_error: number;
    baseline: number;
    by_status: Record<string, number>;
    by_agent: Array<{
      agent_id: string;
      attempts: number;
      scored: number;
      pending: number;
      crashed: number;
      best: ReviewAttemptSummary | null;
    }>;
    top: ReviewAttemptSummary[];
    recent: ReviewAttemptSummary[];
    best: ReviewAttemptSummary | null;
    best_baseline: ReviewAttemptSummary | null;
    improvement_over_baseline: number | null;
    eval_versions: Record<string, number>;
    eval_profiles: Record<string, number>;
  };
  knowledge: {
    sources: number;
    notes: number;
    proposed_sources: number;
    inbox_sources: number;
    sources_by_category: Record<string, number>;
    sources_by_status: Record<string, number>;
    notes_by_category: Record<string, number>;
    recent_notes: Array<{
      title: string;
      date: string;
      category: string;
      relative_path: string;
    }>;
  };
  usage?: UsageSummary | Record<string, unknown>;
  readiness?: ControlReadinessResponse | Record<string, unknown>;
  flags: ReviewFlag[];
  recommended_actions: string[];
}

export interface AddKnowledgeNoteResponse {
  ok: boolean;
  message?: string;
  title?: string;
  path?: string;
  relative_path?: string;
  created?: string;
}

export interface AddKnowledgeSourceResponse {
  ok: boolean;
  message?: string;
  entry?: KnowledgeSource;
  path?: string;
}

export interface UpdateKnowledgeSourceStatusResponse {
  ok: boolean;
  message?: string;
  entry?: KnowledgeSource;
  path?: string;
}

export interface SkillDetail {
  content: string;
  metadata: Record<string, string>;
  body: string;
  files: string[];
}

export interface AgentStatus {
  agent_id: string;
  island_id?: string | null;
  status: "active" | "idle" | "stopped" | "paused" | "evaluating" | "waiting" | "heartbeat";
  sessions: number;
  last_activity: number;
  last_activity_age_seconds?: number | null;
  active_seconds?: number | null;
  status_duration_seconds?: number | null;
  attempts: number;
  best_score: number | null;
  runtime_state?: string | null;
  desired_state?: "running" | "stopped" | string | null;
  usage?: UsageSummary;
}

export interface RunState {
  status: "starting" | "running" | "stopping" | "stopped" | string;
  started_at?: string | null;
  deadline_at?: string | null;
  max_runtime_seconds?: number;
  remaining_seconds?: number | null;
  elapsed_seconds?: number | null;
  stopped_reason?: string | null;
  updated_at?: string | null;
}

export interface RunStatus {
  manager_alive: boolean;
  manager_pid: number | null;
  eval_count: number;
  total_attempts: number;
  scored_attempts: number;
  crashed_attempts: number;
  best_score: number | null;
  best_title: string | null;
  run_state?: RunState;
  usage?: UsageSummary;
  agents: AgentStatus[];
}

export interface UsageSummary {
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  uncategorized_tokens: number;
  total_tokens: number;
  cache_hit_rate: number;
  total_cost_usd: number;
  duration_ms: number;
  duration_api_ms: number;
  num_turns: number;
  agents?: Record<string, UsageSummary>;
}

export interface LogEntry {
  type:
    | "thinking" | "tool_call" | "tool_result" | "text" | "system" | "error"
    | "coral_prompt" | "subagent_start" | "subagent_progress" | "subagent_done"
    | "compact" | "result";
  content: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  details: Record<string, any>;
  timestamp: string;
}

export interface LogTurn {
  index: number;
  entries: LogEntry[];
  usage: {
    input_tokens?: number;
    output_tokens?: number;
    cache_creation?: number;
    cache_read?: number;
  };
  timestamp: string;
}

export interface SessionMeta {
  total_cost_usd?: number;
  duration_ms?: number;
  duration_api_ms?: number;
  num_turns?: number;
  stop_reason?: string;
  session_id?: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  usage?: Record<string, any>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  model_usage?: Record<string, any>;
}

export interface LogSession {
  session_index: number;
  turns: LogTurn[];
  meta?: SessionMeta;
}

export interface LogData {
  agent_id: string;
  log_files: Array<{
    path: string;
    index: number;
    size_bytes: number;
    modified: number;
  }>;
  turns: LogTurn[];
  sessions?: LogSession[];
  agent_meta?: {
    total_cost_usd?: number;
    duration_ms?: number;
    duration_api_ms?: number;
    num_turns?: number;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    usage?: Record<string, any>;
  };
}

export interface RunInfo {
  timestamp: string;
  status: "running" | "stopped";
  attempts: number;
  is_latest: boolean;
}

export interface TaskRuns {
  slug: string;
  runs: RunInfo[];
}

export interface RunsResponse {
  current: { task: string; run: string };
  tasks: TaskRuns[];
}

export interface CreateRunResponse {
  ok: boolean;
  message?: string;
  task: string;
  run: string;
  run_dir: string;
  coral_dir: string;
}

export interface TaskConfig {
  task: {
    name: string;
    description: string;
    tips?: string;
  };
  grader: {
    entrypoint?: string;
    setup?: string[];
    timeout?: number;
    direction?: "maximize" | "minimize";
    eval_version?: string;
    profile?: string;
    profiles?: Record<
      string,
      {
        label?: string;
        timeout?: number;
        args?: Record<string, unknown>;
        resources?: ResourceConfig;
      }
    >;
    args?: Record<string, unknown>;
    private?: string[];
    resources?: ResourceConfig;
    max_pending_per_agent?: number;
    parallel?: {
      max_workers?: number;
      resources?: ResourceConfig;
    };
  };
  agents: {
    count: number;
    runtime?: string;
    model?: string;
    max_turns?: number;
    timeout?: number;
    skills?: string[];
    research?: boolean;
    stagger_seconds?: number;
    runtime_options?: Record<string, unknown>;
    gateway?: {
      enabled?: boolean;
      port?: number;
      config?: string;
      api_key?: string;
    };
    warmstart?: {
      enabled?: boolean;
    };
    heartbeat?: Array<Record<string, unknown>>;
    assignments?: AgentAssignmentConfig[];
  };
  islands?: {
    count?: number;
    migration?: {
      enabled?: boolean;
      every?: number;
      rank_window?: number;
      min_evals?: number;
      dest_weighting?: string;
      max_per_cycle?: number;
      notify_island?: boolean;
    };
  };
  sharing?: {
    attempts?: boolean;
    notes?: boolean;
    skills?: boolean;
  };
  workspace?: {
    results_dir?: string;
    repo_path?: string;
    setup?: string[];
    base_dir?: string;
    run_dir?: string;
  };
  run?: {
    verbose?: boolean;
    ui?: boolean;
    session?: string;
    docker_image?: string | null;
    max_runtime_seconds?: number;
  };
  [key: string]: unknown;
}

export interface ControlConfigResponse {
  config: TaskConfig;
  task: string;
  run: string;
  run_dir: string;
  config_path: string;
  results_dir: string;
}

export interface ReadinessCheck {
  id: string;
  label: string;
  status: "ready" | "warning" | "missing";
  detail: string;
  count?: number;
  path?: string;
}

export interface ControlReadinessResponse {
  status: "ready" | "warning" | "missing";
  checks: ReadinessCheck[];
}

export interface AgentPlanAgent {
  agent_id: string;
  island_id?: string | null;
  title: string;
  summary: string;
  relative_path: string;
  path: string;
}

export interface AgentPlanTheme {
  island_id: string;
  title: string;
  summary: string;
  relative_path: string;
  path: string;
}

export interface AgentPlanIsland {
  island_id: string | null;
  theme: AgentPlanTheme | null;
  agents: AgentPlanAgent[];
}

export interface ControlPlanResponse {
  status: "ready" | "partial" | "missing" | string;
  planned_agents: number;
  brief_count: number;
  missing_briefs: number;
  island_count: number;
  islands: AgentPlanIsland[];
  agents: AgentPlanAgent[];
  paths: {
    agent_briefs: string;
    island_themes: string;
  };
}

export interface ControlInstructionResponse {
  instruction: string;
  path: string;
}

export interface ControlActionResponse {
  ok: boolean;
  message?: string;
  pid?: number;
  log_path?: string;
  instruction_path?: string | null;
  stopped?: string[];
  config?: TaskConfig;
  config_path?: string;
}

/* API functions */

export const api = {
  config: () => get<TaskConfig>("/config"),
  attempts: () => get<Attempt[]>("/attempts"),
  evals: () => get<EvalJobsResponse>("/evals"),
  leaderboard: (top = 20) => get<Attempt[]>(`/leaderboard?top=${top}`),
  attempt: (hash: string) => get<Attempt>(`/attempts/${hash}`),
  agentAttempts: (id: string) => get<Attempt[]>(`/attempts/agent/${id}`),
  notes: () => get<Note[]>("/notes"),
  knowledge: () => get<KnowledgeResponse>("/knowledge"),
  evalSpec: () => get<EvalSpecResponse>("/knowledge/eval-spec"),
  review: () => get<ReviewSummary>("/review"),
  addKnowledgeNote: (payload: { title: string; body: string; category?: string }) =>
    post<AddKnowledgeNoteResponse>("/knowledge/notes", payload),
  addKnowledgeSource: (payload: { title: string; url?: string; category?: string; note?: string }) =>
    post<AddKnowledgeSourceResponse>("/knowledge/sources", payload),
  updateKnowledgeSourceStatus: (payload: {
    selector: {
      id?: string;
      relative_path?: string;
      title?: string;
      origin_url?: string;
      url?: string;
    };
    status: "accepted" | "rejected" | "archived" | "proposed";
  }) => post<UpdateKnowledgeSourceStatusResponse>("/knowledge/sources/status", payload),
  saveEvalSpec: (content: string) =>
    post<EvalSpecResponse>("/knowledge/eval-spec", { content }),
  skills: () => get<Skill[]>("/skills"),
  skill: (name: string) => get<SkillDetail>(`/skills/${name}`),
  logs: (agentId: string, signal?: AbortSignal) => get<LogData>(`/logs/${agentId}`, signal),
  logsList: () => get<Record<string, Array<{ path: string; index: number; size_bytes: number; modified: number }>>>("/logs"),
  status: () => get<RunStatus>("/status"),
  runs: () => get<RunsResponse>("/runs"),
  createRun: () => post<CreateRunResponse>("/runs/new", {}),
  switchRun: (task: string, run: string) =>
    post<{ ok: boolean; task: string; run: string }>("/runs/switch", { task, run }),
  controlConfig: () => get<ControlConfigResponse>("/control/config"),
  controlReadiness: () => get<ControlReadinessResponse>("/control/readiness"),
  controlPlan: () => get<ControlPlanResponse>("/control/plan"),
  saveControlConfig: (config: TaskConfig) =>
    post<ControlActionResponse>("/control/config", { config }),
  controlInstruction: () => get<ControlInstructionResponse>("/control/instruction"),
  saveControlInstruction: (instruction: string) =>
    post<ControlInstructionResponse & { ok: boolean }>("/control/instruction", { instruction }),
  controlResume: () => post<ControlActionResponse>("/control/resume", {}),
  controlStop: () => post<ControlActionResponse>("/control/stop", {}),
  agentStop: (agentId: string) =>
    post<ControlActionResponse & { agent_id: string; desired_state: string }>(
      `/agents/${agentId}/stop`,
      {},
    ),
  agentResume: (agentId: string) =>
    post<ControlActionResponse & { agent_id: string; desired_state: string }>(
      `/agents/${agentId}/resume`,
      {},
    ),
  agentPrompt: (agentId: string, prompt: string) =>
    post<ControlActionResponse & { agent_id: string; action: string }>(
      `/agents/${agentId}/prompt`,
      { prompt },
    ),
};
