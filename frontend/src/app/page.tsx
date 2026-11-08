"use client";

import {
  Activity, Building2, Clock, Flame, Gauge as GaugeIcon, Inbox, Mail, Percent, Wallet, Zap,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { ActivityBars, Gauge, TrajectoryChart } from "@/components/dash-charts";
import { Avatar, InsetStat, KpiCard, Panel, StagePill, IconCircle } from "@/components/dash";
import ImportLeads from "@/components/ImportLeads";
import { Badge, Button, ErrorBanner, Skeleton } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { apiGet } from "@/lib/api";
import {
  averageScore, buildActions, buildActivity, buildIndustryRows, buildInactivityBuckets, elapsed,
  topOpportunities, type MessageGroups,
} from "@/lib/dash-data";
import { formatCompact, formatCurrency, formatDateTime, formatNumber, formatShare } from "@/lib/format";
import { MessageListSchema, OverviewSchema, PerformanceSchema } from "@/lib/schemas";
import { fetchAllLeads } from "@/lib/leads";

async function fetchDashboard(signal: AbortSignal) {
  const [overview, performance, leads] = await Promise.all([
    apiGet("/analytics/overview", OverviewSchema, { signal }),
    apiGet("/analytics/recovery-performance", PerformanceSchema, { signal }),
    fetchAllLeads(signal),
  ]);

  // Messages are optional: if this fails, the rest of the dashboard still renders.
  let messages: MessageGroups | null = null;
  try {
    const get = (status: string) => apiGet(`/messages?status=${status}&limit=200`, MessageListSchema, { signal });
    const [pending, approved, rejected] = await Promise.all([get("pending"), get("approved"), get("rejected")]);
    messages = { pending, approved, rejected };
  } catch (err) {
    if (signal.aborted) throw err;
  }
  return { overview, performance, leads, messages };
}
type DashboardData = Awaited<ReturnType<typeof fetchDashboard>>;

const WINDOWS = [7, 14, 30] as const;
const GRID = "grid gap-5 lg:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_340px]";
const impactTone = (impact: string | null) => (impact === "High" ? "danger" : impact === "Medium" ? "warning" : "neutral");

export default function OverviewPage() {
  const { data, error, loading, reload } = useApi(fetchDashboard);
  const [days, setDays] = useState<number>(7);

  return (
    <>
      <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[28px] font-semibold leading-tight tracking-tight">Recovery Command Center</h1>
          <p className="mt-1 text-sm text-fg-muted">Live view of your lost-deal pipeline, approvals and outreach.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {data ? <span className="hidden text-xs text-fg-dim sm:inline">Updated {formatDateTime(data.overview.generated_at)}</span> : null}
          <label className="flex h-10 items-center gap-2 rounded-full border border-line-strong bg-white/5 pl-3.5 pr-2 text-sm">
            <Clock className="size-4 text-fg-dim" aria-hidden />
            <span className="text-fg-dim">Activity window</span>
            <select
              value={days} onChange={(e) => setDays(Number(e.target.value))}
              className="rounded-full bg-transparent py-1 pr-1 text-fg focus:outline-none [&>option]:bg-panel"
            >
              {WINDOWS.map((d) => <option key={d} value={d}>Last {d} days</option>)}
            </select>
          </label>
          <Button onClick={reload} loading={loading}>Refresh</Button>
        </div>
      </div>

      {loading && !data ? (
        <div aria-busy="true">
          <p className="sr-only" role="status">Loading dashboard…</p>
          <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
            {Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-44" />)}
          </div>
          <div className={`mt-5 ${GRID}`}>
            {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-80" />)}
          </div>
        </div>
      ) : error ? (
        <ErrorBanner error={error} onRetry={reload} />
      ) : data ? (
        data.overview.leads_total === 0 ? <ImportLeads onImported={reload} /> : <Dashboard data={data} days={days} />
      ) : null}
    </>
  );
}

function Dashboard({ data, days }: { data: DashboardData; days: number }) {
  const { overview, performance, leads, messages } = data;
  const now = useMemo(() => new Date(), [data]);

  const allMessages = useMemo(
    () => (messages ? [...messages.pending, ...messages.approved, ...messages.rejected] : []),
    [messages],
  );
  const activity = useMemo(() => buildActivity(leads, allMessages, days, now), [leads, allMessages, days, now]);
  const activityTotals = useMemo(
    () => activity.reduce((t, d) => ({ analyses: t.analyses + d.analyses, drafts: t.drafts + d.drafts }), { analyses: 0, drafts: 0 }),
    [activity],
  );
  const buckets = useMemo(() => buildInactivityBuckets(leads), [leads]);
  const industries = useMemo(() => buildIndustryRows(leads), [leads]);
  const opportunities = useMemo(() => topOpportunities(leads, 6), [leads]);
  const actions = useMemo(() => buildActions(leads, messages, now), [leads, messages, now]);
  const { average, count: scoredCount } = useMemo(() => averageScore(leads), [leads]);

  const totalValue = leads.reduce((s, l) => s + l.deal_value, 0);
  const coldValue = leads.filter((l) => l.days_inactive > 90).reduce((s, l) => s + l.deal_value, 0);
  const high = overview.priority_distribution.High;
  const highLeads = leads.filter((l) => l.priority === "High");

  // KPI 1 insight: where the high-priority leads are concentrated.
  const industryCounts = new Map<string, number>();
  for (const l of highLeads) industryCounts.set(l.industry ?? "Unknown", (industryCounts.get(l.industry ?? "Unknown") ?? 0) + 1);
  const topIndustry = Array.from(industryCounts.entries()).sort((a, b) => b[1] - a[1])[0];
  const highInsight =
    overview.leads_analyzed === 0 ? "Analyze your leads to rank them by recovery potential."
    : high === 0 ? "No lead has reached High priority yet."
    : topIndustry ? `Most are in ${topIndustry[0]} (${topIndustry[1]} of ${high}).` : "";

  const oldestPending = messages && messages.pending.length
    ? messages.pending.reduce((a, b) => (a.created_at < b.created_at ? a : b)).created_at : null;
  const pendingInsight =
    overview.approvals.pending === 0 ? "Nothing is waiting on you right now."
    : oldestPending ? `The oldest draft has been waiting ${elapsed(oldestPending, now)}.`
    : "Approve or reject drafts to move them forward.";

  const withApproved = performance.pipeline_value_with_approved_messages;
  const readiness = average == null ? null : average >= 65 ? { label: "Strong", tone: "success" as const }
    : average >= 40 ? { label: "Moderate", tone: "warning" as const } : { label: "Weak", tone: "danger" as const };

  return (
    <div className="space-y-5">
      {/* ---------- KPI row ---------- */}
      <section aria-label="Key metrics" className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          title="High-priority leads" icon={<Flame className="size-4" />} value={formatNumber(high)}
          chip={<Badge tone="brand">{overview.leads_analyzed > 0 ? `${formatShare(high, overview.leads_analyzed)} of analyzed` : "Not analyzed yet"}</Badge>}
          sub={`${formatCurrency(overview.lost_pipeline_value_by_priority.High)} in lost pipeline`}
          insight={highInsight}
        />
        <KpiCard
          title="Pipeline in play" icon={<Wallet className="size-4" />} value={formatCompact(withApproved)}
          chip={<Badge tone="brand">Potential</Badge>}
          sub="Lost-deal value with an approved message"
          insight={totalValue > 0 ? `${formatShare(withApproved, totalValue)} of the ${formatCompact(totalValue)} lost pipeline has an approved message.` : "No pipeline value yet."}
        />
        <KpiCard
          title="Pending approvals" icon={<Zap className="size-4" />} value={formatNumber(overview.approvals.pending)}
          chip={overview.approvals.pending > 0 ? <Badge tone="warning" dot>Needs review</Badge> : <Badge tone="success" dot>All clear</Badge>}
          sub="Drafts awaiting a human decision"
          insight={pendingInsight}
        />
        <KpiCard
          unavailable title="Recovery rate" icon={<Percent className="size-4" />} value="Not tracked"
          chip={<Badge>No outcome data</Badge>}
          sub="Replies and won deals aren’t recorded yet"
          insight="Record outcomes to unlock recovery rate and recovered revenue."
        />
      </section>

      {/* ---------- Charts and lists ---------- */}
      <div className={GRID}>
        <Panel title="Recovery Activity" subtitle={`Analyses and drafts, last ${days} days`} href="/analytics" hrefLabel="Analytics" edge>
          <InsetStat
            icon={<Activity className="size-4" />} value={formatNumber(activityTotals.analyses + activityTotals.drafts)} label="events"
            chip={<Badge tone="brand">{activityTotals.analyses} analyses · {activityTotals.drafts} drafts</Badge>}
          >
            <ActivityBars data={activity} />
          </InsetStat>
          <p className="mt-3 text-xs text-fg-dim">
            Analyses are counted on each lead’s latest analysis date.
            {messages ? "" : " Draft data is unavailable right now."}
          </p>
        </Panel>

        <Panel title="Pipeline by Inactivity" subtitle="Lost-deal value by days since last contact" href="/leads" hrefLabel="Leads" edge>
          <InsetStat
            icon={<Clock className="size-4" />} value={formatCompact(coldValue)} label="inactive 90+ days"
            chip={<Badge tone="brand">{formatShare(coldValue, totalValue)} of pipeline</Badge>}
          >
            <TrajectoryChart buckets={buckets} />
          </InsetStat>
          <p className="mt-3 text-xs text-fg-dim">Deal sizes of lost deals, not recovered revenue. Hover the chart to inspect a range.</p>
        </Panel>

        <Panel title="Recovery Readiness" subtitle="Average recovery score of analyzed leads">
          <div className="grid grid-cols-2 gap-3">
            <div className="well p-3">
              <div className="flex items-center gap-2">
                <IconCircle className="size-8"><Mail className="size-3.5" /></IconCircle>
                <span className="text-xl font-semibold tabular-nums">{formatNumber(overview.messages_total)}</span>
              </div>
              <p className="mt-2 text-xs font-medium">Drafts created</p>
              <p className="text-[11px] text-fg-dim">AI-generated messages</p>
            </div>
            <div className="well p-3">
              <div className="flex items-center gap-2">
                <IconCircle className="size-8"><Inbox className="size-3.5" /></IconCircle>
                <span className="text-xl font-semibold tabular-nums">{formatNumber(overview.approvals.pending)}</span>
              </div>
              <p className="mt-2 text-xs font-medium">Awaiting review</p>
              <p className="text-[11px] text-fg-dim">Pending human decision</p>
            </div>
          </div>
          <div className="mt-6 flex flex-1 flex-col items-center justify-center gap-3">
            <Gauge score={average} />
            {readiness ? <Badge tone={readiness.tone} dot>{readiness.label} readiness</Badge> : <Badge>Not analyzed yet</Badge>}
            <p className="text-center text-xs text-fg-dim">
              {average == null ? "Analyze leads to see readiness." : `Based on ${formatNumber(scoredCount)} analyzed leads · 65+ strong, 40+ moderate`}
            </p>
          </div>
        </Panel>

        <Panel title="Industry Exposure" subtitle="Lost pipeline by industry" href="/leads" hrefLabel="Leads">
          <InsetStat
            icon={<Building2 className="size-4" />} value={formatNumber(industries.length)} label={industries.length === 1 ? "industry" : "industries"}
            chip={industries[0] ? <Badge tone="info">Top: {industries[0].industry}</Badge> : undefined}
          >
            <ul className="dotgrid -mx-1 space-y-1 rounded-xl p-1">
              {industries.slice(0, 4).map((row) => (
                <li key={row.industry} className="flex items-center gap-3 rounded-xl px-2 py-2">
                  <Avatar name={row.industry} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{row.industry}</p>
                    <p className="text-xs text-fg-dim">{row.leads} {row.leads === 1 ? "lead" : "leads"}</p>
                  </div>
                  <span className="text-sm tabular-nums">{formatCompact(row.value)}</span>
                  <Badge tone="brand" className="w-14 justify-center">{Math.round(row.share * 100)}%</Badge>
                </li>
              ))}
            </ul>
          </InsetStat>
        </Panel>

        <Panel title="Top Recovery Opportunities" subtitle="Highest recovery scores" href="/leads">
          {opportunities.length === 0 ? (
            <p className="rounded-xl border border-dashed border-line-strong p-4 text-sm text-fg-muted">
              No analyzed leads yet. Open Leads and click “Analyze unscored leads”.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[380px] text-sm">
                <caption className="sr-only">Top leads by recovery score</caption>
                <thead className="text-left text-xs text-fg-dim">
                  <tr>
                    <th scope="col" className="pb-2 pr-2 font-medium">Customer</th>
                    <th scope="col" className="pb-2 font-medium">Lost at</th>
                    <th scope="col" className="pb-2 pl-2 text-right font-medium">Value</th>
                  </tr>
                </thead>
                <tbody>
                  {opportunities.map((l) => (
                    <tr key={l.id} className="border-t border-line/60">
                      <th scope="row" className="py-2.5 pr-2 text-left font-normal">
                        <Link href={`/leads?q=${encodeURIComponent(l.name)}`} className="flex items-center gap-3 hover:text-brand">
                          <Avatar name={l.name} />
                          <span className="min-w-0">
                            <span className="block truncate font-medium">{l.name}</span>
                            <span className="block truncate text-xs text-fg-dim">{l.company}</span>
                          </span>
                        </Link>
                      </th>
                      <td className="py-2.5"><StagePill stage={l.stage_lost_at} /></td>
                      <td className="py-2.5 pl-2 text-right">
                        <span className="block tabular-nums">{formatCurrency(l.deal_value)}</span>
                        <span className="block text-xs text-fg-dim">Score {(l.recovery_score ?? 0).toFixed(1)}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel title="Priority Actions" subtitle="What needs a human next" href="/approvals" hrefLabel="Approvals">
          {!messages ? (
            <p className="rounded-xl border border-dashed border-line-strong p-4 text-sm text-fg-muted">
              Message data is unavailable, so actions can’t be listed. Check the backend and refresh.
            </p>
          ) : actions.length === 0 ? (
            <p className="rounded-xl border border-dashed border-line-strong p-4 text-sm text-fg-muted">
              No actions waiting. Analyze leads and generate drafts to create new ones.
            </p>
          ) : (
            <ul className="space-y-3">
              {actions.map((a) => (
                <li key={a.id}>
                  <Link href={a.href} className="block rounded-xl border border-line bg-white/[0.03] p-3 transition-colors hover:border-brand/40 hover:bg-white/[0.06]">
                    <div className="flex items-center justify-between gap-2">
                      <Badge tone={impactTone(a.impact)} dot>{a.impact ? `${a.impact} priority` : "Action"}</Badge>
                      <span className="text-xs text-fg-dim">{a.meta}</span>
                    </div>
                    <p className="mt-2 text-sm font-medium">{a.title}</p>
                    {a.detail ? <p className="mt-1 text-xs text-fg-dim">{a.detail}</p> : null}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <aside className="flex items-start gap-3 rounded-xl border border-info/25 bg-info/5 p-4 text-sm text-fg-muted">
        <GaugeIcon className="mt-0.5 size-4 shrink-0 text-info" aria-hidden />
        <p>
          Insights are computed from your data using fixed rules. Pipeline figures are the sizes of deals that were lost,
          not recovered revenue, and simulated sends contacted no one. Recovery rate stays “Not tracked” until outcomes are recorded.
        </p>
      </aside>
    </div>
  );
}