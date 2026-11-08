"use client";

import { Activity, Bell, ChevronLeft, ChevronRight, Inbox, LayoutDashboard, Search, ShieldCheck, Users } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useApi } from "@/hooks/useApi";
import { apiGet } from "@/lib/api";
import { HealthSchema, OverviewSchema } from "@/lib/schemas";
import { Badge, cn } from "./ui";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/leads", label: "Leads", icon: Users },
  { href: "/approvals", label: "Approvals", icon: Inbox },
  { href: "/analytics", label: "Analytics", icon: Activity },
];

function LogoMark() {
  return (
    <svg viewBox="0 0 32 32" className="size-8" aria-hidden="true">
      <defs>
        <linearGradient id="logo-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#7aa2ff" />
          <stop offset="1" stopColor="#3d6bf5" />
        </linearGradient>
      </defs>
      <circle cx="12" cy="16" r="9" fill="url(#logo-g)" />
      <circle cx="21" cy="16" r="8" fill="none" stroke="#a78bfa" strokeWidth="3" />
    </svg>
  );
}

export default function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const searchRef = useRef<HTMLInputElement>(null);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState(false);

  // Health + pending-approval count; refetched on every navigation so the bell stays current.
  const fetchShell = useCallback(
    async (signal: AbortSignal) => {
      void pathname;
      const health = await apiGet("/health", HealthSchema, { signal, acceptStatuses: [503] });
      let pending: number | null = null;
      try {
        pending = (await apiGet("/analytics/overview", OverviewSchema, { signal })).approvals.pending;
      } catch (err) {
        if (signal.aborted) throw err;
      }
      return { health, pending };
    },
    [pathname],
  );
  const { data, error, loading } = useApi(fetchShell);

  // Remember whether the rail is expanded.
  useEffect(() => {
    try {
      if (localStorage.getItem("rail-expanded") === "1") setExpanded(true);
    } catch { /* storage unavailable: keep default */ }
  }, []);
  function toggleRail() {
    setExpanded((value) => {
      try { localStorage.setItem("rail-expanded", value ? "0" : "1"); } catch { /* ignore */ }
      return !value;
    });
  }

  // "/" focuses the search box.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const el = event.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      event.preventDefault();
      searchRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    const q = search.trim();
    router.push(q ? `/leads?q=${encodeURIComponent(q)}` : "/leads");
  }

  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));
  const pending = data?.pending ?? 0;
  const simulated = data?.health.communication_provider === "mock";

  let status: ReactNode;
  if (loading && !data) status = <Badge>Checking…</Badge>;
  else if (error || !data) status = <Badge tone="danger" dot>Backend unreachable</Badge>;
  else if (data.health.status === "ok") status = <Badge tone="success" dot>Online</Badge>;
  else {
    const reason = data.health.database !== "ok" ? data.health.database.replace("_", " ") : "schema not initialized";
    status = <Badge tone="warning" dot>Degraded: {reason}</Badge>;
  }

  return (
    <div className="min-h-screen">
      <a href="#content" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-brand-strong focus:px-3 focus:py-2 focus:text-white">
        Skip to main content
      </a>

      <header className="sticky top-0 z-30 border-b border-line bg-canvas/75 backdrop-blur-xl">
        <div className="mx-auto flex w-full max-w-[1500px] flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3 lg:px-8">
          <Link href="/" className="flex items-center gap-3">
            <LogoMark />
            <span className="text-lg font-semibold tracking-tight">AI Sales Recovery</span>
          </Link>

          <nav aria-label="Primary" className="order-last -mx-1 flex w-full gap-1 overflow-x-auto px-1 md:order-none md:mx-0 md:ml-4 md:w-auto md:px-0">
            {NAV.map(({ href, label }) => {
              const active = isActive(href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "whitespace-nowrap rounded-full px-4 py-1.5 text-sm font-medium transition-colors",
                    active
                      ? "bg-[linear-gradient(180deg,#5b8cff,#3d5af1)] text-white shadow-[0_0_22px_-4px_rgba(91,140,255,0.85)]"
                      : "text-fg-muted hover:bg-white/5 hover:text-fg",
                  )}
                >
                  {label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <form role="search" onSubmit={submitSearch} className="relative hidden sm:block">
              <label htmlFor="global-search" className="sr-only">Search leads</label>
              <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-fg-dim" aria-hidden />
              <input
                id="global-search" ref={searchRef} value={search} onChange={(e) => setSearch(e.target.value)}
                placeholder="Search leads…" autoComplete="off"
                className="h-10 w-56 rounded-full border border-line-strong bg-white/5 pl-10 pr-9 text-sm placeholder:text-fg-dim focus:border-brand focus:outline-none lg:w-72"
              />
              <kbd className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rounded border border-line-strong px-1.5 text-[11px] text-fg-dim" aria-hidden>/</kbd>
            </form>

            <Link
              href="/approvals"
              aria-label={`Approval Center, ${pending} pending`}
              className="relative grid size-10 place-items-center rounded-full border border-line-strong bg-white/5 text-fg-muted transition-colors hover:bg-white/10 hover:text-fg"
            >
              <Bell className="size-4" aria-hidden />
              {pending > 0 ? (
                <span className="absolute -right-1 -top-1 grid min-w-5 place-items-center rounded-full bg-bad px-1 text-[11px] font-semibold leading-5 text-canvas">
                  {pending > 99 ? "99+" : pending}
                </span>
              ) : null}
            </Link>

            <div className="hidden items-center gap-2 md:flex">
              {simulated ? <Badge tone="info" dot title="Messages go to a mock provider. No customer is contacted.">Simulated sending</Badge> : null}
              {status}
            </div>
          </div>
        </div>
      </header>

      <div className="mx-auto flex w-full max-w-[1500px] items-start gap-5 px-4 pb-12 pt-6 lg:px-8">
        <aside
          aria-label="Sidebar"
          className={cn(
            "sticky top-24 hidden h-fit shrink-0 flex-col gap-2 rounded-[28px] border border-line bg-panel/70 p-2 backdrop-blur-xl transition-[width] duration-200 lg:flex",
            expanded ? "w-52" : "w-[60px]",
          )}
        >
          <button
            type="button" onClick={toggleRail} aria-expanded={expanded}
            aria-label={expanded ? "Collapse sidebar" : "Expand sidebar"}
            className="grid size-11 place-items-center rounded-full border border-line-strong text-fg-muted transition-colors hover:bg-white/10 hover:text-fg"
          >
            {expanded ? <ChevronLeft className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
          </button>

          <nav className="flex flex-col gap-2">
            {NAV.map(({ href, label, icon: Icon }) => {
              const active = isActive(href);
              return (
                <Link
                  key={href} href={href} aria-current={active ? "page" : undefined} aria-label={label}
                  className={cn("group relative flex items-center gap-3 rounded-full transition-colors", expanded && "pr-4 hover:bg-white/5")}
                >
                  <span
                    className={cn(
                      "grid size-11 shrink-0 place-items-center rounded-full border transition-colors",
                      active
                        ? "border-brand/60 bg-brand/15 text-brand shadow-[0_0_18px_-2px_rgba(91,140,255,0.7)]"
                        : "border-line-strong text-fg-muted group-hover:bg-white/10 group-hover:text-fg",
                    )}
                  >
                    <Icon className="size-[18px]" aria-hidden />
                  </span>
                  {expanded ? <span className={cn("text-sm font-medium", active ? "text-fg" : "text-fg-muted")}>{label}</span> : null}
                  {!expanded ? (
                    <span role="tooltip" className="pointer-events-none absolute left-full top-1/2 z-40 ml-3 -translate-y-1/2 whitespace-nowrap rounded-md border border-line-strong bg-raised px-2 py-1 text-xs opacity-0 shadow-lg transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
                      {label}
                    </span>
                  ) : null}
                </Link>
              );
            })}
          </nav>

          <div
            className="mt-2 flex items-center gap-3 border-t border-line pt-3"
            title={simulated ? "Simulated sending: no customer is contacted" : "Live sending"}
          >
            <span className={cn("grid size-11 shrink-0 place-items-center rounded-full border border-line-strong", simulated ? "text-info" : "text-ok")}>
              <ShieldCheck className="size-[18px]" aria-hidden />
            </span>
            {expanded ? <span className="text-xs leading-tight text-fg-muted">{data ? (simulated ? "Simulated sending" : "Live sending") : "Checking…"}</span> : <span className="sr-only">{simulated ? "Simulated sending" : "Live sending"}</span>}
          </div>
        </aside>

        <main id="content" tabIndex={-1} className="min-w-0 flex-1 outline-none">
          {children}
        </main>
      </div>
    </div>
  );
}