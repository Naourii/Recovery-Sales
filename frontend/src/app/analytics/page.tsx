
"use client";

import {
  Activity,
  CheckCircle2,
  Clock3,
  DollarSign,
  Mail,
  Send,
  Target,
  TrendingUp,
  XCircle,
} from "lucide-react";

import { useApi } from "@/hooks/useApi";
import { apiGet } from "@/lib/api";
import {
  formatCurrency,
  formatDateTime,
  formatNumber,
} from "@/lib/format";

import {
  PerformanceSchema,
  OverviewSchema,
  type Performance,
  type Overview,
} from "@/lib/schemas";

import { HBars } from "@/components/charts";

import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  LoadingGrid,
  PageHeader,
  SectionTitle,
  StatCard,
} from "@/components/ui";

async function fetchAnalytics(signal: AbortSignal) {
  const [overview, performance] = await Promise.all([
    apiGet("/analytics/overview", OverviewSchema, { signal }),
    apiGet("/analytics/recovery-performance", PerformanceSchema, {
      signal,
    }),
  ]);

  return { overview, performance };
}

function percentage(value: number | null): string {
  if (value === null || !Number.isFinite(value)) {
    return "Not available";
  }

  return `${(value * 100).toFixed(1)}%`;
}

function MetricCard({
  title,
  value,
  description,
  icon,
}: {
  title: string;
  value: string;
  description: string;
  icon: React.ReactNode;
}) {
  return (
    <Card className="card-glow p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm text-fg-muted">{title}</p>
          <p className="mt-3 text-2xl font-semibold tracking-tight text-fg">
            {value}
          </p>
        </div>

        <div className="grid size-10 shrink-0 place-items-center rounded-xl border border-brand/20 bg-brand/10 text-brand">
          {icon}
        </div>
      </div>

      <p className="mt-3 text-xs leading-relaxed text-fg-dim">
        {description}
      </p>
    </Card>
  );
}

export default function AnalyticsPage() {
  const { data, error, loading, reload } = useApi(fetchAnalytics);

  return (
    <div className="space-y-8">
      <PageHeader
        title="Recovery Analytics"
        description="Understand your recovery funnel, outreach performance, and lost pipeline."
        actions={
          <>
            {data ? (
              <span className="text-xs text-fg-dim">
                Updated {formatDateTime(data.performance.generated_at)}
              </span>
            ) : null}

            <Button onClick={reload} loading={loading}>
              Refresh
            </Button>
          </>
        }
      />

      {loading && !data ? (
        <LoadingGrid />
      ) : error ? (
        <ErrorBanner error={error} onRetry={reload} />
      ) : data ? (
        <AnalyticsContent
          overview={data.overview}
          performance={data.performance}
        />
      ) : null}
    </div>
  );
}

function AnalyticsContent({
  overview,
  performance,
}: {
  overview: Overview;
  performance: Performance;
}) {
  const funnel = performance.funnel;

  const strategyRows = [...performance.by_strategy].sort(
    (a, b) => b.messages - a.messages
  );

  const maxStrategyMessages = Math.max(
    ...strategyRows.map((item) => item.messages),
    1
  );

  const totalSent =
    overview.sends.sent_simulated + overview.sends.sent_real;

  const totalReviewed =
    overview.approvals.approved + overview.approvals.rejected;

  return (
    <div className="space-y-8">
      {/* Performance summary */}

      <section aria-label="Performance summary">
        <div className="mb-4 flex items-center justify-between gap-3">
          <SectionTitle>Performance overview</SectionTitle>
          <Badge tone="brand">Live metrics</Badge>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            icon={<Target className="size-4" />}
            label="Approval rate"
            value={percentage(performance.approval_rate)}
            chip={
              <Badge tone="success">
                {formatNumber(overview.approvals.approved)} approved
              </Badge>
            }
            hint={`${formatNumber(overview.approvals.rejected)} messages rejected`}
          />

          <StatCard
            icon={<Send className="size-4" />}
            label="Send rate"
            value={percentage(performance.send_rate_of_approved)}
            chip={
              <Badge tone="info">
                {formatNumber(totalSent)} sent
              </Badge>
            }
            hint="Share of approved messages that were sent."
          />

          <StatCard
            icon={<DollarSign className="size-4" />}
            label="Lost pipeline"
            value={formatCurrency(performance.pipeline_value_total)}
            hint="Total value of deals currently represented in the recovery pipeline."
          />

          <StatCard
            icon={<TrendingUp className="size-4" />}
            label="Recovered revenue"
            value={
              performance.actual_recovered_revenue === null
                ? "Not tracked"
                : formatCurrency(performance.actual_recovered_revenue)
            }
            chip={
              <Badge tone="warning">
                {performance.recovered_revenue_status}
              </Badge>
            }
            hint="Actual recovered revenue requires replies and won deals to be recorded."
            unavailable={performance.actual_recovered_revenue === null}
          />
        </div>
      </section>

      {/* Funnel */}

      <section>
        <SectionTitle>Recovery funnel</SectionTitle>

        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(260px,0.8fr)]">
          <Card className="p-6">
            <div className="mb-6 flex items-center justify-between gap-3">
              <div>
                <h3 className="font-semibold">Outreach journey</h3>
                <p className="mt-1 text-xs text-fg-dim">
                  From imported leads to successful outreach.
                </p>
              </div>

              <div className="grid size-10 place-items-center rounded-xl bg-brand/10 text-brand">
                <Activity className="size-5" />
              </div>
            </div>

            <HBars
              max={Math.max(funnel.leads_total, 1)}
              rows={[
                {
                  label: "Leads imported",
                  value: funnel.leads_total,
                  display: formatNumber(funnel.leads_total),
                  barClass: "bg-brand",
                },
                {
                  label: "Leads analyzed",
                  value: funnel.leads_analyzed,
                  display: formatNumber(funnel.leads_analyzed),
                  barClass: "bg-info",
                },
                {
                  label: "Messages generated",
                  value: funnel.messages_generated,
                  display: formatNumber(funnel.messages_generated),
                  barClass: "bg-brand",
                },
                {
                  label: "Messages approved",
                  value: funnel.messages_approved,
                  display: formatNumber(funnel.messages_approved),
                  barClass: "bg-ok",
                },
                {
                  label: "Messages rejected",
                  value: funnel.messages_rejected,
                  display: formatNumber(funnel.messages_rejected),
                  barClass: "bg-bad",
                },
                {
                  label: "Messages sent",
                  value: totalSent,
                  display: formatNumber(totalSent),
                  barClass: "bg-info",
                },
              ]}
            />
          </Card>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-1">
            <MetricCard
              title="Pending review"
              value={formatNumber(funnel.messages_pending)}
              description="Messages waiting for human approval."
              icon={<Clock3 className="size-5" />}
            />

            <MetricCard
              title="Send failures"
              value={formatNumber(funnel.send_failures)}
              description="Messages that encountered a delivery failure."
              icon={<XCircle className="size-5" />}
            />

            <MetricCard
              title="Total messages"
              value={formatNumber(overview.messages_total)}
              description="All messages generated by the recovery workflow."
              icon={<Mail className="size-5" />}
            />
          </div>
        </div>
      </section>

      {/* Pipeline */}

      <section>
        <SectionTitle>Pipeline performance</SectionTitle>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card className="p-6">
            <h3 className="font-semibold">Pipeline progression</h3>
            <p className="mt-1 text-xs text-fg-dim">
              Deal value across different outreach stages.
            </p>

            <div className="mt-6">
              <HBars
                max={Math.max(performance.pipeline_value_total, 1)}
                rows={[
                  {
                    label: "Total lost pipeline",
                    value: performance.pipeline_value_total,
                    display: formatCurrency(performance.pipeline_value_total),
                    barClass: "bg-brand",
                  },
                  {
                    label: "With approved messages",
                    value: performance.pipeline_value_with_approved_messages,
                    display: formatCurrency(
                      performance.pipeline_value_with_approved_messages
                    ),
                    barClass: "bg-ok",
                  },
                  {
                    label: "Contacted (simulated)",
                    value: performance.pipeline_value_contacted_simulated,
                    display: formatCurrency(
                      performance.pipeline_value_contacted_simulated
                    ),
                    barClass: "bg-info",
                    hatched: true,
                  },
                  {
                    label: "Contacted (real)",
                    value: performance.pipeline_value_contacted_real,
                    display: formatCurrency(
                      performance.pipeline_value_contacted_real
                    ),
                    barClass: "bg-ok",
                  },
                ]}
              />
            </div>

            <div className="mt-6 rounded-xl border border-warn/20 bg-warn/5 p-3">
              <p className="text-xs leading-relaxed text-fg-muted">
                Pipeline values represent lost deal opportunities. They do
                not necessarily represent recovered revenue.
              </p>
            </div>
          </Card>

          <Card className="p-6">
            <h3 className="font-semibold">Approval breakdown</h3>
            <p className="mt-1 text-xs text-fg-dim">
              Human review decisions across generated messages.
            </p>

            <div className="mt-6 space-y-5">
              <div>
                <div className="mb-2 flex items-center justify-between">
                  <span className="flex items-center gap-2 text-sm text-fg-muted">
                    <CheckCircle2 className="size-4 text-ok" />
                    Approved
                  </span>
                  <span className="font-semibold tabular-nums">
                    {formatNumber(overview.approvals.approved)}
                  </span>
                </div>

                <div className="h-2 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className="h-full rounded-full bg-ok transition-all"
                    style={{
                      width: `${
                        totalReviewed > 0
                          ? (overview.approvals.approved / totalReviewed) * 100
                          : 0
                      }%`,
                    }}
                  />
                </div>
              </div>

              <div>
                <div className="mb-2 flex items-center justify-between">
                  <span className="flex items-center gap-2 text-sm text-fg-muted">
                    <XCircle className="size-4 text-bad" />
                    Rejected
                  </span>
                  <span className="font-semibold tabular-nums">
                    {formatNumber(overview.approvals.rejected)}
                  </span>
                </div>

                <div className="h-2 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className="h-full rounded-full bg-bad transition-all"
                    style={{
                      width: `${
                        totalReviewed > 0
                          ? (overview.approvals.rejected / totalReviewed) * 100
                          : 0
                      }%`,
                    }}
                  />
                </div>
              </div>

              <div className="flex items-center justify-between border-t border-line pt-4">
                <span className="text-sm text-fg-muted">
                  Total reviewed
                </span>
                <span className="text-lg font-semibold tabular-nums">
                  {formatNumber(totalReviewed)}
                </span>
              </div>
            </div>
          </Card>
        </div>
      </section>

      {/* Strategy performance */}

      <section>
        <SectionTitle>Strategy performance</SectionTitle>

        <Card className="mt-4 overflow-hidden">
          {strategyRows.length === 0 ? (
            <div className="p-8 text-center">
              <p className="font-medium">No strategy data yet</p>
              <p className="mt-2 text-sm text-fg-dim">
                Strategy statistics will appear after messages are generated.
              </p>
            </div>
          ) : (
            <>
              <div className="border-b border-line px-6 py-4">
                <h3 className="font-semibold">Message strategies</h3>
                <p className="mt-1 text-xs text-fg-dim">
                  Compare the volume and outcomes of each recovery strategy.
                </p>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full min-w-[650px] text-sm">
                  <thead className="bg-white/[0.025] text-left text-xs uppercase tracking-wider text-fg-dim">
                    <tr>
                      <th className="px-6 py-4 font-medium">Strategy</th>
                      <th className="px-4 py-4 text-right font-medium">Messages</th>
                      <th className="px-4 py-4 text-right font-medium">Approved</th>
                      <th className="px-4 py-4 text-right font-medium">Rejected</th>
                      <th className="px-6 py-4 text-right font-medium">Sent</th>
                    </tr>
                  </thead>

                  <tbody>
                    {strategyRows.map((item) => (
                      <tr
                        key={item.strategy}
                        className="border-t border-line/70 transition-colors hover:bg-white/[0.025]"
                      >
                        <th className="px-6 py-4 text-left font-medium text-fg">
                          <div>{item.strategy}</div>
                          <div className="mt-2 h-1.5 w-28 overflow-hidden rounded-full bg-white/[0.06]">
                            <div
                              className="h-full rounded-full bg-brand"
                              style={{
                                width: `${
                                  (item.messages / maxStrategyMessages) * 100
                                }%`,
                              }}
                            />
                          </div>
                        </th>

                        <td className="px-4 py-4 text-right tabular-nums">
                          {formatNumber(item.messages)}
                        </td>

                        <td className="px-4 py-4 text-right tabular-nums text-ok">
                          {formatNumber(item.approved)}
                        </td>

                        <td className="px-4 py-4 text-right tabular-nums text-bad">
                          {formatNumber(item.rejected)}
                        </td>

                        <td className="px-6 py-4 text-right tabular-nums text-info">
                          {formatNumber(item.sent)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Card>
      </section>

      {/* System notes */}

      {performance.notes.length > 0 ? (
        <section>
          <SectionTitle>Analytics notes</SectionTitle>

          <Card className="mt-4 divide-y divide-line/70">
            {performance.notes.map((note, index) => (
              <div
                key={`${index}-${note}`}
                className="flex items-start gap-3 p-4"
              >
                <div className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-info/10 text-info">
                  <Activity className="size-4" />
                </div>

                <p className="text-sm leading-relaxed text-fg-muted">
                  {note}
                </p>
              </div>
            ))}
          </Card>
        </section>
      ) : null}
    </div>
  );
}
