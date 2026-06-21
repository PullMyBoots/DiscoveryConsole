const styles: Record<string, string> = {
  improved:
    "border border-success/30 bg-success-soft text-success",
  baseline:
    "bg-surface-muted text-muted-fg border border-border",
  regressed:
    "border border-warning/40 bg-warning-soft text-warning",
  crashed:
    "border border-danger/40 bg-danger-soft text-danger line-through",
  timeout:
    "border border-danger/40 bg-danger-soft text-danger italic",
  active:
    "border border-success/30 bg-success-soft text-success",
  evaluating:
    "border border-accent/30 bg-accent-soft text-accent-fg",
  heartbeat:
    "border border-info/35 bg-info-soft text-info",
  waiting:
    "bg-surface-muted text-muted-fg border border-border",
  idle:
    "bg-surface-muted text-muted-fg border border-border",
  paused:
    "border border-warning/35 bg-warning-soft text-warning",
  stopped:
    "border border-border text-muted-fg",
};

const labels: Record<string, string> = {
  active: "running",
  idle: "running",
  waiting: "waiting",
  evaluating: "evaluating",
  heartbeat: "heartbeat",
  paused: "paused",
  stopped: "stopped",
};

export default function StatusBadge({ status }: { status: string }) {
  const cls = styles[status] || styles.baseline;
  return (
    <span
      className={`inline-block px-2.5 py-0.5 text-[9px] font-mono font-medium uppercase tracking-widest rounded-full ${cls}`}
    >
      {labels[status] || status}
    </span>
  );
}
