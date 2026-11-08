"use client";

import { Mail, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Badge, Button, ErrorBanner, Notice, PriorityBadge, ScoreMeter, Skeleton } from "./ui";
import { useApi } from "@/hooks/useApi";
import { apiGet, apiPost, toApiError, type ApiError } from "@/lib/api";
import { formatCurrency, formatDate, formatDateTime, humanize } from "@/lib/format";
import { AnalysisSchema, GenerateMessageSchema, LeadDetailSchema } from "@/lib/schemas";

type Props = { leadId: number; onClose: () => void; onChanged: () => void };

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-fg-dim">{label}</dt>
      <dd className="mt-0.5 text-sm">{children}</dd>
    </div>
  );
}

export default function LeadDrawer({ leadId, onClose, onChanged }: Props) {
  const fetchDetail = useCallback(
    (signal: AbortSignal) => apiGet(`/leads/${leadId}`, LeadDetailSchema, { signal }),
    [leadId],
  );
  const { data: lead, error, reload } = useApi(fetchDetail);
  const [busy, setBusy] = useState<"analyze" | "generate" | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [draftCreated, setDraftCreated] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => { closeRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function analyze() {
    setBusy("analyze"); setActionError(null); setNotice(null); setDraftCreated(false);
    try {
      await apiPost(`/leads/${leadId}/analyze`, AnalysisSchema, {});
      setNotice("Analysis saved.");
      reload(); onChanged();
    } catch (err) {
      setActionError(toApiError(err));
    } finally {
      setBusy(null);
    }
  }

  async function generate() {
    setBusy("generate"); setActionError(null); setNotice(null); setDraftCreated(false);
    try {
      await apiPost(`/leads/${leadId}/generate-message`, GenerateMessageSchema, {});
      setDraftCreated(true);
      reload(); onChanged();
    } catch (err) {
      setActionError(toApiError(err));
    } finally {
      setBusy(null);
    }
  }

  const a = lead?.latest_analysis ?? null;
  const breakdown = a?.score_breakdown
    ? Object.entries(a.score_breakdown).filter((entry): entry is [string, number] => typeof entry[1] === "number")
    : [];

  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} aria-hidden="true" />
      <aside
        role="dialog" aria-modal="true" aria-labelledby="lead-title"
        className="absolute inset-y-0 right-0 flex w-full max-w-xl flex-col border-l border-line bg-panel shadow-2xl"
      >
        <header className="flex items-start justify-between gap-4 border-b border-line p-5">
          <div className="min-w-0">
            <h2 id="lead-title" className="truncate text-lg font-semibold">{lead ? lead.name : "Lead details"}</h2>
            {lead ? <p className="truncate text-sm text-fg-muted">{lead.company}{lead.role ? ` · ${lead.role}` : ""}</p> : null}
            {lead ? (
              <div className="mt-3 flex flex-wrap gap-2">
                <PriorityBadge priority={lead.priority} />
                {lead.strategy ? <Badge tone="info">{lead.strategy}</Badge> : null}
                {lead.opted_out ? <Badge tone="danger" dot>Opted out</Badge> : null}
              </div>
            ) : null}
          </div>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="Close" className="rounded-lg p-2 text-fg-muted hover:bg-white/5 hover:text-fg">
            <X className="size-4" />
          </button>
        </header>

        <div className="flex-1 space-y-6 overflow-y-auto p-5">
          {error ? <ErrorBanner error={error} onRetry={reload} /> : null}
          {!lead && !error ? <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-40" /></div> : null}
          {actionError ? <ErrorBanner error={actionError} /> : null}
          {notice ? <Notice tone="success" onClose={() => setNotice(null)}>{notice}</Notice> : null}
          {draftCreated ? (
            <Notice tone="success" onClose={() => setDraftCreated(false)}>
              Draft created. <Link href="/approvals" className="font-medium text-brand underline">Review it in the Approval Center</Link>.
            </Notice>
          ) : null}

          {lead ? (
            <>
              <dl className="grid grid-cols-2 gap-4">
                <Fact label="Email">{lead.email}</Fact>
                <Fact label="Industry">{lead.industry ?? "—"}</Fact>
                <Fact label="Plan interested">{lead.plan_interested ?? "—"}</Fact>
                <Fact label="Deal value">{formatCurrency(lead.deal_value)}</Fact>
                <Fact label="Stage lost at">{lead.stage_lost_at ?? "—"}</Fact>
                <Fact label="Last contact">{formatDate(lead.last_contact_date)} ({lead.days_inactive} days ago)</Fact>
              </dl>

              <section>
                <h3 className="mb-2 text-sm font-semibold">CRM notes</h3>
                <p className="rounded-lg border border-line bg-white/[0.03] p-3 text-sm text-fg-muted">{lead.crm_notes || "No notes."}</p>
              </section>

              {a ? (
                <section className="space-y-4">
                  <h3 className="text-sm font-semibold">Recovery analysis</h3>
                  <div className="flex items-center justify-between rounded-lg border border-line bg-white/[0.03] p-3">
                    <span className="text-sm text-fg-muted">Recovery score</span>
                    <ScoreMeter score={a.recovery_score} />
                  </div>
                  {a.score_explanation ? <p className="text-sm leading-relaxed text-fg-muted">{a.score_explanation}</p> : null}
                  {breakdown.length > 0 ? (
                    <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
                      {breakdown.map(([key, value]) => (
                        <div key={key} className="flex justify-between border-b border-line/60 py-1">
                          <dt className="capitalize text-fg-dim">{humanize(key)}</dt>
                          <dd className="tabular-nums">{value.toFixed(1)}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : null}
                  <dl className="grid grid-cols-2 gap-4">
                    <Fact label="Likely loss reason"><span className="capitalize">{humanize(a.loss_reason)}</span> ({Math.round(a.confidence * 100)}% confidence)</Fact>
                    <Fact label="Engagement / urgency"><span className="capitalize">{a.engagement_level ?? "—"} / {a.urgency ?? "—"}</span></Fact>
                  </dl>
                  {a.evidence.length > 0 ? (
                    <div className="flex flex-wrap gap-2">
                      {a.evidence.map((e) => <Badge key={e}>{e}</Badge>)}
                    </div>
                  ) : null}
                  <div className="rounded-lg border border-brand/25 bg-brand/5 p-3 text-sm">
                    <p className="font-medium">{a.strategy}{a.recommended_channel ? ` · via ${a.recommended_channel}` : ""}</p>
                    {a.strategy_reasoning ? <p className="mt-1 text-fg-muted">{a.strategy_reasoning}</p> : null}
                    {a.next_action ? <p className="mt-2 text-fg-muted"><span className="text-fg">Next action:</span> {a.next_action}</p> : null}
                    {a.strategy_warnings.map((w) => <p key={w} className="mt-2 text-xs text-warn">{w}</p>)}
                  </div>
                </section>
              ) : (
                <p className="rounded-lg border border-dashed border-line-strong p-4 text-sm text-fg-muted">
                  This lead has not been analyzed yet.
                </p>
              )}

              <section>
                <h3 className="mb-2 text-sm font-semibold">Recovery messages</h3>
                {lead.messages.length === 0 ? (
                  <p className="text-sm text-fg-dim">No messages yet.</p>
                ) : (
                  <ul className="space-y-2">
                    {lead.messages.map((m) => (
                      <li key={m.message_id} className="flex items-center justify-between gap-3 rounded-lg border border-line bg-white/[0.03] px-3 py-2 text-sm">
                        <span className="min-w-0 truncate text-fg-muted">{m.strategy ?? "Message"} · {formatDateTime(m.created_at ?? "")}</span>
                        <span className="flex shrink-0 gap-2">
                          {m.approval_status ? <Badge tone={m.approval_status === "approved" ? "success" : m.approval_status === "rejected" ? "danger" : "warning"}>{m.approval_status}</Badge> : null}
                          {m.send_status ? <Badge tone="info">{m.send_status}</Badge> : null}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          ) : null}
        </div>

        <footer className="flex flex-wrap justify-end gap-3 border-t border-line p-4">
          <Button onClick={analyze} loading={busy === "analyze"} disabled={!lead || busy !== null}>
            <Sparkles className="size-4" aria-hidden /> {a ? "Re-analyze" : "Analyze"}
          </Button>
          <Button variant="primary" onClick={generate} loading={busy === "generate"} disabled={!lead || busy !== null || lead.opted_out}
            title={lead?.opted_out ? "This customer has opted out" : undefined}>
            <Mail className="size-4" aria-hidden /> Generate draft
          </Button>
        </footer>
      </aside>
    </div>
  );
}