"use client";

import { Check, Inbox, Pencil, Send, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, ErrorBanner, LoadingGrid, Notice, PageHeader, cn } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { apiGet, apiPost, apiPut, toApiError, type ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { MessageListSchema, MessageSchema, SendResponseSchema, type MessageItem } from "@/lib/schemas";

type Tab = "pending" | "approved" | "rejected";
const TABS: { id: Tab; label: string }[] = [
  { id: "pending", label: "Pending review" },
  { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
];
const EMPTY: MessageItem[] = [];

async function fetchQueues(signal: AbortSignal): Promise<Record<Tab, MessageItem[]>> {
  const get = (status: Tab) => apiGet(`/messages?status=${status}&limit=200`, MessageListSchema, { signal });
  const [pending, approved, rejected] = await Promise.all([get("pending"), get("approved"), get("rejected")]);
  return { pending, approved, rejected };
}

function parseEmail(content: string): { subject: string | null; body: string } {
  const lines = content.split("\n");
  if (/^subject:/i.test(lines[0] ?? "")) {
    return { subject: lines[0].replace(/^subject:\s*/i, "").trim(), body: lines.slice(1).join("\n").trim() };
  }
  return { subject: null, body: content.trim() };
}

function StatusBadge({ m }: { m: MessageItem }) {
  if (m.send_status === "sent") return <Badge tone="info" dot>Sent</Badge>;
  if (m.send_status === "failed") return <Badge tone="danger" dot>Send failed</Badge>;
  if (m.status === "approved") return <Badge tone="success" dot>Approved</Badge>;
  if (m.status === "rejected") return <Badge tone="danger" dot>Rejected</Badge>;
  return <Badge tone="warning" dot>Pending review</Badge>;
}

type NoticeState = { tone: "success" | "info"; text: string };

export default function ApprovalsPage() {
  const { data, error, loading, reload } = useApi(fetchQueues);
  const [tab, setTab] = useState<Tab>("pending");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState("Reviewer");
  const [notice, setNotice] = useState<NoticeState | null>(null);

  const list = data ? data[tab] : EMPTY;
  const selected = list.find((m) => m.message_id === selectedId) ?? list[0] ?? null;

  return (
    <>
      <PageHeader
        title="Approval Center"
        description="Nothing is sent until a human approves it."
        actions={
          <>
            <label className="flex items-center gap-2 text-sm text-fg-muted">
              Reviewing as
              <input
                value={reviewer} onChange={(e) => setReviewer(e.target.value)} maxLength={100}
                className="w-40 rounded-lg border border-line-strong bg-panel px-3 py-1.5 text-sm text-fg focus:border-brand focus:outline-none"
              />
            </label>
            <Button onClick={reload} loading={loading}>Refresh</Button>
          </>
        }
      />

      {notice ? <div className="mb-4"><Notice tone={notice.tone} onClose={() => setNotice(null)}>{notice.text}</Notice></div> : null}

      {loading && !data ? (
        <LoadingGrid cards={3} />
      ) : error ? (
        <ErrorBanner error={error} onRetry={reload} />
      ) : data ? (
        <>
          <div role="tablist" aria-label="Message status" className="mb-4 inline-flex rounded-xl border border-line bg-white/[0.03] p-1">
            {TABS.map((t) => (
              <button
                key={t.id} role="tab" type="button" aria-selected={tab === t.id}
                onClick={() => { setTab(t.id); setSelectedId(null); }}
                className={cn("rounded-lg px-4 py-1.5 text-sm font-medium transition-colors", tab === t.id ? "bg-brand/15 text-fg ring-1 ring-inset ring-brand/30" : "text-fg-muted hover:text-fg")}
              >
                {t.label} <span className="ml-1 text-xs tabular-nums text-fg-dim">{data[t.id].length}</span>
              </button>
            ))}
          </div>

          {list.length === 0 ? (
            <Card className="grid justify-items-center gap-3 p-12 text-center">
              <Inbox className="size-8 text-fg-dim" aria-hidden />
              <p className="font-medium">No {tab} messages</p>
              <p className="max-w-md text-sm text-fg-muted">
                {tab === "pending"
                  ? "Drafts appear here after you generate them. Open a lead and click “Generate draft”."
                  : `Messages you ${tab === "approved" ? "approve" : "reject"} will be listed here.`}
              </p>
              {tab === "pending" ? <Link href="/leads" className="text-sm font-medium text-brand hover:underline">Go to Leads</Link> : null}
            </Card>
          ) : (
            <div className="grid gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
              <ul aria-label="Messages" className="max-h-[70vh] space-y-2 overflow-y-auto pr-1">
                {list.map((m) => {
                  const { subject, body } = parseEmail(m.message_content);
                  const active = selected?.message_id === m.message_id;
                  return (
                    <li key={m.message_id}>
                      <button
                        type="button" onClick={() => setSelectedId(m.message_id)} aria-current={active ? "true" : undefined}
                        className={cn("card card-glow w-full p-4 text-left", active && "border-brand/60 shadow-[0_0_0_1px_rgba(139,154,255,0.3)]")}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="truncate font-medium">{m.customer_name ?? `Customer #${m.customer_id}`}</p>
                            <p className="truncate text-xs text-fg-dim">{m.customer_company ?? ""}</p>
                          </div>
                          <StatusBadge m={m} />
                        </div>
                        <p className="mt-2 truncate text-sm text-fg-muted">{subject ?? body}</p>
                        <p className="mt-2 text-xs text-fg-dim">{m.strategy ?? "—"} · {formatDateTime(m.created_at)}</p>
                      </button>
                    </li>
                  );
                })}
              </ul>

              {selected ? (
                <MessagePanel
                  key={selected.message_id} message={selected} reviewer={reviewer}
                  onDone={(n) => { setNotice(n); reload(); }}
                />
              ) : null}
            </div>
          )}
        </>
      ) : null}
    </>
  );
}

type Busy = null | "save" | "approve" | "reject" | "send";

function MessagePanel({ message, reviewer, onDone }: { message: MessageItem; reviewer: string; onDone: (n: NoticeState) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.message_content);
  const [showReject, setShowReject] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const name = reviewer.trim();
  const { subject, body } = parseEmail(message.message_content);
  const isPending = message.status === "pending";
  const canSend = message.status === "approved" && message.send_status !== "sent" && message.send_status !== "sending";
  const emailChannel = (message.channel ?? "email") === "email";

  async function run(kind: Exclude<Busy, null>, action: () => Promise<NoticeState>) {
    setBusy(kind);
    setError(null);
    try {
      onDone(await action());
    } catch (err) {
      const e = toApiError(err);
      if (kind === "send" && e.code === "duplicate_send") onDone({ tone: "info", text: "This message was already sent." });
      else setError(e);
    } finally {
      setBusy(null);
    }
  }

  const id = encodeURIComponent(message.message_id);

  return (
    <Card className="p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{message.customer_name ?? `Customer #${message.customer_id}`}</h2>
          <p className="text-sm text-fg-muted">{message.customer_company}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge m={message} />
          {message.edited_at ? <Badge title={`Edited ${formatDateTime(message.edited_at)}`}>Edited{message.edited_by ? ` by ${message.edited_by}` : ""}</Badge> : null}
          {message.channel ? <Badge tone="neutral">{message.channel}</Badge> : null}
        </div>
      </div>

      <div className="mt-5 overflow-hidden rounded-xl border border-line bg-canvas/60">
        <div className="space-y-1 border-b border-line px-4 py-3 text-sm">
          <p><span className="inline-block w-16 text-fg-dim">To</span>{message.customer_name} ({message.customer_company})</p>
          <p><span className="inline-block w-16 text-fg-dim">Subject</span><span className="font-medium">{subject ?? "—"}</span></p>
        </div>
        {editing ? (
          <div className="p-4">
            <label className="mb-2 block text-xs text-fg-dim" htmlFor="draft">
              Edit the message. Keep the first line as “Subject: …” so the subject is preserved.
            </label>
            <textarea
              id="draft" value={draft} onChange={(e) => setDraft(e.target.value)} rows={12} maxLength={5000}
              className="w-full rounded-lg border border-line-strong bg-panel p-3 text-sm leading-relaxed focus:border-brand focus:outline-none"
            />
          </div>
        ) : (
          <p className="whitespace-pre-wrap p-4 text-sm leading-relaxed">{body}</p>
        )}
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-3 text-xs text-fg-dim sm:grid-cols-4">
        <div><dt>Strategy</dt><dd className="mt-0.5 text-sm text-fg-muted">{message.strategy ?? "—"}</dd></div>
        <div><dt>Created</dt><dd className="mt-0.5 text-sm text-fg-muted">{formatDateTime(message.created_at)}</dd></div>
        <div><dt>Reviewed by</dt><dd className="mt-0.5 text-sm text-fg-muted">{message.reviewer ?? "—"}</dd></div>
        <div><dt>Reviewed at</dt><dd className="mt-0.5 text-sm text-fg-muted">{message.reviewed_at ? formatDateTime(message.reviewed_at) : "—"}</dd></div>
      </dl>
      {message.review_note ? <p className="mt-3 rounded-lg border border-bad/20 bg-bad/5 p-3 text-sm text-fg-muted">Rejection reason: {message.review_note}</p> : null}

      {error ? <div className="mt-4"><ErrorBanner error={error} /></div> : null}

      {isPending ? (
        <div className="mt-6 space-y-3">
          {!name ? <p className="text-xs text-warn">Enter your name in “Reviewing as” to approve, reject, or edit.</p> : null}
          {showReject ? (
            <div className="space-y-2">
              <label htmlFor="reason" className="text-xs text-fg-dim">Reason (optional)</label>
              <input id="reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000}
                className="w-full rounded-lg border border-line-strong bg-panel px-3 py-2 text-sm focus:border-brand focus:outline-none" />
            </div>
          ) : null}
          <div className="flex flex-wrap justify-end gap-3">
            {editing ? (
              <>
                <Button variant="ghost" onClick={() => { setEditing(false); setDraft(message.message_content); }}>Cancel</Button>
                <Button variant="primary" loading={busy === "save"} disabled={!name || !draft.trim()}
                  onClick={() => run("save", async () => {
                    await apiPut(`/messages/${id}`, MessageSchema, { message_content: draft, editor: name });
                    return { tone: "success", text: "Changes saved. The message is still pending." };
                  })}>
                  Save changes
                </Button>
              </>
            ) : showReject ? (
              <>
                <Button variant="ghost" onClick={() => setShowReject(false)}>Cancel</Button>
                <Button variant="danger" loading={busy === "reject"} disabled={!name}
                  onClick={() => run("reject", async () => {
                    await apiPost(`/messages/${id}/reject`, MessageSchema, { reviewer: name, reason: reason.trim() || null });
                    return { tone: "success", text: "Message rejected." };
                  })}>
                  <X className="size-4" aria-hidden /> Confirm reject
                </Button>
              </>
            ) : (
              <>
                <Button onClick={() => setEditing(true)} disabled={!name}><Pencil className="size-4" aria-hidden /> Edit</Button>
                <Button variant="danger" onClick={() => setShowReject(true)} disabled={!name}><X className="size-4" aria-hidden /> Reject</Button>
                <Button variant="primary" loading={busy === "approve"} disabled={!name}
                  onClick={() => run("approve", async () => {
                    await apiPost(`/messages/${id}/approve`, MessageSchema, { reviewer: name });
                    return { tone: "success", text: "Message approved. It has not been sent yet. Open the Approved tab to send it." };
                  })}>
                  <Check className="size-4" aria-hidden /> Approve
                </Button>
              </>
            )}
          </div>
        </div>
      ) : null}

      {message.status === "approved" ? (
        <div className="mt-6 flex flex-wrap items-center justify-end gap-3">
          {message.send_status === "sent" ? <span className="text-sm text-fg-muted">This message has been sent.</span> : null}
          {!emailChannel ? <span className="text-xs text-warn">Channel “{message.channel}” is not supported yet. Only email can be sent.</span> : null}
          {canSend ? (
            <Button variant="primary" loading={busy === "send"} disabled={!emailChannel}
              onClick={() => run("send", async () => {
                const res = await apiPost(`/messages/${id}/send`, SendResponseSchema, {});
                return {
                  tone: "success",
                  text: res.delivery_mode === "simulated" ? "Sent in simulation mode. No email was actually delivered." : "Message sent.",
                };
              })}>
              <Send className="size-4" aria-hidden /> {message.send_status === "failed" ? "Retry send" : "Send (simulated)"}
            </Button>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}