import type { Attempt } from "../lib/api";
import { scoreComponents, scoreLabel, scoreValue } from "../lib/scores";
import StatusBadge from "./StatusBadge";

interface Props {
  attempt: Attempt;
  rank: number;
  expanded: boolean;
  onToggle: () => void;
  highlight?: boolean;
  metric?: string;
}

export default function AttemptRow({
  attempt: a,
  rank,
  expanded,
  onToggle,
  highlight,
  metric = "score",
}: Props) {
  const displayScore = scoreValue(a, metric);
  const components = scoreComponents(a);
  const componentEntries = Object.entries(components);
  return (
    <>
      <tr
        onClick={onToggle}
        className={`border-b border-border cursor-pointer transition-colors duration-100 ${
          expanded ? "bg-muted" : highlight ? "bg-muted/80" : "hover:bg-muted/50"
        }`}
      >
        <td className="py-2.5 px-3 font-mono text-xs text-muted-fg">{rank}</td>
        <td className="py-2.5 px-3 font-mono text-[13px] font-medium">
          {displayScore != null ? String(displayScore) : "---"}
        </td>
        <td className="py-2.5 px-3 font-mono text-xs">{a.agent_id}</td>
        <td className="py-2.5 px-3">
          <StatusBadge status={a.status} />
        </td>
        <td className="py-2.5 px-3 font-mono text-xs text-muted-fg whitespace-nowrap">
          {formatTime(a.timestamp)}
        </td>
      </tr>

      {expanded && (
        <tr>
          <td colSpan={5} className="bg-muted border-b border-border">
            <div className="px-6 py-4">
              {/* Title */}
              {a.title && (
                <p className="font-display text-[14px] font-semibold mb-3">
                  {a.title}
                </p>
              )}

              {/* Metadata grid */}
              <div className="grid grid-cols-2 gap-x-8 gap-y-3 mb-4">
                <Field label={scoreLabel(metric)}>
                  <span className="font-display text-lg font-bold">
                    {displayScore != null ? String(displayScore) : "---"}
                  </span>
                </Field>
                {metric !== "score" && (
                  <Field label="Total score">
                    <span className="font-mono text-[13px]">
                      {a.score != null ? String(a.score) : "---"}
                    </span>
                  </Field>
                )}
                <Field label="Agent">
                  <span className="font-mono text-[13px]">{a.agent_id}</span>
                </Field>
                <Field label="Timestamp">
                  <span className="font-mono text-[13px]">
                    {new Date(a.timestamp).toLocaleString()}
                  </span>
                </Field>
                <Field label="Status">
                  <StatusBadge status={a.status} />
                </Field>
                <Field label="Commit">
                  <span className="font-mono text-[13px]">{a.commit_hash}</span>
                </Field>
                <Field label="Parent">
                  <span className="font-mono text-[13px] text-muted-fg">
                    {a.parent_hash ? a.parent_hash.slice(0, 12) + "..." : "---"}
                  </span>
                </Field>
              </div>

              {componentEntries.length > 0 && (
                <div className="mb-4">
                  <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-2">
                    Score components
                  </p>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {componentEntries.map(([name, component]) => (
                      <div key={name} className="rounded-lg border border-border bg-background p-3">
                        <div className="mb-1 flex items-center justify-between gap-3">
                          <span className="font-mono text-[11px] font-medium">{name}</span>
                          <span className="font-mono text-[12px]">
                            {typeof component.value === "number" ? String(component.value) : "---"}
                          </span>
                        </div>
                        {component.explanation && (
                          <p className="font-body text-[11px] leading-relaxed text-muted-fg">
                            {component.explanation}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Feedback */}
              {a.feedback && (
                <div>
                  <p className="font-mono text-[10px] tracking-widest uppercase text-muted-fg mb-2">
                    Feedback
                  </p>
                  <div className="border border-border rounded-lg p-4 bg-background">
                    <pre className="font-mono text-xs whitespace-pre-wrap leading-relaxed">
                      {a.feedback}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="font-mono text-[10px] text-muted-fg tracking-widest uppercase mb-0.5">
        {label}
      </p>
      {children}
    </div>
  );
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso.slice(0, 16);
  }
}
