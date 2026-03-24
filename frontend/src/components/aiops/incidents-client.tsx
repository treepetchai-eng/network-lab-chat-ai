"use client";

import Link from "next/link";
import { useEffect, useState, useCallback, useMemo } from "react";
import { RefreshCw, Search, ChevronLeft, ChevronRight, ArrowUpDown, AlertCircle } from "lucide-react";
import { ResetIncidentsButton } from "@/components/aiops/reset-incidents-button";
import { SectionCard } from "@/components/aiops/section-card";
import { StatusBadge } from "@/components/aiops/status-badge";
import { fetchIncidents } from "@/lib/aiops-api";
import type { AIOpsIncident } from "@/lib/aiops-types";

const PAGE_SIZE = 20;
const POLL_INTERVAL = 30_000;

const STATUS_OPTIONS = [
  "all","new","triaged","investigating","active","recovering",
  "monitoring","awaiting_approval","approved","executing","verifying","escalated","reopened",
] as const;
const SEVERITY_OPTIONS = ["all","critical","warning","info"] as const;

type SortField = "last_seen_at" | "severity" | "status" | "event_count";
type SortDir   = "asc" | "desc";

const SEVERITY_RANK: Record<string, number> = { critical: 3, warning: 2, info: 1 };

const SEV_ROW: Record<string, string> = {
  critical: "border-l-rose-500",
  warning:  "border-l-amber-500",
  info:     "border-l-sky-500/50",
};

function sortIncidents(list: AIOpsIncident[], field: SortField, dir: SortDir) {
  return [...list].sort((a, b) => {
    let cmp = 0;
    switch (field) {
      case "last_seen_at": cmp = new Date(a.last_seen_at).getTime() - new Date(b.last_seen_at).getTime(); break;
      case "severity":     cmp = (SEVERITY_RANK[a.severity] ?? 0) - (SEVERITY_RANK[b.severity] ?? 0); break;
      case "status":       cmp = a.status.localeCompare(b.status); break;
      case "event_count":  cmp = a.event_count - b.event_count; break;
    }
    return dir === "desc" ? -cmp : cmp;
  });
}

function relativeTime(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function IncidentsClient({ initialIncidents }: { initialIncidents: AIOpsIncident[] }) {
  const [incidents, setIncidents]       = useState(initialIncidents);
  const [refreshing, setRefreshing]     = useState(false);
  const [fetchError, setFetchError]     = useState<string | null>(null);
  const [search, setSearch]             = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [severityFilter, setSeverityFilter] = useState<string>("all");
  const [sortField, setSortField]       = useState<SortField>("last_seen_at");
  const [sortDir, setSortDir]           = useState<SortDir>("desc");
  const [page, setPage]                 = useState(1);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      setIncidents(await fetchIncidents());
      setFetchError(null);
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : "Failed to refresh incidents");
    } finally { setRefreshing(false); }
  }, []);

  useEffect(() => {
    const id = setInterval(refresh, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [refresh]);

  const filtered = useMemo(() => {
    let list = incidents;
    if (statusFilter   !== "all") list = list.filter((i) => i.status   === statusFilter);
    if (severityFilter !== "all") list = list.filter((i) => i.severity === severityFilter);
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter((i) =>
        i.incident_no.toLowerCase().includes(q) ||
        i.title.toLowerCase().includes(q) ||
        (i.primary_hostname ?? "").toLowerCase().includes(q) ||
        i.primary_source_ip.toLowerCase().includes(q),
      );
    }
    return sortIncidents(list, sortField, sortDir);
  }, [incidents, statusFilter, severityFilter, search, sortField, sortDir]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage   = Math.min(page, totalPages);
  const paged      = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  useEffect(() => { setPage(1); }, [search, statusFilter, severityFilter]);

  const toggleSort = (field: SortField) => {
    if (sortField === field) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortField(field); setSortDir("desc"); }
  };

  const SortTh = ({ field, children }: { field: SortField; children: React.ReactNode }) => (
    <th
      className="cursor-pointer select-none px-4 py-2.5 text-left text-[0.68rem] font-semibold uppercase tracking-widest text-slate-600"
      onClick={() => toggleSort(field)}
    >
      <span className="inline-flex items-center gap-1">
        {children}
        <ArrowUpDown className={`h-2.5 w-2.5 ${sortField === field ? "text-cyan-400" : "text-slate-700"}`} />
      </span>
    </th>
  );

  return (
    <SectionCard
      title="Incident Queue"
      eyebrow="Active"
      noPadding
      actions={
        <div className="flex items-center gap-2">
          <button
            onClick={refresh}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 rounded border border-white/8 bg-white/[0.04] px-2.5 py-1.5 text-[0.72rem] text-slate-400 transition hover:border-white/14 hover:text-slate-200 disabled:opacity-40"
          >
            <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <ResetIncidentsButton />
        </div>
      }
    >
      {fetchError && (
        <div className="flex items-center gap-2 border-b border-rose-500/20 bg-rose-500/[0.06] px-4 py-2.5">
          <AlertCircle className="h-3.5 w-3.5 shrink-0 text-rose-400" />
          <p className="text-[0.76rem] text-rose-300">Refresh failed: {fetchError} — showing cached data</p>
        </div>
      )}
      {/* Filter bar */}
      <div className="grid gap-2.5 border-b border-white/[0.07] px-4 py-3 md:grid-cols-3">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-slate-600" />
          <input
            type="text"
            placeholder="Search incidents, IPs, devices…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded border border-white/8 bg-white/[0.04] py-1.5 pl-7 pr-3 text-[0.8rem] text-white placeholder-slate-600 outline-none transition focus:border-cyan-500/30 focus:ring-1 focus:ring-cyan-500/15"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded border border-white/8 bg-[#0c1220] px-3 py-1.5 text-[0.8rem] text-slate-300 outline-none transition focus:border-cyan-500/30"
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s} className="bg-[#0c1220]">
              {s === "all" ? "All statuses" : s.replaceAll("_", " ")}
            </option>
          ))}
        </select>
        <select
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value)}
          className="rounded border border-white/8 bg-[#0c1220] px-3 py-1.5 text-[0.8rem] text-slate-300 outline-none transition focus:border-cyan-500/30"
        >
          {SEVERITY_OPTIONS.map((s) => (
            <option key={s} value={s} className="bg-[#0c1220]">
              {s === "all" ? "All severities" : s}
            </option>
          ))}
        </select>
      </div>

      {/* Table */}
      {paged.length ? (
        <>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-white/[0.07]">
                <tr>
                  <th className="px-4 py-2.5 text-left text-[0.68rem] font-semibold uppercase tracking-widest text-slate-600">Incident</th>
                  <SortTh field="severity">Sev</SortTh>
                  <SortTh field="status">Status</SortTh>
                  <th className="px-4 py-2.5 text-left text-[0.68rem] font-semibold uppercase tracking-widest text-slate-600">Device</th>
                  <th className="hidden px-4 py-2.5 text-left text-[0.68rem] font-semibold uppercase tracking-widest text-slate-600 xl:table-cell">Family</th>
                  <SortTh field="event_count">Events</SortTh>
                  <SortTh field="last_seen_at">Last Seen</SortTh>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {paged.map((inc) => (
                  <tr
                    key={inc.incident_no}
                    className={`border-l-2 transition hover:bg-white/[0.03] ${SEV_ROW[inc.severity] ?? "border-l-transparent"}`}
                  >
                    <td className="px-4 py-3">
                      <Link href={`/aiops/incidents/${inc.incident_no}`} className="block">
                        <p className="text-[0.65rem] font-semibold uppercase tracking-widest text-slate-600">{inc.incident_no}</p>
                        <p className="mt-0.5 text-[0.84rem] font-semibold text-slate-100">{inc.title}</p>
                        {inc.summary && (
                          <p className="mt-0.5 max-w-lg truncate text-[0.75rem] text-slate-500">{inc.summary}</p>
                        )}
                      </Link>
                    </td>
                    <td className="px-4 py-3"><StatusBadge value={inc.severity} showDot /></td>
                    <td className="px-4 py-3"><StatusBadge value={inc.status} /></td>
                    <td className="px-4 py-3 font-mono text-[0.78rem] text-slate-400">{inc.primary_hostname ?? inc.primary_source_ip}</td>
                    <td className="hidden px-4 py-3 text-[0.78rem] text-slate-500 xl:table-cell">{inc.event_family}</td>
                    <td className="px-4 py-3 text-center text-[0.78rem] text-slate-500">{inc.event_count}</td>
                    <td className="px-4 py-3 text-[0.75rem] text-slate-600">{relativeTime(inc.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between border-t border-white/[0.07] px-4 py-3">
            <p className="text-[0.72rem] text-slate-600">{filtered.length} incident{filtered.length !== 1 ? "s" : ""}</p>
            <div className="flex items-center gap-2">
              <button
                disabled={safePage <= 1}
                onClick={() => setPage((p) => p - 1)}
                className="rounded border border-white/8 bg-white/[0.03] p-1.5 text-slate-400 transition hover:bg-white/[0.07] disabled:opacity-30"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <span className="text-[0.72rem] text-slate-500">{safePage} / {totalPages}</span>
              <button
                disabled={safePage >= totalPages}
                onClick={() => setPage((p) => p + 1)}
                className="rounded border border-white/8 bg-white/[0.03] p-1.5 text-slate-400 transition hover:bg-white/[0.07] disabled:opacity-30"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        </>
      ) : (
        <div className="px-4 py-12 text-center">
          <p className="text-[0.86rem] font-medium text-slate-400">No incidents found</p>
          <p className="mt-1 text-[0.78rem] text-slate-600">
            {search || statusFilter !== "all" || severityFilter !== "all"
              ? "Adjust your filters to see more results."
              : "No active incidents. The system is monitoring syslog."}
          </p>
        </div>
      )}
    </SectionCard>
  );
}
