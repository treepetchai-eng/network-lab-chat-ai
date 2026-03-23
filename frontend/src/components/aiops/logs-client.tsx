"use client";

import Link from "next/link";
import { useEffect, useState, useCallback, useMemo } from "react";
import {
  ChevronLeft, ChevronRight, RefreshCw, Search, X, SlidersHorizontal,
} from "lucide-react";
import { StatusBadge } from "@/components/aiops/status-badge";
import { fetchLogs } from "@/lib/aiops-api";
import type { AIOpsLogsPayload, AIOpsRawLog, AIOpsEvent } from "@/lib/aiops-types";

const PAGE_SIZE = 25;
const POLL_INTERVAL = 30_000;

function relativeTime(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function Pager({ page, total, onChange }: { page: number; total: number; onChange: (p: number) => void }) {
  if (total <= 1) return null;
  return (
    <div className="flex items-center justify-between border-t border-white/[0.06] px-4 py-2.5">
      <span className="text-[0.7rem] text-slate-600">Page {page} of {total}</span>
      <div className="flex items-center gap-1.5">
        <button disabled={page <= 1} onClick={() => onChange(page - 1)}
          className="rounded border border-white/8 bg-white/[0.03] p-1 text-slate-400 transition hover:bg-white/[0.07] disabled:opacity-30">
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
        <button disabled={page >= total} onClick={() => onChange(page + 1)}
          className="rounded border border-white/8 bg-white/[0.03] p-1 text-slate-400 transition hover:bg-white/[0.07] disabled:opacity-30">
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

const SEV_BORDER: Record<string, string> = {
  critical: "border-l-rose-500",
  warning:  "border-l-amber-500",
  info:     "border-l-sky-500/50",
  down:     "border-l-rose-500",
  up:       "border-l-emerald-500",
};

/* ─────────────────────── Raw Logs Tab ─────────────────────────────────── */
function RawLogsTab({ logs, incidentFilter }: { logs: AIOpsRawLog[]; incidentFilter?: string }) {
  const [search, setSearch]       = useState("");
  const [statusFilter, setStatus] = useState("all");
  const [page, setPage]           = useState(1);

  const PARSE_STATUSES = ["all", "ingested", "pending_parse", "llm_decided", "noise"];

  const filtered = useMemo(() => {
    let list = logs;
    if (statusFilter !== "all") list = list.filter(l => l.parse_status === statusFilter);
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(l =>
        l.raw_message.toLowerCase().includes(q) ||
        l.source_ip.toLowerCase().includes(q) ||
        (l.hostname ?? "").toLowerCase().includes(q)
      );
    }
    return list;
  }, [logs, search, statusFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage   = Math.min(page, totalPages);
  const paged      = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  // reset page on filter change
  useEffect(() => { setPage(1); }, [search, statusFilter]);

  return (
    <div>
      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] px-4 py-2.5">
        <div className="relative flex-1 min-w-[180px]">
          <Search className="absolute left-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-slate-600" />
          <input
            type="text" value={search} onChange={e => { setSearch(e.target.value); }}
            placeholder="Search IP, hostname, message…"
            className="w-full rounded border border-white/8 bg-white/[0.04] py-1.5 pl-7 pr-3 text-[0.78rem] text-white placeholder-slate-600 outline-none focus:border-cyan-500/30 focus:ring-1 focus:ring-cyan-500/15"
          />
          {search && (
            <button onClick={() => setSearch("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-600 hover:text-slate-300">
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
        <select value={statusFilter} onChange={e => setStatus(e.target.value)}
          className="rounded border border-white/8 bg-[#0c1220] px-2.5 py-1.5 text-[0.78rem] text-slate-300 outline-none focus:border-cyan-500/30">
          {PARSE_STATUSES.map(s => (
            <option key={s} value={s} className="bg-[#0c1220]">
              {s === "all" ? "All statuses" : s.replaceAll("_", " ")}
            </option>
          ))}
        </select>
        <span className="text-[0.7rem] text-slate-600">{filtered.length} logs</span>
        {incidentFilter && (
          <Link href="/aiops/logs"
            className="inline-flex items-center gap-1 rounded border border-cyan-500/20 bg-cyan-500/[0.08] px-2 py-1 text-[0.7rem] text-cyan-300 hover:bg-cyan-500/[0.14]">
            {incidentFilter} <X className="h-2.5 w-2.5" />
          </Link>
        )}
      </div>

      {/* Table */}
      {paged.length ? (
        <>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-white/[0.06]">
                <tr>
                  {["Time", "Source", "Status", "Message", "Incident"].map(h => (
                    <th key={h} className="px-4 py-2 text-left text-[0.66rem] font-semibold uppercase tracking-widest text-slate-600">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {paged.map(log => (
                  <tr key={`${log.id}-${log.received_at}`} className="group hover:bg-white/[0.02]">
                    <td className="px-4 py-2.5 text-[0.72rem] text-slate-600 whitespace-nowrap">
                      <span title={new Date(log.received_at).toLocaleString()}>
                        {relativeTime(log.received_at)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 font-mono text-[0.72rem] text-slate-400 whitespace-nowrap">
                      <div>{log.source_ip}</div>
                      {log.hostname && <div className="text-slate-600">{log.hostname}</div>}
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <StatusBadge value={log.parse_status} />
                    </td>
                    <td className="px-4 py-2.5 max-w-[420px]">
                      <p className="truncate font-mono text-[0.76rem] text-slate-300 group-hover:whitespace-normal group-hover:break-all">
                        {log.raw_message}
                      </p>
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      {log.incident_no ? (
                        <Link href={`/aiops/incidents/${log.incident_no}`}
                          className="text-[0.72rem] font-semibold text-cyan-400 hover:text-cyan-200">
                          {log.incident_no}
                        </Link>
                      ) : <span className="text-slate-700">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={safePage} total={totalPages} onChange={setPage} />
        </>
      ) : (
        <div className="px-4 py-10 text-center">
          <p className="text-[0.84rem] font-medium text-slate-400">No logs found</p>
          <p className="mt-1 text-[0.76rem] text-slate-600">
            {search || statusFilter !== "all" ? "Adjust your filters." : "Waiting for syslog events."}
          </p>
        </div>
      )}
    </div>
  );
}

/* ─────────────────────── Events Tab ───────────────────────────────────── */
function EventsTab({ events }: { events: AIOpsEvent[] }) {
  const [search, setSearch]         = useState("");
  const [severityFilter, setSev]    = useState("all");
  const [familyFilter, setFamily]   = useState("all");
  const [stateFilter, setState]     = useState("all");
  const [page, setPage]             = useState(1);

  const families  = useMemo(() => ["all", ...Array.from(new Set(events.map(e => e.event_family)))], [events]);
  const states    = useMemo(() => ["all", ...Array.from(new Set(events.map(e => e.event_state)))], [events]);

  const filtered = useMemo(() => {
    let list = events;
    if (severityFilter !== "all") list = list.filter(e => e.severity === severityFilter);
    if (familyFilter   !== "all") list = list.filter(e => e.event_family === familyFilter);
    if (stateFilter    !== "all") list = list.filter(e => e.event_state === stateFilter);
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(e =>
        e.title.toLowerCase().includes(q) ||
        (e.hostname ?? "").toLowerCase().includes(q) ||
        e.correlation_key.toLowerCase().includes(q)
      );
    }
    return list;
  }, [events, search, severityFilter, familyFilter, stateFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage   = Math.min(page, totalPages);
  const paged      = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  useEffect(() => { setPage(1); }, [search, severityFilter, familyFilter, stateFilter]);

  return (
    <div>
      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] px-4 py-2.5">
        <div className="relative flex-1 min-w-[160px]">
          <Search className="absolute left-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-slate-600" />
          <input
            type="text" value={search} onChange={e => { setSearch(e.target.value); }}
            placeholder="Title, hostname, key…"
            className="w-full rounded border border-white/8 bg-white/[0.04] py-1.5 pl-7 pr-3 text-[0.78rem] text-white placeholder-slate-600 outline-none focus:border-cyan-500/30 focus:ring-1 focus:ring-cyan-500/15"
          />
          {search && (
            <button onClick={() => setSearch("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-600 hover:text-slate-300">
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
        {[
          { label: "Severity", value: severityFilter, onChange: setSev,    opts: ["all","critical","warning","info"] },
          { label: "Family",   value: familyFilter,   onChange: setFamily, opts: families },
          { label: "State",    value: stateFilter,    onChange: setState,  opts: states },
        ].map(({ label, value, onChange, opts }) => (
          <select key={label} value={value} onChange={e => onChange(e.target.value)}
            className="rounded border border-white/8 bg-[#0c1220] px-2.5 py-1.5 text-[0.78rem] text-slate-300 outline-none focus:border-cyan-500/30">
            {opts.map(o => (
              <option key={o} value={o} className="bg-[#0c1220]">
                {o === "all" ? `All ${label.toLowerCase()}s` : o.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        ))}
        <span className="text-[0.7rem] text-slate-600">{filtered.length} events</span>
      </div>

      {/* Table */}
      {paged.length ? (
        <>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-white/[0.06]">
                <tr>
                  {["Time", "Device", "Family", "State", "Severity", "Title", "Incident"].map(h => (
                    <th key={h} className="px-4 py-2 text-left text-[0.66rem] font-semibold uppercase tracking-widest text-slate-600">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {paged.map(ev => (
                  <tr key={`${ev.id}-${ev.created_at}`}
                    className={`border-l-2 hover:bg-white/[0.02] ${SEV_BORDER[ev.event_state] ?? SEV_BORDER[ev.severity] ?? "border-l-transparent"}`}>
                    <td className="px-4 py-2.5 text-[0.72rem] text-slate-600 whitespace-nowrap">
                      <span title={new Date(ev.created_at).toLocaleString()}>
                        {relativeTime(ev.created_at)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 font-mono text-[0.72rem] text-slate-400 whitespace-nowrap">
                      {ev.hostname ?? "—"}
                    </td>
                    <td className="px-4 py-2.5 text-[0.72rem] text-slate-500 whitespace-nowrap">
                      {ev.event_family.replaceAll("_", " ")}
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <StatusBadge value={ev.event_state} />
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <StatusBadge value={ev.severity} showDot />
                    </td>
                    <td className="px-4 py-2.5 max-w-[320px]">
                      <p className="truncate text-[0.78rem] font-medium text-slate-200">{ev.title}</p>
                      {ev.summary && (
                        <p className="mt-0.5 truncate text-[0.7rem] text-slate-600">{ev.summary}</p>
                      )}
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      {ev.incident_no ? (
                        <Link href={`/aiops/incidents/${ev.incident_no}`}
                          className="text-[0.72rem] font-semibold text-cyan-400 hover:text-cyan-200">
                          {ev.incident_no}
                        </Link>
                      ) : <span className="text-slate-700">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={safePage} total={totalPages} onChange={setPage} />
        </>
      ) : (
        <div className="px-4 py-10 text-center">
          <p className="text-[0.84rem] font-medium text-slate-400">No events found</p>
          <p className="mt-1 text-[0.76rem] text-slate-600">
            {search || severityFilter !== "all" || familyFilter !== "all" || stateFilter !== "all"
              ? "Adjust your filters." : "Waiting for parsed events."}
          </p>
        </div>
      )}
    </div>
  );
}

/* ─────────────────────── Main Component ───────────────────────────────── */
export function LogsClient({
  initialPayload,
  incidentFilter,
}: {
  initialPayload: AIOpsLogsPayload;
  incidentFilter?: string;
}) {
  const [payload, setPayload]     = useState(initialPayload);
  const [refreshing, setRefreshing] = useState(false);
  const [tab, setTab]             = useState<"raw" | "events">("raw");

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try { setPayload(await fetchLogs(incidentFilter)); }
    catch { /* ignore */ }
    finally { setRefreshing(false); }
  }, [incidentFilter]);

  useEffect(() => {
    const id = setInterval(refresh, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [refresh]);

  const tabs: { key: "raw" | "events"; label: string; count: number }[] = [
    { key: "raw",    label: "Raw Logs",         count: payload.raw_logs.length },
    { key: "events", label: "Normalized Events", count: payload.events.length },
  ];

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <nav className="flex items-center gap-1.5 text-[0.76rem] text-slate-500">
            <Link href="/aiops" className="hover:text-cyan-300">Dashboard</Link>
            <span>/</span>
            <span className="text-slate-300">Logs</span>
          </nav>
          <h1 className="mt-0.5 text-lg font-semibold text-slate-100">Log Feed</h1>
        </div>
        <div className="flex items-center gap-2">
          <SlidersHorizontal className="h-3.5 w-3.5 text-slate-600" />
          <span className="text-[0.72rem] text-slate-600">
            Auto-refresh 30s
          </span>
          <button onClick={refresh} disabled={refreshing}
            className="inline-flex items-center gap-1.5 rounded border border-white/8 bg-white/[0.04] px-2.5 py-1.5 text-[0.73rem] text-slate-400 transition hover:border-white/14 hover:text-slate-200 disabled:opacity-40">
            <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Card */}
      <div className="overflow-hidden rounded-xl border border-white/8 bg-white/[0.03]">
        {/* Tabs */}
        <div className="flex items-center gap-0 border-b border-white/[0.07]">
          {tabs.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-2 px-5 py-3 text-[0.78rem] font-medium transition border-b-2 -mb-px ${
                tab === t.key
                  ? "border-cyan-400 text-cyan-300"
                  : "border-transparent text-slate-500 hover:text-slate-300"
              }`}
            >
              {t.label}
              <span className={`rounded px-1.5 py-0.5 text-[0.65rem] font-semibold ${
                tab === t.key ? "bg-cyan-500/15 text-cyan-300" : "bg-white/[0.05] text-slate-500"
              }`}>
                {t.count}
              </span>
            </button>
          ))}
        </div>

        {/* Tab content */}
        {tab === "raw"
          ? <RawLogsTab logs={payload.raw_logs} incidentFilter={incidentFilter} />
          : <EventsTab events={payload.events} />
        }
      </div>
    </div>
  );
}
