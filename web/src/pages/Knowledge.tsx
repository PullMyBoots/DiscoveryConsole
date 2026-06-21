import { useEffect, useState, useMemo } from "react";
import { api, type EvalSpecResponse, type KnowledgeSource, type Note, type ReviewSummary, type Skill } from "../lib/api";
import { useSSE } from "../hooks/useSSE";
import { EmptyState, PageTitle, Panel, inputClass } from "../components/Ui";

const CATEGORY_ORDER = ["papers", "repos", "web", "docs", "datasets", "synthesis", "research", "experiments", "open-questions", "other", "raw"];
const CATEGORY_LABELS: Record<string, string> = {
  papers: "Papers",
  repos: "Repositories",
  web: "Web",
  docs: "Docs",
  datasets: "Datasets",
  synthesis: "Synthesis",
  research: "Research",
  experiments: "Experiments",
  "open-questions": "Questions",
  raw: "Raw Sources",
  other: "Other",
};

export default function Knowledge() {
  const [notes, setNotes] = useState<Note[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [review, setReview] = useState<ReviewSummary | null>(null);
  const [evalSpec, setEvalSpec] = useState<EvalSpecResponse | null>(null);
  const [evalSpecDraft, setEvalSpecDraft] = useState("");
  const [expandedNote, setExpandedNote] = useState<number | null>(null);
  const [reviewTitle, setReviewTitle] = useState("");
  const [reviewBody, setReviewBody] = useState("");
  const [reviewCategory, setReviewCategory] = useState("synthesis");
  const [sourceTitle, setSourceTitle] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceCategory, setSourceCategory] = useState("web");
  const [sourceNote, setSourceNote] = useState("");
  const [saving, setSaving] = useState<"note" | "source" | "source-status" | "eval-spec" | null>(null);
  const [notice, setNotice] = useState("");

  const refreshNotes = () => api.notes().then(setNotes).catch(() => {});
  const refreshSkills = () => api.skills().then(setSkills).catch(() => {});
  const refreshKnowledge = () => api.knowledge().then((data) => setSources(data.sources)).catch(() => {});
  const refreshReview = () => api.review().then(setReview).catch(() => {});
  const refreshEvalSpec = () =>
    api
      .evalSpec()
      .then((data) => {
        setEvalSpec(data);
        setEvalSpecDraft(data.content || "");
      })
      .catch(() => {});

  useEffect(() => {
    refreshNotes();
    refreshSkills();
    refreshKnowledge();
    refreshReview();
    refreshEvalSpec();
  }, []);

  useSSE({
    "note:update": () => {
      refreshNotes();
      refreshKnowledge();
      refreshReview();
    },
    "attempt:new": () => {
      refreshReview();
    },
  });

  const saveReviewNote = async () => {
    if (!reviewTitle.trim() || !reviewBody.trim()) return;
    setSaving("note");
    setNotice("");
    try {
      await api.addKnowledgeNote({
        title: reviewTitle,
        body: reviewBody,
        category: reviewCategory,
      });
      setReviewTitle("");
      setReviewBody("");
      setNotice("Review note saved");
      refreshNotes();
      refreshKnowledge();
      refreshReview();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Save failed");
    } finally {
      setSaving(null);
    }
  };

  const saveSource = async () => {
    if (!sourceTitle.trim() || (!sourceUrl.trim() && !sourceNote.trim())) return;
    setSaving("source");
    setNotice("");
    try {
      await api.addKnowledgeSource({
        title: sourceTitle,
        url: sourceUrl,
        category: sourceCategory,
        note: sourceNote,
      });
      setSourceTitle("");
      setSourceUrl("");
      setSourceNote("");
      setNotice("Reference saved");
      refreshKnowledge();
      refreshReview();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Save failed");
    } finally {
      setSaving(null);
    }
  };

  const updateSourceStatus = async (
    source: KnowledgeSource,
    status: "accepted" | "rejected" | "archived" | "proposed",
  ) => {
    const selector = sourceSelector(source);
    if (!selector) return;
    setSaving("source-status");
    setNotice("");
    try {
      await api.updateKnowledgeSourceStatus({ selector, status });
      setNotice(`Reference marked ${status}`);
      refreshKnowledge();
      refreshReview();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Update failed");
    } finally {
      setSaving(null);
    }
  };

  const saveEvalSpec = async () => {
    setSaving("eval-spec");
    setNotice("");
    try {
      const saved = await api.saveEvalSpec(evalSpecDraft);
      setEvalSpec(saved);
      setEvalSpecDraft(saved.content || "");
      setNotice("Eval spec saved");
      refreshReview();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Save failed");
    } finally {
      setSaving(null);
    }
  };

  const groupedNotes = useMemo(() => {
    const groups: Record<string, Note[]> = {};
    for (const note of notes) {
      const cat = note.category || "other";
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(note);
    }
    // Sort categories by defined order, unknown categories at the end
    const sorted = Object.entries(groups).sort(([a], [b]) => {
      const ia = CATEGORY_ORDER.indexOf(a);
      const ib = CATEGORY_ORDER.indexOf(b);
      return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
    });
    return sorted;
  }, [notes]);

  const groupedSources = useMemo(() => {
    const groups: Record<string, KnowledgeSource[]> = {};
    for (const source of sources) {
      const cat = source.category || "other";
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(source);
    }
    return Object.entries(groups).sort(([a], [b]) => {
      const ia = CATEGORY_ORDER.indexOf(a);
      const ib = CATEGORY_ORDER.indexOf(b);
      return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
    });
  }, [sources]);

  return (
    <>
      <div className="control-scroll h-full min-h-0 overflow-y-scroll p-4 sm:p-5">
        <div className="mx-auto grid max-w-[1480px] gap-5">
          <PageTitle icon="book" title="Knowledge" subtitle={`${notes.length} notes · ${sources.length} sources`} />

          <div className="grid gap-5">
            <Panel title={`Notes (${notes.length})`} icon="book">
        {notes.length === 0 ? (
          <EmptyState
            icon="book"
            title="No notes yet"
            body="Agents document learnings after evaluations. Notes appear here as agents discover patterns, identify failure modes, and refine their strategies."
          />
        ) : (
          <div className="space-y-4">
            {groupedNotes.map(([category, catNotes]) => (
              <div key={category}>
                <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-2">
                  {CATEGORY_LABELS[category] || category} ({catNotes.length})
                </p>
                <div className="overflow-hidden rounded-xl border border-border bg-surface/85">
                  {[...catNotes].reverse().map((note) => (
                    <div key={note.index} className="border-b border-border last:border-b-0">
                      <button
                        onClick={() =>
                          setExpandedNote(
                            expandedNote === note.index ? null : note.index
                          )
                        }
                        className="w-full text-left py-3.5 px-4 hover:bg-muted/50 transition-colors duration-100 flex items-start gap-3"
                      >
                        <div className="mt-1 shrink-0">
                          <div className="h-2.5 w-2.5 rounded-full border-2 border-accent bg-accent-soft" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="font-mono text-[10px] text-muted-fg mb-0.5">
                            {note.date}
                            {note.relative_path && (
                              <span className="ml-2 opacity-60">{note.relative_path}</span>
                            )}
                          </p>
                          <p className="font-display text-[14px] font-semibold leading-snug">
                            {note.title}
                          </p>
                        </div>
                        <span className="font-mono text-xs text-muted-fg shrink-0">
                          {expandedNote === note.index ? "−" : "+"}
                        </span>
                      </button>

                      {expandedNote === note.index && (
                        <div className="pb-4 pl-10 pr-4">
                          <div className="border-l-2 border-border pl-4">
                            <div className="font-body text-[13px] leading-relaxed whitespace-pre-wrap text-muted-fg">
                              {note.body}
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
            </Panel>

            <div className="grid content-start gap-5">
        <ReviewPanel review={review} />

        <EvalSpecPanel
          evalSpec={evalSpec}
          draft={evalSpecDraft}
          saving={saving === "eval-spec"}
          onChange={setEvalSpecDraft}
          onSave={saveEvalSpec}
        />

        <Panel title="Capture" icon="file">
          <div className="grid gap-3">
            <div className="rounded-xl border border-border bg-surface/75 p-4">
              <div className="grid gap-2">
                <div className="grid grid-cols-[1fr_130px] gap-2">
                  <input
                    value={reviewTitle}
                    onChange={(event) => setReviewTitle(event.target.value)}
                    placeholder="Review note title"
                    className={inputClass}
                  />
                  <select
                    value={reviewCategory}
                    onChange={(event) => setReviewCategory(event.target.value)}
                    className={`${inputClass} font-mono text-[11px]`}
                  >
                    <option value="synthesis">Synthesis</option>
                    <option value="experiments">Experiments</option>
                    <option value="open-questions">Questions</option>
                    <option value="research">Research</option>
                  </select>
                </div>
                <textarea
                  value={reviewBody}
                  onChange={(event) => setReviewBody(event.target.value)}
                  placeholder="Finding, risk, eval concern, or next-run instruction"
                  rows={4}
                  className={`${inputClass} resize-y leading-relaxed`}
                />
                <button
                  onClick={saveReviewNote}
                  disabled={saving !== null || !reviewTitle.trim() || !reviewBody.trim()}
                  className="justify-self-end rounded-md bg-accent px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-white transition-colors hover:bg-accent-fg disabled:opacity-40"
                >
                  {saving === "note" ? "Saving" : "Save note"}
                </button>
              </div>
            </div>

            <div className="rounded-xl border border-border bg-surface/75 p-4">
              <div className="grid gap-2">
                <div className="grid grid-cols-[1fr_130px] gap-2">
                  <input
                    value={sourceTitle}
                    onChange={(event) => setSourceTitle(event.target.value)}
                    placeholder="Reference title"
                    className={inputClass}
                  />
                  <select
                    value={sourceCategory}
                    onChange={(event) => setSourceCategory(event.target.value)}
                    className={`${inputClass} font-mono text-[11px]`}
                  >
                    <option value="papers">Papers</option>
                    <option value="repos">Repos</option>
                    <option value="web">Web</option>
                    <option value="docs">Docs</option>
                    <option value="datasets">Datasets</option>
                  </select>
                </div>
                <input
                  value={sourceUrl}
                  onChange={(event) => setSourceUrl(event.target.value)}
                  placeholder="URL or local reference"
                  className={inputClass}
                />
                <textarea
                  value={sourceNote}
                  onChange={(event) => setSourceNote(event.target.value)}
                  placeholder="Why this source matters"
                  rows={2}
                  className={`${inputClass} resize-y leading-relaxed`}
                />
                <button
                  onClick={saveSource}
                  disabled={
                    saving !== null ||
                    !sourceTitle.trim() ||
                    (!sourceUrl.trim() && !sourceNote.trim())
                  }
                  className="justify-self-end rounded-md bg-accent px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-white transition-colors hover:bg-accent-fg disabled:opacity-40"
                >
                  {saving === "source" ? "Saving" : "Save reference"}
                </button>
              </div>
            </div>
            {notice && (
              <p className="rounded-lg border border-border bg-muted/40 px-3 py-2 font-mono text-[11px] text-muted-fg">
                {notice}
              </p>
            )}
          </div>
        </Panel>

        <Panel title={`Sources (${sources.length})`} icon="network">

          {sources.length === 0 ? (
            <EmptyState icon="network" title="No sources yet" />
          ) : (
            <div className="space-y-4">
              {groupedSources.map(([category, entries]) => (
                <div key={category}>
                  <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-2">
                    {CATEGORY_LABELS[category] || category} ({entries.length})
                  </p>
                  <div className="overflow-hidden rounded-xl border border-border bg-surface/85">
                    {entries.map((source, index) => (
                      <SourceRow
                        key={`${source.island_id ?? "public"}:${source.relative_path ?? source.title}:${index}`}
                        source={source}
                        busy={saving === "source-status"}
                        onStatusChange={updateSourceStatus}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title={`Skills (${skills.length})`} icon="spark">

        {skills.length === 0 ? (
          <EmptyState
            icon="spark"
            title="No skills yet"
            body="Agents package reusable tools and techniques as skills. Skills appear here as agents build solutions that can be shared across the team."
          />
        ) : (
          <div className="space-y-3">
            {skills.map((skill) => (
              <div
                key={skill.name}
                className="rounded-lg border border-border bg-surface/85 p-4 transition-colors duration-100 hover:bg-surface"
              >
                <p className="font-display text-[14px] font-semibold mb-1">
                  {skill.name}
                </p>
                {skill.description && (
                  <p className="font-body text-[13px] text-muted-fg mb-2">
                    {skill.description}
                  </p>
                )}
                <div className="font-mono text-[10px] text-muted-fg flex gap-3">
                  <span>By: {skill.creator}</span>
                  {skill.created && (
                    <span>{String(skill.created).slice(0, 10)}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
        </Panel>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function ReviewPanel({ review }: { review: ReviewSummary | null }) {
  if (!review) {
    return (
      <div>
        <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-3">
          Review
        </p>
        <div className="border border-border rounded-xl p-4">
          <p className="font-mono text-[11px] text-muted-fg">Loading review summary...</p>
        </div>
      </div>
    );
  }

  const best = review.attempts.best;
  const baseline = review.attempts.best_baseline;
  const usage = review.usage && "total_tokens" in review.usage ? review.usage as { total_tokens?: number; total_cost_usd?: number } : null;

  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg">
          Review
        </p>
        <span className="rounded-md border border-border px-2 py-1 font-mono text-[9px] uppercase tracking-widest text-muted-fg">
          {review.task.eval_version || "eval"} / {review.task.eval_profile || "profile"}
        </span>
      </div>

      <div className="border border-border rounded-xl p-4">
        <div className="grid grid-cols-4 gap-3">
          <ReviewMetric label="Best" value={best?.score == null ? "---" : formatScore(best.score)} />
          <ReviewMetric label="Baseline" value={baseline?.score == null ? "---" : formatScore(baseline.score)} />
          <ReviewMetric label="Attempts" value={`${review.attempts.scored}/${review.attempts.total}`} />
          <ReviewMetric label="Sources" value={String(review.knowledge.sources)} />
        </div>

        <div className="mt-4 grid grid-cols-3 gap-3">
          <ReviewMetric label="Pending" value={String(review.attempts.pending)} />
          <ReviewMetric label="Failed" value={String(review.attempts.crashed + review.attempts.timeout + review.attempts.grader_error)} />
          <ReviewMetric label="Notes" value={String(review.knowledge.notes)} />
        </div>

        <div className="mt-4 flex flex-wrap gap-2 font-mono text-[10px] text-muted-fg">
          <span>{review.run_state?.status ? String(review.run_state.status) : "stopped"}</span>
          {review.attempts.improvement_over_baseline != null && (
            <span>delta {formatSigned(review.attempts.improvement_over_baseline)}</span>
          )}
          {usage && <span>{formatTokens(usage.total_tokens || 0)} tok</span>}
          {usage && <span>{formatCost(usage.total_cost_usd || 0)}</span>}
        </div>

        {best && (
          <div className="mt-4 border-t border-border pt-3">
            <p className="mb-1 font-mono text-[10px] uppercase tracking-widest text-muted-fg">
              Best Attempt
            </p>
            <p className="truncate font-display text-[14px] font-semibold">{best.title}</p>
            <p className="mt-0.5 font-mono text-[10px] text-muted-fg">
              {best.agent_id} · {best.commit_hash.slice(0, 8)}
            </p>
          </div>
        )}

        {review.flags.length > 0 && (
          <div className="mt-4 border-t border-border pt-3">
            <p className="mb-2 font-mono text-[10px] uppercase tracking-widest text-muted-fg">
              Flags
            </p>
            <div className="space-y-2">
              {review.flags.slice(0, 4).map((flag) => (
                <div key={`${flag.severity}:${flag.label}`} className="rounded-lg border border-border bg-muted/30 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span className={`h-2 w-2 rounded-full ${flagColor(flag.severity)}`} />
                    <p className="font-display text-[13px] font-semibold">{flag.label}</p>
                  </div>
                  <p className="mt-1 font-body text-[12px] leading-relaxed text-muted-fg">{flag.detail}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-4 border-t border-border pt-3">
          <p className="mb-2 font-mono text-[10px] uppercase tracking-widest text-muted-fg">
            Next Actions
          </p>
          <div className="space-y-1.5">
            {review.recommended_actions.slice(0, 4).map((action) => (
              <p key={action} className="font-body text-[12px] leading-relaxed text-muted-fg">
                {action}
              </p>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function ReviewMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-border px-3 py-2">
      <p className="font-mono text-[9px] uppercase tracking-widest text-muted-fg">{label}</p>
      <p className="mt-1 truncate font-mono text-[14px] text-foreground">{value}</p>
    </div>
  );
}

function EvalSpecPanel({
  evalSpec,
  draft,
  saving,
  onChange,
  onSave,
}: {
  evalSpec: EvalSpecResponse | null;
  draft: string;
  saving: boolean;
  onChange: (value: string) => void;
  onSave: () => void;
}) {
  const dirty = evalSpec ? draft !== (evalSpec.content || "") : false;
  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg">
          Eval Spec
        </p>
        <span className="rounded-md border border-border px-2 py-1 font-mono text-[9px] uppercase tracking-widest text-muted-fg">
          {evalSpec?.exists ? "tracked" : "missing"}
        </span>
      </div>
      <div className="border border-border rounded-xl p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <p className="min-w-0 truncate font-mono text-[10px] text-muted-fg">
            {evalSpec?.path || "knowledge/eval_spec.md"}
          </p>
          {evalSpec?.updated_at && (
            <span className="shrink-0 font-mono text-[10px] text-muted-fg">
              {String(evalSpec.updated_at).slice(0, 19)}
            </span>
          )}
        </div>
        <textarea
          value={draft}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Breakthrough metrics, guardrail metrics, anti-cheating checks, scalar score formula, and eval profile purpose"
          rows={11}
          className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 font-mono text-[12px] leading-relaxed outline-none"
        />
        <div className="mt-3 flex justify-end">
          <button
            onClick={onSave}
            disabled={saving || !dirty}
            className="rounded-md bg-accent px-3 py-2 font-mono text-[10px] uppercase tracking-widest text-white transition-colors hover:bg-accent-fg disabled:opacity-40"
          >
            {saving ? "Saving" : "Save eval spec"}
          </button>
        </div>
      </div>
    </div>
  );
}

function SourceRow({
  source,
  busy,
  onStatusChange,
}: {
  source: KnowledgeSource;
  busy: boolean;
  onStatusChange: (
    source: KnowledgeSource,
    status: "accepted" | "rejected" | "archived" | "proposed",
  ) => void;
}) {
  const url = typeof source.origin_url === "string" ? source.origin_url : typeof source.url === "string" ? source.url : "";
  const status = typeof source.status === "string" ? source.status : "";
  const reviewedBy = typeof source.reviewed_by === "string" ? source.reviewed_by : "";
  const canReview = source.source === "manifest" && !source.island_id && sourceSelector(source) !== null;
  return (
    <div className="border-b border-border last:border-b-0 px-4 py-3">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate font-display text-[14px] font-semibold">
            {source.title || source.relative_path || "source"}
          </p>
          {source.relative_path && (
            <p className="mt-0.5 truncate font-mono text-[10px] text-muted-fg">
              {source.relative_path}
            </p>
          )}
        </div>
        <span className="shrink-0 rounded-md border border-border px-2 py-1 font-mono text-[9px] uppercase tracking-widest text-muted-fg">
          {source.source || "source"}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3 font-mono text-[10px] text-muted-fg">
        {source.status && <span>{source.status}</span>}
        {source.added_by && <span>by {source.added_by}</span>}
        {source.added_at && <span>{String(source.added_at).slice(0, 10)}</span>}
        {reviewedBy && <span>reviewed by {reviewedBy}</span>}
        {source.island_id && <span>island {source.island_id}</span>}
        {source.size_bytes != null && <span>{formatBytes(source.size_bytes)}</span>}
      </div>
      {url && (
        <p className="mt-1 truncate font-mono text-[10px] text-muted-fg">
          {url}
        </p>
      )}
      {canReview && (
        <div className="mt-3 flex flex-wrap gap-2">
          {status !== "accepted" && (
            <button
              onClick={() => onStatusChange(source, "accepted")}
              disabled={busy}
              className="rounded-md border border-border px-2.5 py-1 font-mono text-[9px] uppercase tracking-widest disabled:opacity-40"
            >
              Accept
            </button>
          )}
          {status !== "rejected" && (
            <button
              onClick={() => onStatusChange(source, "rejected")}
              disabled={busy}
              className="rounded-md border border-border px-2.5 py-1 font-mono text-[9px] uppercase tracking-widest disabled:opacity-40"
            >
              Reject
            </button>
          )}
          {status !== "archived" && (
            <button
              onClick={() => onStatusChange(source, "archived")}
              disabled={busy}
              className="rounded-md border border-border px-2.5 py-1 font-mono text-[9px] uppercase tracking-widest disabled:opacity-40"
            >
              Archive
            </button>
          )}
          {status && status !== "proposed" && (
            <button
              onClick={() => onStatusChange(source, "proposed")}
              disabled={busy}
              className="rounded-md border border-border px-2.5 py-1 font-mono text-[9px] uppercase tracking-widest disabled:opacity-40"
            >
              Reopen
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function sourceSelector(source: KnowledgeSource): {
  id?: string;
  relative_path?: string;
  title?: string;
  origin_url?: string;
  url?: string;
} | null {
  const selector: {
    id?: string;
    relative_path?: string;
    title?: string;
    origin_url?: string;
    url?: string;
  } = {};
  if (typeof source.id === "string" && source.id) selector.id = source.id;
  if (typeof source.relative_path === "string" && source.relative_path) selector.relative_path = source.relative_path;
  if (typeof source.title === "string" && source.title) selector.title = source.title;
  if (typeof source.origin_url === "string" && source.origin_url) selector.origin_url = source.origin_url;
  if (typeof source.url === "string" && source.url) selector.url = source.url;
  return Object.keys(selector).length > 0 ? selector : null;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatScore(score: number): string {
  return Number.isFinite(score) ? score.toPrecision(5) : String(score);
}

function formatSigned(value: number): string {
  const formatted = Math.abs(value) < 0.0001 ? value.toExponential(2) : value.toPrecision(4);
  return value > 0 ? `+${formatted}` : formatted;
}

function formatTokens(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`;
  return String(tokens);
}

function formatCost(cost: number): string {
  if (!cost) return "$0";
  return `$${cost.toFixed(cost < 1 ? 4 : 2)}`;
}

function flagColor(severity: string): string {
  if (severity === "high") return "bg-danger";
  if (severity === "medium") return "bg-warning";
  return "bg-muted-fg";
}
