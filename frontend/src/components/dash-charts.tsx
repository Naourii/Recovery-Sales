"use client";

import { useId, useState, type MouseEvent } from "react";
import { niceScale, smoothPath, type ActivityDay, type Bucket } from "@/lib/dash-data";
import { formatCompact, formatCurrency, formatNumber } from "@/lib/format";

const W = 640;
const H = 250;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const GRID = "rgba(140,160,255,0.12)";

/* ================= Activity bars ================= */
export function ActivityBars({ data }: { data: ActivityDay[] }) {
  const uid = useId().replace(/:/g, "");
  const [hover, setHover] = useState<number | null>(null);

  const pad = { top: 14, right: 10, bottom: 30, left: 40 };
  const innerW = W - pad.left - pad.right;
  const innerH = H - pad.top - pad.bottom;
  const totals = data.map((d) => d.analyses + d.drafts);
  const scale = niceScale(Math.max(0, ...totals), 4, true);
  const slot = innerW / data.length;
  const barW = Math.min(40, slot * 0.62);
  const base = pad.top + innerH;
  const labelEvery = Math.ceil(data.length / 7);
  const sum = totals.reduce((a, b) => a + b, 0);
  const active = hover !== null ? data[hover] : null;
  const activeX = hover !== null ? pad.left + slot * hover + slot / 2 : 0;

  return (
    <div>
      <div className="relative">
        <svg
          viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img"
          aria-label={`Recovery activity for the last ${data.length} days: ${formatNumber(sum)} events in total.`}
          onMouseLeave={() => setHover(null)}
        >
          <defs>
            <linearGradient id={`${uid}-a`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#a78bfa" />
              <stop offset="1" stopColor="#3d6bf5" />
            </linearGradient>
            <linearGradient id={`${uid}-d`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#7ee0ff" />
              <stop offset="1" stopColor="#3a9fe0" />
            </linearGradient>
          </defs>

          {scale.values.map((v) => {
            const y = pad.top + innerH * (1 - v / scale.max);
            return (
              <g key={v}>
                <line x1={pad.left} x2={W - pad.right} y1={y} y2={y} stroke={GRID} strokeDasharray="3 5" />
                <text x={pad.left - 8} y={y + 4} textAnchor="end" fontSize="11" className="fill-fg-dim">{v}</text>
              </g>
            );
          })}

          {data.map((d, i) => {
            const cx = pad.left + slot * i + slot / 2;
            const x = cx - barW / 2;
            const aH = innerH * (d.analyses / scale.max);
            const dH = innerH * (d.drafts / scale.max);
            const dim = hover !== null && hover !== i;
            return (
              <g key={d.key} onMouseEnter={() => setHover(i)} opacity={dim ? 0.5 : 1}>
                <rect x={pad.left + slot * i} y={pad.top} width={slot} height={innerH} fill="transparent" />
                <rect x={x} y={pad.top} width={barW} height={innerH} rx={6} fill="rgba(255,255,255,0.035)" />
                {d.analyses > 0 ? <rect x={x} y={base - aH} width={barW} height={aH} rx={5} fill={`url(#${uid}-a)`} /> : null}
                {d.drafts > 0 ? <rect x={x} y={base - aH - dH} width={barW} height={dH} rx={5} fill={`url(#${uid}-d)`} /> : null}
                {i % labelEvery === 0 ? <text x={cx} y={H - 9} textAnchor="middle" fontSize="11" className="fill-fg-dim">{d.label}</text> : null}
              </g>
            );
          })}
        </svg>

        {sum === 0 ? (
          <div className="pointer-events-none absolute inset-0 grid place-items-center text-sm text-fg-dim">No activity in this window yet.</div>
        ) : null}

        {active ? (
          <div
            className="pointer-events-none absolute top-0 -translate-x-1/2 rounded-lg border border-line-strong bg-raised/95 px-3 py-2 text-xs shadow-lg"
            style={{ left: `${clamp((activeX / W) * 100, 16, 84)}%` }}
          >
            <p className="font-medium">{active.label}</p>
            <p className="mt-1 text-fg-muted">{active.analyses} analyses · {active.drafts} drafts</p>
          </div>
        ) : null}
      </div>

      <ul className="mt-3 flex gap-5 text-xs text-fg-muted">
        <li className="flex items-center gap-2"><span aria-hidden className="size-2.5 rounded-full bg-violet" />Analyses</li>
        <li className="flex items-center gap-2"><span aria-hidden className="size-2.5 rounded-full bg-info" />Drafts</li>
      </ul>

      <table className="sr-only">
        <caption>Recovery activity per day</caption>
        <thead><tr><th scope="col">Day</th><th scope="col">Analyses</th><th scope="col">Drafts</th></tr></thead>
        <tbody>{data.map((d) => <tr key={d.key}><th scope="row">{d.label}</th><td>{d.analyses}</td><td>{d.drafts}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

/* ================= Pipeline by inactivity (smooth curve) ================= */
export function TrajectoryChart({ buckets }: { buckets: Bucket[] }) {
  const uid = useId().replace(/:/g, "");
  const [hover, setHover] = useState<number | null>(null);

  const pad = { top: 16, right: 14, bottom: 30, left: 56 };
  const inset = 22;
  const innerW = W - pad.left - pad.right;
  const innerH = H - pad.top - pad.bottom;
  const n = buckets.length;
  const values = buckets.map((b) => b.value);
  const maxVal = Math.max(0, ...values);
  const scale = niceScale(maxVal, 4);
  const base = pad.top + innerH;

  const points = buckets.map((b, i) => ({
    x: pad.left + inset + (n === 1 ? (innerW - inset * 2) / 2 : (i / (n - 1)) * (innerW - inset * 2)),
    y: pad.top + innerH * (1 - b.value / scale.max),
  }));
  const line = smoothPath(points);
  const area = `${line} L ${points[n - 1].x} ${base} L ${points[0].x} ${base} Z`;
  const peakIdx = maxVal > 0 ? values.indexOf(maxVal) : 0;
  const activeIdx = hover ?? peakIdx;
  const activePoint = points[activeIdx];
  const activeBucket = buckets[activeIdx];

  function onMove(event: MouseEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width === 0) return;
    const x = ((event.clientX - rect.left) / rect.width) * W;
    let best = 0;
    let bestDist = Infinity;
    points.forEach((p, i) => {
      const dist = Math.abs(p.x - x);
      if (dist < bestDist) { best = i; bestDist = dist; }
    });
    setHover(best);
  }

  const above = activePoint.y > 100;

  return (
    <div>
      <div className="relative">
        <svg
          viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img"
          aria-label="Lost-deal value grouped by days since last contact."
          onMouseMove={onMove} onMouseLeave={() => setHover(null)}
        >
          <defs>
            <linearGradient id={`${uid}-line`} x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="#3d6bf5" />
              <stop offset="1" stopColor="#a78bfa" />
            </linearGradient>
            <linearGradient id={`${uid}-area`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#5b8cff" stopOpacity="0.32" />
              <stop offset="1" stopColor="#5b8cff" stopOpacity="0" />
            </linearGradient>
            <linearGradient id={`${uid}-vl`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#7aa2ff" stopOpacity="0" />
              <stop offset="0.5" stopColor="#7aa2ff" stopOpacity="0.9" />
              <stop offset="1" stopColor="#7aa2ff" stopOpacity="0.1" />
            </linearGradient>
          </defs>

          {scale.values.map((v) => {
            const y = pad.top + innerH * (1 - v / scale.max);
            return (
              <g key={v}>
                <line x1={pad.left} x2={W - pad.right} y1={y} y2={y} stroke={GRID} strokeDasharray="3 5" />
                <text x={pad.left - 8} y={y + 4} textAnchor="end" fontSize="11" className="fill-fg-dim">{formatCompact(v)}</text>
              </g>
            );
          })}

          {buckets.map((b, i) => (
            <text key={b.label} x={points[i].x} y={H - 9} textAnchor="middle" fontSize="11" className={i === activeIdx ? "fill-fg" : "fill-fg-dim"}>{b.label}</text>
          ))}

          <path d={area} fill={`url(#${uid}-area)`} />
          <rect x={activePoint.x - 0.75} y={pad.top} width="1.5" height={innerH} fill={`url(#${uid}-vl)`} />
          <path d={line} fill="none" stroke={`url(#${uid}-line)`} strokeWidth="3" strokeLinecap="round" style={{ filter: "drop-shadow(0 0 6px rgba(91,140,255,0.8))" }} />

          {points.map((p, i) => (
            <circle key={buckets[i].label} cx={p.x} cy={p.y} r="3.5" fill="#090e26" stroke="#7aa2ff" strokeWidth="2" />
          ))}
          <circle cx={activePoint.x} cy={activePoint.y} r="12" fill="rgba(122,162,255,0.18)" />
          <circle cx={activePoint.x} cy={activePoint.y} r="6" fill="#ffffff" stroke="#5b8cff" strokeWidth="3" />
        </svg>

        <div
          className={`pointer-events-none absolute -translate-x-1/2 rounded-lg border border-line-strong bg-raised/95 px-3 py-2 text-xs shadow-lg ${above ? "-translate-y-[135%]" : "translate-y-4"}`}
          style={{ left: `${clamp((activePoint.x / W) * 100, 18, 82)}%`, top: `${(activePoint.y / H) * 100}%` }}
        >
          <p className="font-medium">
            {activeBucket.label} inactive
            {hover === null && maxVal > 0 ? <span className="ml-2 text-brand">Peak</span> : null}
          </p>
          <p className="mt-1 text-fg-muted">{formatCurrency(activeBucket.value)} · {activeBucket.leads} {activeBucket.leads === 1 ? "lead" : "leads"}</p>
        </div>
      </div>

      <table className="sr-only">
        <caption>Lost-deal value by days since last contact</caption>
        <thead><tr><th scope="col">Inactive for</th><th scope="col">Leads</th><th scope="col">Value</th></tr></thead>
        <tbody>{buckets.map((b) => <tr key={b.label}><th scope="row">{b.label}</th><td>{b.leads}</td><td>{formatCurrency(b.value)}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

/* ================= Gauge ================= */
export function Gauge({ score }: { score: number | null }) {
  const uid = useId().replace(/:/g, "");
  const value = score == null ? 0 : clamp(score, 0, 100);
  const arc = "M 25 110 A 85 85 0 0 1 195 110";

  return (
    <div className="relative mx-auto w-full max-w-[260px]">
      <svg
        viewBox="0 0 220 125" className="w-full" role="img"
        aria-label={score == null ? "No recovery score yet." : `Average recovery score ${Math.round(score)} out of 100.`}
      >
        <defs>
          <linearGradient id={`${uid}-g`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#3d6bf5" />
            <stop offset="1" stopColor="#a78bfa" />
          </linearGradient>
        </defs>
        <path d={arc} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="18" strokeLinecap="round" />
        {value > 0 ? (
          <path
            d={arc} fill="none" stroke={`url(#${uid}-g)`} strokeWidth="18" strokeLinecap="round"
            pathLength={100} strokeDasharray={`${value} 100`}
            style={{ filter: "drop-shadow(0 0 10px rgba(91,140,255,0.65))" }}
          />
        ) : null}
      </svg>
      <div className="absolute inset-x-0 bottom-[6%] text-center">
        <span className="text-3xl font-semibold tabular-nums">{score == null ? "—" : Math.round(score)}</span>
        <span className="ml-1 text-sm text-fg-dim">/ 100</span>
      </div>
    </div>
  );
}