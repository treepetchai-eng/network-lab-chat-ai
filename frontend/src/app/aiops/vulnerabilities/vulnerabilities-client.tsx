"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ChevronRight,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  ShieldOff,
  Wifi,
} from "lucide-react";
import { fetchVulnerabilitySummary, triggerScanAll, triggerVulnScan } from "@/lib/aiops-api";
import type { AIOpsDeviceVulnRow, AIOpsVulnSummaryPayload } from "@/lib/aiops-types";
import { SectionCard } from "@/components/aiops/section-card";

/* ─── Severity helpers ──────────────────────────────────────────────────── */

const SIR_STYLE: Record<string, string> = {
  Critical: "text-rose-300",
  High:     "text-orange-300",
  Medium:   "text-amber-300",
  Low:      "text-sky-300",
};

function RiskBar({ device }: { device: AIOpsDeviceVulnRow }) {
  const c = device.critical_count ?? 0;
  const h = device.high_count    ?? 0;
  const m = device.medium_count  ?? 0;
  const l = device.low_count     ?? 0;
  const total = c + h + m + l;
  if (total === 0) return <span className="text-[0.72rem] text-emerald-400">Clean</span>;

  const segs = [
    { count: c, color: "bg-rose-500"   },
    { count: h, color: "bg-orange-500" },
    { count: m, color: "bg-amber-400"  },
    { count: l, color: "bg-sky-400"    },
  ].filter((s) => s.count > 0);

  return (
    <div className="flex h-2 w-full max-w-[72px] overflow-hidden rounded-full bg-white/10">
      {segs.map(({ count, color }) => (
        <div
          key={color}
          className={color}
          style={{ width: `${(count / total) * 100}%` }}
        />
      ))}
    </div>
  );
}

function SevCell({ n, style }: { n: number | null | undefined; style: string }) {
  if (!n) return <span className="text-[0.78rem] text-slate-700">—</span>;
  return <span className={`text-[0.82rem] font-bold ${style}`}>{n}</span>;
}

function DeviceRiskIcon({ device }: { device: AIOpsDeviceVulnRow }) {
  if (!device.scan_id) return <ShieldOff className="h-4 w-4 text-slate-600" />;
  if ((device.critical_count ?? 0) > 0) return <ShieldAlert className="h-4 w-4 text-rose-400" />;
  if ((device.high_count ?? 0) > 0) return <ShieldAlert className="h-4 w-4 text-orange-400" />;
  return <ShieldCheck className="h-4 w-4 text-emerald-400" />;
}

/* ─── KPI card ─────────────────────────────────────────────────────────── */
function KpiCard({
  label, value, sub, accent,
}: { label: string; value: number | string; sub?: string; accent?: string }) {
  return (
    <div className="rounded-[0.95rem] border border-white/8 bg-white/[0.03] px-4 py-4">
      <p className="text-[0.62rem] font-semibold uppercase tracking-[0.14em] text-slate-500">{label}</p>
      <p className={`mt-1.5 text-3xl font-bold ${accent ?? "text-white"}`}>{value}</p>
      {sub && <p className="mt-0.5 text-[0.7rem] text-slate-600">{sub}</p>}
    </div>
  );
}

/* ─── Scanning overlay row ─────────────────────────────────────────────── */
function ScanningRow({ hostname }: { hostname: string }) {
  return (
    <tr className="animate-pulse border-b border-white/[0.05]">
      <td colSpan={8} className="px-4 py-3">
        <div className="flex items-center gap-2 text-[0.8rem] text-slate-500">
          <RefreshCw className="h-3.5 w-3.5 animate-spin text-cyan-400" />
          Scanning {hostname}…
        </div>
      </td>
    </tr>
  );
}

/* ─── Main component ───────────────────────────────────────────────────── */
interface Props {
  initialData: AIOpsVulnSummaryPayload | null;
  initialError: string | null;
}

export function VulnerabilitiesClient({ initialData, initialError }: Props) {
  const [data, setData] = useState<AIOpsVulnSummaryPayload | null>(initialData);
  const [error, setError] = useState<string | null>(initialError);
  const [scanning, setScanning] = useState<"idle" | "all" | Set<string>>("idle");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "critical" | "high" | "unscanned">("all");
  // toScan = devices that need scanning, startedAt = epoch ms when scan fired
  const scanStateRef = useRef<{ toScan: number; startedAt: number } | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const summary = data?.summary;
  const devices = data?.devices ?? [];

  function stopPolling() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    scanStateRef.current = null;
    setScanning("idle");
  }

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  /* ── filtered devices ────────────────────────────────────────────────── */
  const filtered = useMemo(() => {
    let rows = devices;
    if (search) {
      const q = search.toLowerCase();
      rows = rows.filter(
        (d) => d.hostname.toLowerCase().includes(q) || d.ip_address.includes(q),
      );
    }
    if (filter === "critical") rows = rows.filter((d) => (d.critical_count ?? 0) > 0);
    if (filter === "high")     rows = rows.filter((d) => (d.high_count ?? 0) > 0 || (d.critical_count ?? 0) > 0);
    if (filter === "unscanned") rows = rows.filter((d) => !d.scan_id);
    return rows;
  }, [devices, search, filter]);

  /* ── handlers ────────────────────────────────────────────────────────── */
  async function refresh() {
    try {
      setData(await fetchVulnerabilitySummary());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Refresh failed");
    }
  }

  async function handleScanAll() {
    if (scanning === "all") return;
    setScanning("all");
    setError(null);
    try {
      const res = await triggerScanAll();
      const toScan = res.to_scan ?? 0;
      if (toScan === 0) {
        setScanning("idle");
        setError(res.message ?? "All devices were recently scanned.");
        return;
      }

      const startedAt = Date.now();
      // max wait = 60s per device, hard cap 10 min
      const maxWaitMs = Math.min(toScan * 60_000, 600_000);
      scanStateRef.current = { toScan, startedAt };

      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        const state = scanStateRef.current;
        if (!state) { stopPolling(); return; }

        // Hard timeout — stop no matter what
        if (Date.now() - state.startedAt > maxWaitMs) {
          stopPolling();
          return;
        }

        try {
          const fresh = await fetchVulnerabilitySummary();
          setData(fresh);

          // Check if all "error+unscanned" devices now have a fresh scan
          // A fresh scan = scanned_at AFTER our startedAt timestamp
          const freshScanned = (fresh.devices ?? []).filter((d) => {
            if (!d.scanned_at) return false;
            return new Date(d.scanned_at).getTime() >= state.startedAt;
          }).length;

          if (freshScanned >= state.toScan) {
            stopPolling();
          }
        } catch { /* keep polling */ }
      }, 5000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Scan all failed");
      setScanning("idle");
    }
  }

  async function handleScanDevice(hostname: string) {
    setScanning((prev) => {
      const s = new Set(prev instanceof Set ? prev : []);
      s.add(hostname);
      return s;
    });
    try {
      await triggerVulnScan(hostname);
      setData(await fetchVulnerabilitySummary());
    } catch (e) {
      setError(e instanceof Error ? e.message : `Scan failed: ${hostname}`);
    } finally {
      setScanning((prev) => {
        if (!(prev instanceof Set)) return "idle";
        const s = new Set(prev);
        s.delete(hostname);
        return s.size === 0 ? "idle" : s;
      });
    }
  }

  const isScanning = (h: string) =>
    scanning === "all" || (scanning instanceof Set && scanning.has(h));

  /* ── risk level for sorting / display ─────────────────────────────────── */
  function riskLevel(d: AIOpsDeviceVulnRow) {
    if (!d.scan_id) return "unscanned";
    if ((d.critical_count ?? 0) > 0) return "critical";
    if ((d.high_count ?? 0) > 0) return "high";
    if ((d.medium_count ?? 0) > 0) return "medium";
    if ((d.low_count ?? 0) > 0) return "low";
    return "clean";
  }

  const RISK_LABEL: Record<string, { label: string; cls: string }> = {
    critical:  { label: "Critical",  cls: "border-rose-500/30  bg-rose-500/10  text-rose-300" },
    high:      { label: "High Risk", cls: "border-orange-500/30 bg-orange-500/10 text-orange-300" },
    medium:    { label: "Medium",    cls: "border-amber-500/30  bg-amber-500/10  text-amber-300" },
    low:       { label: "Low",       cls: "border-sky-500/30    bg-sky-500/10    text-sky-300" },
    clean:     { label: "Clean",     cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" },
    unscanned: { label: "Not Scanned", cls: "border-slate-700/40 bg-slate-700/20 text-slate-500" },
  };

  return (
    <div className="space-y-5">
      {/* ── Page header ──────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[0.68rem] font-semibold uppercase tracking-[0.16em] text-slate-600">Security</p>
          <h1 className="mt-0.5 text-[1.15rem] font-bold text-white">Vulnerability Management</h1>
          <p className="mt-0.5 text-[0.78rem] text-slate-500">
            Cisco PSIRT advisories across all network devices
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={refresh}
            className="flex items-center gap-1.5 rounded border border-white/10 bg-white/[0.04] px-3 py-1.5 text-[0.75rem] text-slate-400 transition hover:text-slate-200"
          >
            <RefreshCw className="h-3 w-3" />
            Refresh
          </button>
          <button
            onClick={handleScanAll}
            disabled={scanning !== "idle"}
            className="flex items-center gap-1.5 rounded border border-cyan-500/30 bg-cyan-500/10 px-3.5 py-1.5 text-[0.78rem] font-medium text-cyan-300 transition hover:bg-cyan-500/20 disabled:opacity-50"
          >
            {scanning === "all" ? (
              <>
                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                {scanStateRef.current
                  ? `Scanning… (${scanStateRef.current.toScan} devices)`
                  : "Starting…"}
              </>
            ) : (
              <>
                <ShieldAlert className="h-3.5 w-3.5" />
                Scan All Devices
              </>
            )}
          </button>
        </div>
      </div>

      {/* ── Error banner ─────────────────────────────────────────────── */}
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3.5 py-2.5 text-[0.82rem] text-rose-300">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {/* ── KPI row ──────────────────────────────────────────────────── */}
      {summary && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
          <KpiCard
            label="Total Devices"
            value={summary.total_devices}
            sub={`${summary.scanned_devices} scanned`}
          />
          <KpiCard
            label="Devices at Risk"
            value={summary.devices_with_critical + summary.devices_with_high}
            sub="critical or high"
            accent={(summary.devices_with_critical + summary.devices_with_high) > 0 ? "text-rose-300" : "text-emerald-300"}
          />
          <KpiCard
            label="Critical"
            value={summary.total_critical}
            sub="advisories"
            accent={summary.total_critical > 0 ? "text-rose-300" : "text-slate-400"}
          />
          <KpiCard
            label="High"
            value={summary.total_high}
            sub="advisories"
            accent={summary.total_high > 0 ? "text-orange-300" : "text-slate-400"}
          />
          <KpiCard
            label="Medium"
            value={summary.total_medium}
            sub="advisories"
            accent={summary.total_medium > 0 ? "text-amber-300" : "text-slate-400"}
          />
          <KpiCard
            label="Total Advisories"
            value={summary.total_advisories}
            sub={`${summary.unscanned_devices} unscanned`}
          />
        </div>
      )}

      {/* ── Device risk matrix ───────────────────────────────────────── */}
      <SectionCard
        title="Device Risk Matrix"
        eyebrow="All Devices"
        noPadding
        actions={
          <div className="flex items-center gap-2">
            {/* Quick filters */}
            {(["all", "critical", "high", "unscanned"] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`rounded px-2.5 py-1 text-[0.7rem] font-medium transition ${
                  filter === f
                    ? "bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-500/25"
                    : "text-slate-500 hover:text-slate-300"
                }`}
              >
                {f === "all" ? "All" : f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
            {/* Search */}
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-slate-600" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search…"
                className="rounded border border-white/10 bg-white/[0.04] pl-7 pr-3 py-1.5 text-[0.75rem] text-slate-300 placeholder-slate-600 outline-none focus:border-cyan-500/30 w-36"
              />
            </div>
          </div>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full table-fixed text-[0.8rem]">
            <colgroup>
              <col className="w-[22%]" />
              <col className="w-[9%]" />
              <col className="w-[12%]" />
              <col className="w-[7%]" />
              <col className="w-[7%]" />
              <col className="w-[7%]" />
              <col className="w-[7%]" />
              <col className="w-[13%]" />
              <col className="w-[16%]" />
            </colgroup>
            <thead>
              <tr className="border-b border-white/[0.07]">
                {["Device", "Risk", "IOS Version", "Crit", "High", "Med", "Low", "Impact Checks", ""].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-2.5 text-left text-[0.65rem] font-semibold uppercase tracking-[0.14em] text-slate-600"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-[0.82rem] text-slate-600">
                    No devices match the current filter.
                  </td>
                </tr>
              )}
              {filtered.map((device) => {
                const rl = riskLevel(device);
                const { label, cls } = RISK_LABEL[rl];
                const deviceScanning = isScanning(device.hostname);
                return (
                  <tr
                    key={device.id}
                    className={`border-b border-white/[0.05] transition hover:bg-white/[0.025] ${deviceScanning ? "animate-pulse opacity-60" : ""}`}
                  >
                    {/* Device */}
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2.5">
                        <DeviceRiskIcon device={device} />
                        <div className="min-w-0">
                          <p className="truncate font-semibold text-white">{device.hostname}</p>
                          <p className="text-[0.68rem] text-slate-600">{device.ip_address}</p>
                        </div>
                      </div>
                    </td>

                    {/* Risk badge */}
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center rounded border px-2 py-0.5 text-[0.63rem] font-semibold uppercase tracking-wide ${cls}`}>
                        {label}
                      </span>
                    </td>

                    {/* IOS Version */}
                    <td className="px-4 py-3">
                      <span className="rounded bg-white/[0.04] px-1.5 py-0.5 text-[0.72rem] font-mono text-slate-400">
                        {device.version || "—"}
                      </span>
                    </td>

                    {/* Severity counts */}
                    <td className="px-4 py-3">
                      <SevCell n={device.critical_count} style="text-rose-300" />
                    </td>
                    <td className="px-4 py-3">
                      <SevCell n={device.high_count} style="text-orange-300" />
                    </td>
                    <td className="px-4 py-3">
                      <SevCell n={device.medium_count} style="text-amber-300" />
                    </td>
                    <td className="px-4 py-3">
                      <SevCell n={device.low_count} style="text-sky-300" />
                    </td>

                    {/* Actions */}
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        {device.scan_id ? (
                          <Link
                            href={`/aiops/devices/${encodeURIComponent(device.hostname)}?tab=vulnerabilities`}
                            className="flex items-center gap-1 rounded border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[0.72rem] text-slate-400 transition hover:text-cyan-300"
                          >
                            View
                            <ChevronRight className="h-3 w-3" />
                          </Link>
                        ) : null}
                        <button
                          onClick={() => handleScanDevice(device.hostname)}
                          disabled={scanning !== "idle"}
                          className="flex items-center gap-1 rounded border border-cyan-500/20 bg-cyan-500/[0.07] px-2.5 py-1 text-[0.72rem] text-cyan-400 transition hover:bg-cyan-500/15 disabled:opacity-40"
                        >
                          {deviceScanning ? (
                            <RefreshCw className="h-3 w-3 animate-spin" />
                          ) : (
                            <ShieldAlert className="h-3 w-3" />
                          )}
                          {deviceScanning ? "…" : "Scan"}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Last scan note */}
        {summary && summary.scanned_devices > 0 && (
          <div className="border-t border-white/[0.05] px-4 py-2.5">
            <p className="text-[0.68rem] text-slate-700">
              {summary.scanned_devices} of {summary.total_devices} devices scanned
              {summary.unscanned_devices > 0 && (
                <> · <span className="text-amber-600">{summary.unscanned_devices} not yet scanned</span></>
              )}
            </p>
          </div>
        )}
      </SectionCard>

      {/* ── Risk distribution visual ─────────────────────────────────── */}
      {summary && summary.total_advisories > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[
            { label: "Critical Advisories", count: summary.total_critical, bar: "bg-rose-500",   text: "text-rose-300",   pct: summary.total_critical  / summary.total_advisories },
            { label: "High Advisories",     count: summary.total_high,     bar: "bg-orange-500", text: "text-orange-300", pct: summary.total_high      / summary.total_advisories },
            { label: "Medium Advisories",   count: summary.total_medium,   bar: "bg-amber-400",  text: "text-amber-300",  pct: summary.total_medium    / summary.total_advisories },
            { label: "Low Advisories",      count: summary.total_low,      bar: "bg-sky-400",    text: "text-sky-300",    pct: summary.total_low       / summary.total_advisories },
          ].map(({ label, count, bar, text, pct }) => (
            <div key={label} className="rounded-[0.95rem] border border-white/8 bg-white/[0.03] px-4 py-3.5">
              <div className="flex items-center justify-between">
                <p className="text-[0.7rem] font-medium text-slate-500">{label}</p>
                <p className={`text-[0.78rem] font-semibold ${text}`}>
                  {Math.round(pct * 100)}%
                </p>
              </div>
              <p className={`mt-1 text-2xl font-bold ${text}`}>{count}</p>
              <div className="mt-2 h-1.5 w-full rounded-full bg-white/10">
                <div
                  className={`h-full rounded-full ${bar} transition-all`}
                  style={{ width: `${Math.round(pct * 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Devices with AI summaries ─────────────────────────────────── */}
      {devices.some((d) => d.llm_summary) && (
        <SectionCard title="AI Security Assessments" eyebrow="Per Device">
          <div className="space-y-3">
            {devices
              .filter((d) => d.llm_summary && ((d.critical_count ?? 0) > 0 || (d.high_count ?? 0) > 0))
              .map((d) => (
                <div
                  key={d.id}
                  className="rounded-[0.95rem] border border-indigo-500/10 bg-indigo-500/[0.04] p-4"
                >
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <Wifi className="h-3.5 w-3.5 text-indigo-400/70" />
                    <span className="text-[0.82rem] font-semibold text-white">{d.hostname}</span>
                    <span className="rounded bg-white/[0.04] px-1.5 py-0.5 font-mono text-[0.68rem] text-slate-500">
                      {d.version}
                    </span>
                    {(d.critical_count ?? 0) > 0 && (
                      <span className="rounded border border-rose-500/25 bg-rose-500/10 px-1.5 py-0.5 text-[0.63rem] font-semibold uppercase text-rose-300">
                        {d.critical_count} Critical
                      </span>
                    )}
                    {(d.high_count ?? 0) > 0 && (
                      <span className="rounded border border-orange-500/25 bg-orange-500/10 px-1.5 py-0.5 text-[0.63rem] font-semibold uppercase text-orange-300">
                        {d.high_count} High
                      </span>
                    )}
                    <Link
                      href={`/aiops/devices/${encodeURIComponent(d.hostname)}?tab=vulnerabilities`}
                      className="ml-auto flex items-center gap-1 text-[0.72rem] text-slate-500 transition hover:text-cyan-300"
                    >
                      View full report <ChevronRight className="h-3 w-3" />
                    </Link>
                  </div>
                  <p className="text-[0.82rem] leading-relaxed text-slate-400">{d.llm_summary}</p>
                </div>
              ))}
          </div>
        </SectionCard>
      )}
    </div>
  );
}
