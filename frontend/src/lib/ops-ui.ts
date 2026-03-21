import { cn } from "@/lib/utils";

export const OPS_ACTION_LINK_CLASS = cn(
  "inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm font-medium text-slate-100",
  "cursor-pointer transition hover:border-cyan-300/25 hover:bg-white/[0.08] hover:text-white",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/35",
);

export const OPS_TEXT_LINK_CLASS = cn(
  "rounded-md text-sm text-cyan-200 transition hover:text-white",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/35",
);

export const OPS_CARD_LINK_CLASS = cn(
  "block rounded-2xl border border-white/8 bg-[#0c1520] px-4 py-4",
  "cursor-pointer transition hover:border-cyan-300/20 hover:bg-[#111b28]",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/35",
);

export const OPS_CONTROL_CLASS = cn(
  "w-full rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3 text-sm text-white",
  "transition hover:border-white/16 hover:bg-white/[0.06]",
  "outline-none placeholder:text-slate-500 focus-visible:border-cyan-300/25 focus-visible:ring-2 focus-visible:ring-cyan-300/35",
);

// Shared layout & card classes
export const OPS_SECTION_CLASS = "rounded-xl border border-white/8 bg-white/[0.03] p-5";
export const OPS_INNER_CARD_CLASS = "rounded-lg border border-white/6 bg-[#0d1822] p-4";

/**
 * Unified section card — header/body container used across ALL sections and table wrappers.
 * Structure: <div className={OPS_SECTION_CARD_CLASS}> <div className={OPS_SECTION_CARD_HEADER}> … </div> <div>…body…</div> </div>
 */
export const OPS_SECTION_CARD_CLASS = "overflow-hidden rounded-xl border border-white/8 bg-white/[0.03]";
/** Standard section card header bar — sits directly inside OPS_SECTION_CARD_CLASS with a bottom separator. */
export const OPS_SECTION_CARD_HEADER = "flex items-center justify-between gap-4 border-b border-white/6 px-5 py-4";

/** @deprecated Use OPS_SECTION_CARD_CLASS — kept for backward compat */
export const OPS_TABLE_WRAPPER_CLASS = "overflow-hidden rounded-xl border border-white/8 bg-white/[0.03]";

export const OPS_TH_CLASS = "px-4 py-2.5 text-left text-[0.7rem] font-medium uppercase tracking-[0.18em] text-slate-500";

/** Page body wrapper — consistent padding + section rhythm across all pages. */
export const PAGE_CONTENT_CLASS = "space-y-5 px-6 py-5 sm:px-8";

/** Filter / search bar grid — 3-col on md+, stacked on mobile. */
export const FILTER_BAR_CLASS = "grid gap-2 grid-cols-1 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]";

export const OPS_ERROR_CLASS = "rounded-xl border border-rose-400/20 bg-rose-400/10 px-4 py-3 text-sm text-rose-100";
export const OPS_INFO_CLASS = "rounded-xl border border-cyan-300/20 bg-cyan-300/10 px-4 py-3 text-sm text-cyan-50";
export const OPS_SUCCESS_CLASS = "rounded-xl border border-emerald-400/20 bg-emerald-400/10 px-4 py-3 text-sm text-emerald-100";

/** Severity / risk-level left-border accent map. Works for both incident severity and approval risk_level. */
export const SEV_BORDER: Record<string, string> = {
  critical: "border-l-fuchsia-500",
  high:     "border-l-rose-500",
  medium:   "border-l-amber-500",
  low:      "border-l-emerald-500",
};
