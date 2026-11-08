import type { ReactNode } from "react";
import { cn } from "./ui";

export type BarRow = {
  label: string;
  value: number;
  display: string;
  barClass?: string;
  hatched?: boolean;
  badge?: ReactNode;
};

/** Horizontal bars. Every bar also shows its number, so no information is colour-only. */
export function HBars({ rows, max }: { rows: BarRow[]; max: number }) {
  const denominator = Math.max(max, 1);
  return (
    <ul className="space-y-4">
      {rows.map((row) => (
        <li key={row.label}>
          <div className="mb-1.5 flex items-center justify-between gap-3 text-sm">
            <span className="flex items-center gap-2 text-fg-muted">{row.label}{row.badge}</span>
            <span className="font-medium tabular-nums text-fg">{row.display}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className={cn("h-full rounded-full", row.barClass ?? "bg-brand", row.hatched && "hatch")}
              style={{ width: `${Math.min(100, (row.value / denominator) * 100)}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

export type DonutSegment = { label: string; value: number; stroke: string; swatch: string };

export function Donut({ segments, centerValue, centerLabel }: { segments: DonutSegment[]; centerValue: string; centerLabel: string }) {
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  return (
    <div className="flex flex-wrap items-center gap-6">
      <div className="relative size-40 shrink-0">
        <svg
          viewBox="0 0 140 140"
          className="size-full -rotate-90"
          role="img"
          aria-label={segments.map((s) => `${s.label}: ${s.value}`).join(", ")}
        >
          <circle cx="70" cy="70" r={radius} fill="none" strokeWidth="14" className="stroke-white/10" />
          {total > 0
            ? segments.map((segment) => {
                const length = (segment.value / total) * circumference;
                const circle = (
                  <circle
                    key={segment.label}
                    cx="70" cy="70" r={radius} fill="none" strokeWidth="14"
                    className={segment.stroke}
                    strokeDasharray={`${length} ${circumference - length}`}
                    strokeDashoffset={-offset}
                  />
                );
                offset += length;
                return circle;
              })
            : null}
        </svg>
        <div className="absolute inset-0 grid place-content-center text-center">
          <span className="text-2xl font-semibold tabular-nums">{centerValue}</span>
          <span className="text-xs text-fg-dim">{centerLabel}</span>
        </div>
      </div>
      <ul className="space-y-2 text-sm">
        {segments.map((segment) => (
          <li key={segment.label} className="flex items-center gap-2">
            <span aria-hidden className={cn("size-2.5 rounded-full", segment.swatch)} />
            <span className="text-fg-muted">{segment.label}</span>
            <span className="ml-auto pl-4 font-medium tabular-nums">{segment.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}