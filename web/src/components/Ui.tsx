import type { ReactNode } from "react";

export type IconName =
  | "activity"
  | "book"
  | "chart"
  | "check"
  | "chevron"
  | "clock"
  | "control"
  | "file"
  | "logs"
  | "network"
  | "spark"
  | "users";

export const panelClass =
  "rounded-xl border border-border bg-surface/85 shadow-[0_1px_0_rgba(23,32,29,0.04),0_14px_34px_rgba(23,32,29,0.045)]";

export const inputClass =
  "rounded-md border border-border bg-surface/90 px-3 py-2 text-[13px] outline-none transition-colors placeholder:text-muted-fg/60 hover:border-border-strong focus:border-accent disabled:cursor-not-allowed disabled:bg-muted/40 disabled:text-muted-fg";

export function Icon({ name, className = "h-4 w-4" }: { name: IconName; className?: string }) {
  const paths: Record<IconName, ReactNode> = {
    activity: (
      <>
        <path d="M22 12h-4l-3 8-6-16-3 8H2" />
      </>
    ),
    book: (
      <>
        <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
        <path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5Z" />
      </>
    ),
    chart: (
      <>
        <path d="M3 3v18h18" />
        <path d="m7 15 4-4 3 3 5-7" />
      </>
    ),
    check: <path d="M20 6 9 17l-5-5" />,
    chevron: <path d="m9 18 6-6-6-6" />,
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    control: (
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
    file: (
      <>
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />
        <path d="M14 2v6h6" />
      </>
    ),
    logs: (
      <>
        <path d="M4 6h16" />
        <path d="M4 12h16" />
        <path d="M4 18h10" />
      </>
    ),
    network: (
      <>
        <circle cx="12" cy="12" r="3" />
        <circle cx="5" cy="5" r="2" />
        <circle cx="19" cy="5" r="2" />
        <circle cx="5" cy="19" r="2" />
        <circle cx="19" cy="19" r="2" />
        <path d="m7 7 3 3" />
        <path d="m17 7-3 3" />
        <path d="m7 17 3-3" />
        <path d="m17 17-3-3" />
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

export function IconBadge({
  name,
  tone = "neutral",
}: {
  name: IconName;
  tone?: "neutral" | "good" | "warn" | "danger";
}) {
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

export function Panel({
  title,
  icon,
  children,
  action,
}: {
  title?: string;
  icon?: IconName;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className={`${panelClass} p-4`}>
      {(title || action) && (
        <div className="mb-4 flex items-center justify-between gap-3 border-b border-border/70 pb-3">
          <div className="flex min-w-0 items-center gap-2">
            {icon && <IconBadge name={icon} />}
            {title && <h2 className="font-mono text-[10px] uppercase tracking-wider text-muted-fg">{title}</h2>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function PageTitle({
  icon,
  title,
  subtitle,
}: {
  icon: IconName;
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="mb-5 flex items-start gap-3">
      <IconBadge name={icon} />
      <div className="min-w-0">
        <h1 className="font-display text-2xl">{title}</h1>
        {subtitle && <p className="mt-1 font-mono text-[11px] text-muted-fg">{subtitle}</p>}
      </div>
    </div>
  );
}

export function EmptyState({ title, body, icon = "spark" }: { title: string; body?: string; icon?: IconName }) {
  return (
    <div className={`${panelClass} p-5`}>
      <div className="flex gap-3">
        <IconBadge name={icon} />
        <div>
          <p className="font-display text-[15px] font-semibold">{title}</p>
          {body && <p className="mt-1 font-body text-[12px] leading-relaxed text-muted-fg">{body}</p>}
        </div>
      </div>
    </div>
  );
}
