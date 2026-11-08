"use client";

import { ArrowDown, ArrowUp, Search, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import ImportLeads from "@/components/ImportLeads";
import LeadDrawer from "@/components/LeadDrawer";
import {
  Badge, Button, Card, ErrorBanner, Notice, PageHeader, PriorityBadge, ScoreMeter, Skeleton, cn,
} from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { apiPost } from "@/lib/api";
import { formatCurrency, formatNumber } from "@/lib/format";
import { fetchAllLeads } from "@/lib/leads";
import { AnalysisSchema, type LeadSummary } from "@/lib/schemas";

type SortKey = "name" | "company" | "industry" | "deal_value" | "stage_lost_at" | "days_inactive" | "recovery_score";
const COLUMNS: { key: SortKey; label: string; right?: boolean }[] = [
  { key: "name", label: "Customer" },
  { key: "company", label: "Company" },
  { key: "industry", label: "Industry" },
  { key: "deal_value", label: "Deal value", right: true },
  { key: "stage_lost_at", label: "Stage lost at" },
  { key: "days_inactive", label: "Inactive", right: true },
  { key: "recovery_score", label: "Recovery score" },
];
const PAGE_SIZE = 25;
const EMPTY: LeadSummary[] = [];
const uniq = (values: Array<string | null | undefined>) =>
  Array.from(new Set(values.filter((v): v is string => !!v))).sort();

const selectClass =
  "rounded-lg border border-line-strong bg-panel px-3 py-2 text-sm text-fg focus:border-brand focus:outline-none";

export default function LeadsPage() {
  const { data, error, loading, reload } = useApi(fetchAllLeads);
  const leads = data ?? EMPTY;

  const [query, setQuery] = useState("");
  const [industry, setIndustry] = useState("");
  const [plan, setPlan] = useState("");
  const [priority, setPriority] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("recovery_score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(0);
  const [openId, setOpenId] = useState<number | null>(null);
  const [bulk, setBulk] = useState<{ running: boolean; done: number; total: number; failed: number } | null>(null);

  const industries = useMemo(() => uniq(leads.map((l) => l.industry)), [leads]);
  const plans = useMemo(() => uniq(leads.map((l) => l.plan_interested)), [leads]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = leads.filter((l) => {
      if (industry && l.industry !== industry) return false;
      if (plan && l.plan_interested !== plan) return false;
      if (priority && (l.priority ?? "none") !== priority) return false;
      if (!q) return true;
      return [l.name, l.email, l.company, l.industry, l.crm_notes].some((v) => v?.toLowerCase().includes(q));
    });
    return filtered.sort((a, b) => {
      const av = a[sortKey] as string | number | null | undefined;
      const bv = b[sortKey] as string | number | null | undefined;
      if (av == null && bv == null) return 0;
      if (av == null) return 1; // empty values always last
      if (bv == null) return -1;
      const cmp = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [leads, query, industry, plan, priority, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const visible = rows.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);
  const unscored = leads.filter((l) => l.recovery_score == null).length;
  const filtersActive = !!(query || industry || plan || priority);

  function toggleSort(key: SortKey) {
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir(key === "name" || key === "company" ? "asc" : "desc"); }
    setPage(0);
  }

  function resetFilters() { setQuery(""); setIndustry(""); setPlan(""); setPriority(""); setPage(0); }

  async function analyzeUnscored() {
    const targets = leads.filter((l) => l.recovery_score == null);
    let failed = 0;
    setBulk({ running: true, done: 0, total: targets.length, failed: 0 });
    for (let i = 0; i < targets.length; i++) {
      try { await apiPost(`/leads/${targets[i].id}/analyze`, AnalysisSchema, {}); } catch { failed++; }
      setBulk({ running: true, done: i + 1, total: targets.length, failed });
    }
    setBulk({ running: false, done: targets.length, total: targets.length, failed });
    reload();
  }

  return (
    <>
      <PageHeader
        title="Leads"
        description="Lost-deal customers imported from your CRM."
        actions={
          <Button
            variant="primary"
            onClick={analyzeUnscored}
            loading={bulk?.running ?? false}
            disabled={unscored === 0 || loading}
          >
            <Sparkles className="size-4" aria-hidden />
            {bulk?.running ? `Analyzing ${bulk.done}/${bulk.total}` : `Analyze unscored leads (${unscored})`}
          </Button>
        }
      />

      {bulk && !bulk.running ? (
        <div className="mb-4">
          <Notice tone={bulk.failed ? "danger" : "success"} onClose={() => setBulk(null)}>
            Analyzed {bulk.done - bulk.failed} of {bulk.total} leads{bulk.failed ? `, ${bulk.failed} failed` : ""}.
          </Notice>
        </div>
      ) : null}

      {loading && !data ? (
        <div aria-busy="true" className="space-y-3"><p className="sr-only" role="status">Loading leads…</p><Skeleton className="h-14" />{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-12" />)}</div>
      ) : error ? (
        <ErrorBanner error={error} onRetry={reload} />
      ) : leads.length === 0 ? (
        <ImportLeads onImported={reload} />
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <label className="relative min-w-[240px] flex-1">
              <span className="sr-only">Search leads</span>
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-dim" aria-hidden />
              <input
                type="search" value={query} placeholder="Search name, company, industry, notes…"
                onChange={(e) => { setQuery(e.target.value); setPage(0); }}
                className="w-full rounded-lg border border-line-strong bg-panel py-2 pl-9 pr-3 text-sm placeholder:text-fg-dim focus:border-brand focus:outline-none"
              />
            </label>
            <label><span className="sr-only">Filter by industry</span>
              <select className={selectClass} value={industry} onChange={(e) => { setIndustry(e.target.value); setPage(0); }}>
                <option value="">All industries</option>
                {industries.map((v) => <option key={v}>{v}</option>)}
              </select>
            </label>
            <label><span className="sr-only">Filter by plan</span>
              <select className={selectClass} value={plan} onChange={(e) => { setPlan(e.target.value); setPage(0); }}>
                <option value="">All plans</option>
                {plans.map((v) => <option key={v}>{v}</option>)}
              </select>
            </label>
            <label><span className="sr-only">Filter by priority</span>
              <select className={selectClass} value={priority} onChange={(e) => { setPriority(e.target.value); setPage(0); }}>
                <option value="">All priorities</option>
                <option>High</option><option>Medium</option><option>Low</option>
                <option value="none">Not analyzed</option>
              </select>
            </label>
            {filtersActive ? <Button variant="ghost" size="sm" onClick={resetFilters}>Clear filters</Button> : null}
          </div>

          <p className="text-xs text-fg-dim" aria-live="polite">
            Showing {formatNumber(rows.length)} of {formatNumber(leads.length)} leads
          </p>

          <Card className="overflow-x-auto">
            <table className="w-full min-w-[1100px] text-sm">
              <caption className="sr-only">Lost-deal leads. Column headers are sortable.</caption>
              <thead className="text-left text-xs uppercase tracking-wider text-fg-dim">
                <tr>
                  {COLUMNS.map((c) => {
                    const active = sortKey === c.key;
                    return (
                      <th key={c.key} scope="col" aria-sort={active ? (sortDir === "asc" ? "ascending" : "descending") : "none"} className={cn("px-4 py-3 font-medium", c.right && "text-right")}>
                        <button type="button" onClick={() => toggleSort(c.key)} className={cn("inline-flex items-center gap-1 uppercase tracking-wider hover:text-fg", active && "text-brand")}>
                          {c.label}
                          {active ? (sortDir === "asc" ? <ArrowUp className="size-3" aria-hidden /> : <ArrowDown className="size-3" aria-hidden />) : null}
                        </button>
                      </th>
                    );
                  })}
                  <th scope="col" className="px-4 py-3 font-medium">Priority</th>
                  <th scope="col" className="px-4 py-3 font-medium">CRM notes</th>
                </tr>
              </thead>
              <tbody>
                {visible.length === 0 ? (
                  <tr><td colSpan={9} className="px-4 py-10 text-center text-fg-muted">No leads match your filters.</td></tr>
                ) : visible.map((l) => (
                  <tr key={l.id} onClick={() => setOpenId(l.id)} className="cursor-pointer border-t border-line/70 transition-colors hover:bg-white/[0.04]">
                    <th scope="row" className="px-4 py-3 text-left font-normal">
                      <button type="button" onClick={(e) => { e.stopPropagation(); setOpenId(l.id); }} className="text-left">
                        <span className="block font-medium hover:text-brand">{l.name}</span>
                        <span className="block text-xs text-fg-dim">#{l.id} · {l.email}</span>
                      </button>
                      {l.opted_out ? <Badge tone="danger" className="mt-1">Opted out</Badge> : null}
                    </th>
                    <td className="px-4 py-3 text-fg-muted">{l.company}</td>
                    <td className="px-4 py-3 text-fg-muted">{l.industry ?? "—"}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{formatCurrency(l.deal_value)}</td>
                    <td className="px-4 py-3 text-fg-muted">{l.stage_lost_at ?? "—"}</td>
                    <td className="px-4 py-3 text-right tabular-nums text-fg-muted">{l.days_inactive}d</td>
                    <td className="px-4 py-3"><ScoreMeter score={l.recovery_score} /></td>
                    <td className="px-4 py-3"><PriorityBadge priority={l.priority} /></td>
                    <td className="max-w-[260px] px-4 py-3"><p className="truncate text-fg-muted" title={l.crm_notes ?? undefined}>{l.crm_notes ?? "—"}</p></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <div className="flex items-center justify-between text-sm text-fg-muted">
            <span>Page {safePage + 1} of {pageCount}</span>
            <div className="flex gap-2">
              <Button size="sm" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>Previous</Button>
              <Button size="sm" disabled={safePage >= pageCount - 1} onClick={() => setPage(safePage + 1)}>Next</Button>
            </div>
          </div>
        </div>
      )}

      {openId !== null ? <LeadDrawer key={openId} leadId={openId} onClose={() => setOpenId(null)} onChanged={reload} /> : null}
    </>
  );
}