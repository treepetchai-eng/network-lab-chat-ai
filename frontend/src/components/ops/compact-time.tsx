import { formatRelativeTimestamp, formatTimestamp } from "@/lib/time";

export function CompactTime({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="shrink-0 text-xs text-slate-600">—</span>;
  return (
    <span
      className="shrink-0 text-xs text-slate-400 tabular-nums"
      title={formatTimestamp(value)}
    >
      {formatRelativeTimestamp(value)}
    </span>
  );
}
