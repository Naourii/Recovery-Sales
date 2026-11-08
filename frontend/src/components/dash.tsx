"use client";

import { ArrowUpRight, ChevronRight, Sparkles } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { avatarGradient, initials } from "@/lib/dash-data";
import { Card, cn } from "./ui";

export function IconCircle({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span aria-hidden className={cn("grid size-9 shrink-0 place-items-center rounded-full border border-line-strong bg-white/5 text-brand", className)}>
      {children}
    </span>
  );
}

export function KpiCard({
  title, icon, value, chip, sub, insight, unavailable = false,
}: { title: string; icon: ReactNode; value: string; chip?: ReactNode; sub: string; insight: string; unavailable?: boolean }) {
  return (
    <Card glow className={cn("flex flex-col p-5", unavailable && "border-dashed")}>
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-sm font-medium">{title}</h3>
        <IconCircle>{icon}</IconCircle>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <p className={cn("font-semibold tracking-tight tabular-nums", unavailable ? "py-1 text-2xl text-fg-dim" : "text-4xl")}>{value}</p>
        {chip}
      </div>
      <p className="mt-2 text-xs text-fg-dim">{sub}</p>
      <div className="mt-auto pt-4">
        <div className="flex items-start gap-2 border-t border-line pt-3 text-xs">
          <Sparkles className="mt-0.5 size-3.5 shrink-0 text-brand" aria-hidden />
          <p className="leading-relaxed text-fg-muted"><span className="text-fg-dim">Insight: </span>{insight}</p>
        </div>
      </div>
    </Card>
  );
}

export function Panel({
  title, subtitle, href, hrefLabel = "View all", edge = false, className, children,
}: { title: string; subtitle?: string; href?: string; hrefLabel?: string; edge?: boolean; className?: string; children: ReactNode }) {
  return (
    <Card glow className={cn("flex h-full flex-col p-5", edge && "card-edge", className)}>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold tracking-tight">{title}</h2>
          {subtitle ? <p className="mt-0.5 text-xs text-fg-dim">{subtitle}</p> : null}
        </div>
        {href ? (
          <Link href={href} className="inline-flex shrink-0 items-center gap-1 rounded-full border border-line-strong bg-white/5 px-3 py-1 text-xs text-fg-muted transition-colors hover:bg-white/10 hover:text-fg">
            {hrefLabel} <ChevronRight className="size-3" aria-hidden />
          </Link>
        ) : null}
      </div>
      <div className="flex min-h-0 flex-1 flex-col">{children}</div>
    </Card>
  );
}

export function InsetStat({
  icon, value, label, chip, href, hrefLabel, children,
}: { icon: ReactNode; value: string; label?: string; chip?: ReactNode; href?: string; hrefLabel?: string; children?: ReactNode }) {
  return (
    <div className="well p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <IconCircle>{icon}</IconCircle>
          <span className="text-3xl font-semibold tracking-tight tabular-nums">{value}</span>
          {chip}
          {label ? <span className="text-sm text-fg-muted">{label}</span> : null}
        </div>
        {href ? (
          <Link href={href} aria-label={hrefLabel} className="grid size-9 shrink-0 place-items-center rounded-full border border-line-strong bg-white/5 text-fg-muted transition-colors hover:bg-white/10 hover:text-fg">
            <ArrowUpRight className="size-4" aria-hidden />
          </Link>
        ) : null}
      </div>
      {children ? <div className="mt-4">{children}</div> : null}
    </div>
  );
}

export function Avatar({ name, className }: { name: string; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("grid size-9 shrink-0 place-items-center rounded-full text-xs font-semibold text-white ring-1 ring-white/20", className)}
      style={{ background: avatarGradient(name) }}
    >
      {initials(name)}
    </span>
  );
}

const STAGE_STYLES: Record<string, string> = {
  Negotiation: "bg-[linear-gradient(90deg,#2438c9,#3d6bf5)] text-white ring-brand/40",
  "Proposal Sent": "bg-brand/15 text-brand ring-brand/30",
  "Demo Completed": "bg-info/10 text-info ring-info/25",
  Qualified: "bg-white/5 text-fg-muted ring-white/10",
};

export function StagePill({ stage }: { stage?: string | null }) {
  if (!stage) return <span className="text-fg-dim">—</span>;
  return (
    <span className={cn("inline-flex min-w-[8.5rem] justify-center rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset", STAGE_STYLES[stage] ?? STAGE_STYLES.Qualified)}>
      {stage}
    </span>
  );
}