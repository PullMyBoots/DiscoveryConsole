import type { Attempt } from "./api";

export interface ScoreComponent {
  value?: number | null;
  name?: string;
  explanation?: string | null;
  metadata?: Record<string, unknown>;
}

export function scoreComponents(attempt: Attempt): Record<string, ScoreComponent> {
  const raw = attempt.metadata?.score_components;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const components: Record<string, ScoreComponent> = {};
  for (const [name, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!value || typeof value !== "object" || Array.isArray(value)) continue;
    components[name] = value as ScoreComponent;
  }
  return components;
}

export function scoreMetricNames(attempts: Attempt[]): string[] {
  const names = new Set<string>();
  for (const attempt of attempts) {
    for (const name of Object.keys(scoreComponents(attempt))) {
      names.add(name);
    }
  }
  return [...names].sort();
}

export function scoreValue(attempt: Attempt, metric: string): number | null {
  if (metric === "score") return attempt.score;
  const value = scoreComponents(attempt)[metric]?.value;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function scoreLabel(metric: string): string {
  return metric === "score" ? "Total score" : metric;
}

export function isBaselineAttempt(attempt: Attempt): boolean {
  const metadata = attempt.metadata ?? {};
  return (
    metadata.baseline === true ||
    metadata.is_baseline === true ||
    metadata.reference === "baseline" ||
    metadata.kind === "baseline"
  );
}

export function baselineLabel(attempt: Attempt): string {
  const metadata = attempt.metadata ?? {};
  for (const key of ["baseline_name", "baseline", "method", "reference_name"]) {
    const value = metadata[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return attempt.title || "baseline";
}
