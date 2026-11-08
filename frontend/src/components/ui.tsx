"use client";

import { Info, RefreshCw, X } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { ApiError } from "@/lib/api";

export function cn(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

/* ---------- Badge ---------- */
type Tone = "neutral" | "brand" | "success" | "warning" | "danger" | "info";
const TONE: Record<Tone, string> = {
  neutral: "bg-white/5 text-fg-muted ring-white/10",
  brand: "bg-brand/10 text-brand ring-brand/30",
  success: "bg-ok/10 text-ok ring-ok/25",
  warning: "bg-warn/10 text-warn ring-warn/25",
  danger: "bg-bad/10 text-bad ring-bad/25",
  info: "bg-info/10 text-info ring-info/25",
};

export function Badge({
  tone = "neutral", dot = false, children, title, className,
}: { tone?: Tone; dot?: boolean; children: ReactNode; title?: string; className?: string }) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        TONE[tone], className,
      )}
    >
      {dot ? <span aria-hidden className="size-1.5 rounded-full bg-current shadow-[0_0_8px_currentColor]" /> : null}
      {children}
    </span>
  );
}

export function PriorityBadge({ priority }: { priority?: string | null }) {
  if (!priority) return <Badge>Not analyzed</Badge>;
  const tone: Tone = priority === "High" ? "brand" : priority === "Medium" ? "warning" : "neutral";
  return <Badge tone={tone} dot>{priority}</Badge>;
}

export function ScoreMeter({ score }: { score: number | null | undefined }) {
  if (score == null) return <span className="text-fg-dim">—</span>;
  const color = score >= 65 ? "bg-brand" : score >= 40 ? "bg-warn" : "bg-fg-dim";
  return (
    <div className="flex items-center gap-2">
      <span className="w-10 text-right font-medium tabular-nums">{score.toFixed(1)}</span>
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-white/10">
        <span className={cn("block h-full rounded-full", color)} style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />
      </span>
    </div>
  );
}

/* ---------- Button ---------- */
type Variant = "primary" | "secondary" | "ghost" | "danger";
const VARIANT: Record<Variant, string> = {
  primary:
  "bg-[linear-gradient(180deg,#5b8cff,#3d5af1)] text-white shadow-[0_0_0_1px_rgba(122,162,255,0.5),0_6px_22px_-6px_rgba(61,107,245,0.8)] hover:brightness-110",
  secondary: "bg-white/5 text-fg ring-1 ring-inset ring-line-strong hover:bg-white/10",
  ghost: "text-fg-muted hover:bg-white/5 hover:text-fg",
  danger: "bg-bad/10 text-bad ring-1 ring-inset ring-bad/30 hover:bg-bad/20",
};

export function Button({
  variant = "secondary", size = "md", loading = false, className, children, disabled, ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; loading?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-all duration-150 disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "px-3 py-1.5 text-xs" : "px-4 py-2 text-sm",
        VARIANT[variant], className,
      )}
    >
      {loading ? <RefreshCw className="size-3.5 animate-spin" aria-hidden /> : null}
      {children}
    </button>
  );
}

/* ---------- Layout pieces ---------- */
export function Card({ className, children, glow = false }: { className?: string; children: ReactNode; glow?: boolean }) {
  return <div className={cn("card", glow && "card-glow", className)}>{children}</div>;
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description ? <p className="mt-1 text-sm text-fg-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-3">{actions}</div> : null}
    </div>
  );
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h2 className="text-base font-semibold tracking-tight">{children}</h2>
      {aside}
    </div>
  );
}

export function StatCard({
  label, value, hint, icon, chip, unavailable = false,
}: { label: string; value: string; hint?: string; icon: ReactNode; chip?: ReactNode; unavailable?: boolean }) {
  return (
    <Card glow className={cn("p-5", unavailable && "border-dashed")}>
      <div className="flex items-start justify-between gap-2">
        <span className="grid size-9 place-items-center rounded-lg bg-brand/10 text-brand ring-1 ring-brand/20">{icon}</span>
        {chip}
      </div>
      <h3 className="mt-4 text-sm font-normal text-fg-muted">{label}</h3>
      <p className={cn("mt-1 font-semibold tracking-tight tabular-nums", unavailable ? "text-xl text-fg-dim" : "text-3xl")}>{value}</p>
      {hint ? <p className="mt-2 text-xs leading-relaxed text-fg-dim">{hint}</p> : null}
    </Card>
  );
}

/* ---------- States ---------- */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-xl bg-white/5", className)} />;
}

export function LoadingGrid({ cards = 6 }: { cards?: number }) {
  return (
    <div aria-busy="true">
      <p className="sr-only" role="status">Loading data…</p>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: cards }, (_, i) => <Skeleton key={i} className="h-36" />)}
      </div>
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <Card className="grid justify-items-start gap-3 p-8">
      <h2 className="text-lg font-semibold">{title}</h2>
      <div className="text-sm text-fg-muted">{children}</div>
      {action}
    </Card>
  );
}

export function Notice({
  tone = "info", children, onClose,
}: { tone?: "info" | "success" | "danger"; children: ReactNode; onClose?: () => void }) {
  const styles = { info: "border-info/30 bg-info/10 text-info", success: "border-ok/30 bg-ok/10 text-ok", danger: "border-bad/30 bg-bad/10 text-bad" }[tone];
  return (
    <div role={tone === "danger" ? "alert" : "status"} className={cn("flex items-start gap-3 rounded-xl border px-4 py-3 text-sm", styles)}>
      <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1 text-fg">{children}</div>
      {onClose ? (
        <button type="button" onClick={onClose} aria-label="Dismiss" className="text-fg-muted hover:text-fg">
          <X className="size-4" />
        </button>
      ) : null}
    </div>
  );
}

function titleFor(error: ApiError): string {
  if (error.code === "network_error" || error.code === "backend_unreachable") return "Cannot connect to the backend";
  if (error.code === "invalid_response") return "Unexpected response from the backend";
  return "Something went wrong";
}

function hintFor(error: ApiError): string | null {
  switch (error.code) {
    case "network_error":
    case "backend_unreachable":
      return "Check that the FastAPI server is running and that BACKEND_URL in .env.local is correct.";
    case "backend_not_configured":
      return "Create frontend/.env.local with BACKEND_URL and restart the dev server.";
    case "unauthorized":
      return "The backend requires an API key. Set BACKEND_API_KEY in .env.local and restart the dev server.";
    case "database_unavailable":
    case "database_not_configured":
    case "database_error":
      return "The backend cannot use PostgreSQL. Check that it is running, then run: python database.py init";
    case "invalid_response":
      return "The dashboard and the backend disagree on the response format. Restart the backend after editing main.py.";
    default:
      return null;
  }
}

export function ErrorBanner({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const hint = hintFor(error);
  return (
    <div role="alert" className="flex items-start justify-between gap-4 rounded-xl border border-bad/30 bg-bad/10 p-4">
      <div className="min-w-0 text-sm">
        <p className="font-semibold text-bad">{titleFor(error)}</p>
        <p className="mt-1 text-fg">{error.message}</p>
        {hint ? <p className="mt-1 text-fg-muted">{hint}</p> : null}
        <p className="mt-2 text-xs text-fg-dim">
          Error code: {error.code}{error.status ? ` · HTTP ${error.status}` : ""}
        </p>
      </div>
      {onRetry ? <Button size="sm" onClick={onRetry}>Try again</Button> : null}
    </div>
  );
}