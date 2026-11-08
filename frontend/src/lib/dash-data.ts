import { formatCompact, formatCurrency } from "./format";
import type { LeadSummary, MessageItem } from "./schemas";

export type MessageGroups = { pending: MessageItem[]; approved: MessageItem[]; rejected: MessageItem[] };

/* ---------- Time helpers ---------- */
export function dayKey(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"}`;

/** "under a minute" | "5 minutes" | "3 hours" | "2 days" */
export function elapsed(iso: string, now: Date = new Date()): string {
  const ms = now.getTime() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 60_000) return "under a minute";
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return plural(minutes, "minute");
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return plural(hours, "hour");
  return plural(Math.floor(hours / 24), "day");
}

export function ago(iso: string, now: Date = new Date()): string {
  const e = elapsed(iso, now);
  return e === "under a minute" ? "just now" : `${e} ago`;
}

/* ---------- Chart scales ---------- */
export function niceScale(max: number, ticks = 4, integer = false): { max: number; values: number[] } {
  if (!(max > 0)) return { max: ticks, values: Array.from({ length: ticks + 1 }, (_, i) => i) };
  const raw = max / ticks;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / magnitude;
  let step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * magnitude;
  if (integer) step = Math.max(1, Math.ceil(step));
  const niceMax = step * Math.ceil(max / step);
  const values: number[] = [];
  for (let v = 0; v <= niceMax + step / 1000; v += step) values.push(Math.round(v * 1e6) / 1e6);
  return { max: niceMax, values };
}

/** Smooth, non-overshooting curve through points (monotone cubic interpolation). */
export function smoothPath(points: { x: number; y: number }[]): string {
  const n = points.length;
  if (n === 0) return "";
  if (n === 1) return `M ${points[0].x} ${points[0].y}`;

  const s: number[] = [];
  for (let i = 0; i < n - 1; i++) s.push((points[i + 1].y - points[i].y) / (points[i + 1].x - points[i].x));
  const t: number[] = new Array<number>(n).fill(0);
  t[0] = s[0];
  t[n - 1] = s[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = s[i - 1] * s[i] <= 0 ? 0 : (s[i - 1] + s[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (s[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
    const a = t[i] / s[i];
    const b = t[i + 1] / s[i];
    const h = a * a + b * b;
    if (h > 9) {
      const tau = 3 / Math.sqrt(h);
      t[i] = tau * a * s[i];
      t[i + 1] = tau * b * s[i];
    }
  }

  let d = `M ${points[0].x} ${points[0].y}`;
  for (let i = 0; i < n - 1; i++) {
    const dx = (points[i + 1].x - points[i].x) / 3;
    d += ` C ${points[i].x + dx} ${points[i].y + t[i] * dx} ${points[i + 1].x - dx} ${points[i + 1].y - t[i + 1] * dx} ${points[i + 1].x} ${points[i + 1].y}`;
  }
  return d;
}

/* ---------- Activity per day ---------- */
export type ActivityDay = { key: string; label: string; analyses: number; drafts: number };

export function buildActivity(leads: LeadSummary[], messages: MessageItem[], days: number, now: Date = new Date()): ActivityDay[] {
  const series: ActivityDay[] = [];
  const index = new Map<string, ActivityDay>();
  for (let i = days - 1; i >= 0; i--) {
    const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
    const label = days <= 7
      ? date.toLocaleDateString("en-US", { weekday: "short" })
      : date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    const entry: ActivityDay = { key: dayKey(date), label, analyses: 0, drafts: 0 };
    series.push(entry);
    index.set(entry.key, entry);
  }
  for (const lead of leads) {
    if (!lead.last_analyzed_at) continue;
    const when = new Date(lead.last_analyzed_at);
    if (Number.isNaN(when.getTime())) continue;
    const entry = index.get(dayKey(when));
    if (entry) entry.analyses += 1;
  }
  for (const message of messages) {
    const when = new Date(message.created_at);
    if (Number.isNaN(when.getTime())) continue;
    const entry = index.get(dayKey(when));
    if (entry) entry.drafts += 1;
  }
  return series;
}

/* ---------- Pipeline by inactivity ---------- */
export type Bucket = { label: string; leads: number; value: number };
const BUCKET_EDGES = [30, 60, 90, 120, 150, 180];
const BUCKET_LABELS = ["0–30d", "31–60d", "61–90d", "91–120d", "121–150d", "151–180d", "180d+"];

export function buildInactivityBuckets(leads: LeadSummary[]): Bucket[] {
  const buckets: Bucket[] = BUCKET_LABELS.map((label) => ({ label, leads: 0, value: 0 }));
  for (const lead of leads) {
    const idx = BUCKET_EDGES.findIndex((edge) => lead.days_inactive <= edge);
    const bucket = buckets[idx === -1 ? BUCKET_EDGES.length : idx];
    bucket.leads += 1;
    bucket.value += lead.deal_value;
  }
  while (buckets.length > 2 && buckets[buckets.length - 1].leads === 0) buckets.pop();
  return buckets;
}

/* ---------- Industry exposure ---------- */
export type IndustryRow = { industry: string; leads: number; value: number; share: number };

export function buildIndustryRows(leads: LeadSummary[]): IndustryRow[] {
  const map = new Map<string, { leads: number; value: number }>();
  let total = 0;
  for (const lead of leads) {
    const key = lead.industry?.trim() || "Unknown";
    const entry = map.get(key) ?? { leads: 0, value: 0 };
    entry.leads += 1;
    entry.value += lead.deal_value;
    total += lead.deal_value;
    map.set(key, entry);
  }
  return Array.from(map.entries())
    .map(([industry, e]) => ({ industry, leads: e.leads, value: e.value, share: total > 0 ? e.value / total : 0 }))
    .sort((a, b) => b.value - a.value);
}

/* ---------- Leads ---------- */
export function topOpportunities(leads: LeadSummary[], limit = 6): LeadSummary[] {
  return leads
    .filter((l) => l.recovery_score != null && !l.opted_out)
    .sort((a, b) => (b.recovery_score ?? 0) - (a.recovery_score ?? 0))
    .slice(0, limit);
}

export function averageScore(leads: LeadSummary[]): { average: number | null; count: number } {
  const scores = leads.map((l) => l.recovery_score).filter((s): s is number => s != null);
  if (scores.length === 0) return { average: null, count: 0 };
  return { average: scores.reduce((sum, s) => sum + s, 0) / scores.length, count: scores.length };
}

/* ---------- Priority actions ---------- */
export type ActionItem = { id: string; impact: string | null; title: string; detail: string; meta: string; href: string };

export function buildActions(leads: LeadSummary[], messages: MessageGroups | null, now: Date = new Date()): ActionItem[] {
  if (!messages) return [];
  const byId = new Map(leads.map((l) => [l.id, l] as const));
  const items: ActionItem[] = [];

  const pending = [...messages.pending].sort((a, b) => a.created_at.localeCompare(b.created_at));
  for (const m of pending.slice(0, 3)) {
    items.push({
      id: `review-${m.message_id}`,
      impact: byId.get(m.customer_id)?.priority ?? null,
      title: `Review draft for ${m.customer_name ?? `customer #${m.customer_id}`}`,
      detail: [m.strategy, m.customer_company].filter(Boolean).join(" · "),
      meta: `Drafted ${ago(m.created_at, now)}`,
      href: "/approvals",
    });
  }

  const unsent = messages.approved.filter(
    (m) => m.send_status !== "sent" && m.send_status !== "sending" && (m.channel ?? "email") === "email",
  );
  for (const m of unsent.slice(0, 2)) {
    items.push({
      id: `send-${m.message_id}`,
      impact: byId.get(m.customer_id)?.priority ?? null,
      title: `Send approved message to ${m.customer_name ?? `customer #${m.customer_id}`}`,
      detail: [m.strategy, m.customer_company].filter(Boolean).join(" · "),
      meta: m.reviewed_at ? `Approved ${ago(m.reviewed_at, now)}` : "Approved",
      href: "/approvals",
    });
  }

  const hasMessage = new Set([...messages.pending, ...messages.approved, ...messages.rejected].map((m) => m.customer_id));
  const needDraft = leads
    .filter((l) => l.priority === "High" && !l.opted_out && l.strategy && l.strategy !== "No contact" && !hasMessage.has(l.id))
    .sort((a, b) => (b.recovery_score ?? 0) - (a.recovery_score ?? 0))
    .slice(0, 3);
  for (const l of needDraft) {
    items.push({
      id: `draft-${l.id}`,
      impact: l.priority ?? null,
      title: `Generate a draft for ${l.name}`,
      detail: `${l.strategy} · score ${(l.recovery_score ?? 0).toFixed(1)}`,
      meta: `${formatCurrency(l.deal_value)} at stake`,
      href: `/leads?q=${encodeURIComponent(l.name)}`,
    });
  }

  return items.slice(0, 4);
}

/* ---------- Avatars ---------- */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? "?";
  const last = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + last).toUpperCase();
}

export function avatarGradient(seed: string): string {
  let hue = 0;
  for (const ch of seed) hue = (hue * 31 + ch.charCodeAt(0)) % 360;
  return `linear-gradient(135deg, hsl(${hue} 70% 58%), hsl(${(hue + 48) % 360} 72% 42%))`;
}

export { formatCompact };