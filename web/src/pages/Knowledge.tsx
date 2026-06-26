import { useEffect, useMemo, useState } from "react";
import { api, type Attempt, type KnowledgeSource, type Skill } from "../lib/api";
import { useSSE } from "../hooks/useSSE";
import { EmptyState, PageTitle, Panel, inputClass } from "../components/Ui";
import { scoreComponents } from "../lib/scores";

function formatScore(score: number | null | undefined): string {
  if (score == null) return "---";
  return Number.isFinite(score) ? score.toPrecision(5) : String(score);
}

function sourceType(source: KnowledgeSource): string {
  return source.category || source.source || "source";
}

function sourceLocation(source: KnowledgeSource): string {
  const url = source.origin_url || source.url;
  if (typeof url === "string" && url) return url;
  if (source.relative_path) return source.relative_path;
  return "";
}

function sourceSummary(source: KnowledgeSource): string {
  const record = source as Record<string, unknown>;
  for (const key of ["summary", "note", "description", "why", "body"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return sourceLocation(source) || "No summary available.";
}

function attemptKey(attempt: Attempt): string {
  return `${attempt.agent_id}:${attempt.commit_hash}`;
}

export default function Knowledge() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [selectedSource, setSelectedSource] = useState(0);
  const [selectedAgent, setSelectedAgent] = useState("");
  const [selectedAttemptKey, setSelectedAttemptKey] = useState("");

  const refresh = () => {
    api.skills().then(setSkills).catch(() => {});
    api.knowledge().then((data) => setSources(data.sources)).catch(() => {});
    api.attempts().then(setAttempts).catch(() => {});
  };

  useEffect(refresh, []);
  useSSE({
    "attempt:new": refresh,
    "attempt:update": refresh,
  });

  const attemptsByAgent = useMemo(() => {
    const map = new Map<string, Attempt[]>();
    for (const attempt of attempts) {
      const list = map.get(attempt.agent_id) ?? [];
      list.push(attempt);
      map.set(attempt.agent_id, list);
    }
    for (const list of map.values()) {
      list.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
    }
    return map;
  }, [attempts]);

  const agentIds = [...attemptsByAgent.keys()].sort();
  const effectiveAgent = selectedAgent || agentIds[0] || "";
  const agentAttempts = effectiveAgent ? attemptsByAgent.get(effectiveAgent) ?? [] : [];
  const effectiveAttempt =
    agentAttempts.find((attempt) => attemptKey(attempt) === selectedAttemptKey) ??
    agentAttempts[agentAttempts.length - 1] ??
    null;
  const selectedSourceEntry = sources[Math.min(selectedSource, Math.max(0, sources.length - 1))] ?? null;
  return (
    <div className="control-scroll h-full min-h-0 overflow-y-scroll p-4 sm:p-5">
      <div className="mx-auto grid max-w-[1480px] gap-5">
        <PageTitle
          icon="book"
          title="Knowledge"
          subtitle={`${sources.length} sources · ${attempts.length} eval records · ${skills.length} skills`}
        />

        <div className="grid gap-5 xl:grid-cols-[minmax(320px,0.9fr)_minmax(440px,1.25fr)]">
          <Panel title="External knowledge" icon="network">
            {sources.length === 0 ? (
              <EmptyState icon="network" title="No external sources" body="Papers, repos, docs, datasets, and web references appear here after indexing." />
            ) : (
              <div className="grid gap-4 lg:grid-cols-[minmax(220px,0.85fr)_minmax(0,1.15fr)]">
                <div className="overflow-hidden rounded-lg border border-border bg-surface/85">
                  {sources.map((source, index) => (
                    <button
                      key={`${source.title}:${source.relative_path ?? index}`}
                      onClick={() => setSelectedSource(index)}
                      className={`grid w-full grid-cols-[minmax(0,1fr)_auto] gap-3 border-b border-border px-3 py-2.5 text-left last:border-b-0 ${
                        index === selectedSource ? "bg-accent-soft" : "hover:bg-muted/40"
                      }`}
                    >
                      <span className="min-w-0 truncate text-[13px] font-medium">{source.title}</span>
                      <span className="rounded-md border border-border bg-surface/70 px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider text-muted-fg">
                        {sourceType(source)}
                      </span>
                    </button>
                  ))}
                </div>
                <SourceDetail source={selectedSourceEntry} />
              </div>
            )}
          </Panel>

          <Panel
            title="Practice knowledge"
            icon="activity"
            action={
              agentIds.length > 0 ? (
                <select
                  value={effectiveAgent}
                  onChange={(event) => {
                    setSelectedAgent(event.target.value);
                    setSelectedAttemptKey("");
                  }}
                  className={`${inputClass} w-[190px] py-1.5 font-mono text-[11px]`}
                >
                  {agentIds.map((agentId) => (
                    <option key={agentId} value={agentId}>
                      {agentId}
                    </option>
                  ))}
                </select>
              ) : null
            }
          >
            {agentAttempts.length === 0 ? (
              <EmptyState icon="activity" title="No eval records" body="Each agent's eval sequence appears here after it submits scored attempts." />
            ) : (
              <div className="grid gap-4 lg:grid-cols-[minmax(220px,0.85fr)_minmax(0,1.15fr)]">
                <div className="overflow-hidden rounded-lg border border-border bg-surface/85">
                  {agentAttempts.map((attempt, index) => (
                    <button
                      key={attemptKey(attempt)}
                      onClick={() => setSelectedAttemptKey(attemptKey(attempt))}
                      className={`grid w-full grid-cols-[82px_minmax(0,1fr)] gap-3 border-b border-border px-3 py-2.5 text-left last:border-b-0 ${
                        effectiveAttempt && attemptKey(effectiveAttempt) === attemptKey(attempt)
                          ? "bg-accent-soft"
                          : "hover:bg-muted/40"
                      }`}
                    >
                      <span className="font-mono text-[11px] text-muted-fg">eval #{index + 1}</span>
                      <span className="min-w-0 truncate text-[13px] font-medium">{formatScore(attempt.score)}</span>
                    </button>
                  ))}
                </div>
                <AttemptDetail attempt={effectiveAttempt} />
              </div>
            )}
          </Panel>
        </div>

        <Panel title={`Skills (${skills.length})`} icon="spark">
          {skills.length === 0 ? (
            <EmptyState icon="spark" title="No skills" body="Reusable agent skills appear here when they are installed for this run." />
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {skills.map((skill) => (
                <div key={skill.name} className="rounded-lg border border-border bg-surface/85 p-4">
                  <p className="truncate font-display text-[14px] font-semibold">{skill.name}</p>
                  {skill.description && (
                    <p className="mt-1 font-body text-[13px] leading-relaxed text-muted-fg">{skill.description}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

function SourceDetail({ source }: { source: KnowledgeSource | null }) {
  if (!source) {
    return <EmptyState icon="network" title="Select a source" />;
  }
  const location = sourceLocation(source);
  return (
    <div className="min-w-0 rounded-lg border border-border bg-muted/20 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-display text-[15px] font-semibold">{source.title}</p>
          <p className="mt-1 font-mono text-[10px] uppercase tracking-wider text-muted-fg">{sourceType(source)}</p>
        </div>
        {source.status && (
          <span className="rounded-md border border-border bg-surface/70 px-2 py-1 font-mono text-[9px] uppercase tracking-wider text-muted-fg">
            {source.status}
          </span>
        )}
      </div>
      <p className="mt-3 whitespace-pre-wrap font-body text-[13px] leading-relaxed text-muted-fg">{sourceSummary(source)}</p>
      {location && <p className="mt-3 truncate font-mono text-[10px] text-muted-fg">{location}</p>}
    </div>
  );
}

function AttemptDetail({ attempt }: { attempt: Attempt | null }) {
  if (!attempt) {
    return <EmptyState icon="activity" title="Select an eval record" />;
  }
  const components = Object.entries(scoreComponents(attempt));
  return (
    <div className="min-w-0 rounded-lg border border-border bg-muted/20 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-display text-[15px] font-semibold">{attempt.title || attempt.commit_hash.slice(0, 8)}</p>
          <p className="mt-1 font-mono text-[10px] text-muted-fg">
            {attempt.agent_id} · {attempt.commit_hash.slice(0, 8)} · {attempt.status}
          </p>
        </div>
        <p className="shrink-0 font-mono text-[15px] font-semibold">{formatScore(attempt.score)}</p>
      </div>

      {components.length > 0 && (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {components.map(([name, value]) => (
            <div key={name} className="rounded-md border border-border bg-background px-3 py-2">
              <p className="font-mono text-[10px] uppercase tracking-wider text-muted-fg">{name}</p>
              <p className="mt-1 font-mono text-[12px] text-foreground">
                {typeof value === "number" ? formatScore(value) : JSON.stringify(value)}
              </p>
            </div>
          ))}
        </div>
      )}

      <div className="mt-4 border-t border-border pt-3">
        <p className="font-mono text-[10px] uppercase tracking-wider text-muted-fg">Eval report</p>
        <p className="mt-2 whitespace-pre-wrap font-body text-[13px] leading-relaxed text-muted-fg">
          {attempt.feedback || "No detailed eval feedback recorded."}
        </p>
      </div>
    </div>
  );
}
