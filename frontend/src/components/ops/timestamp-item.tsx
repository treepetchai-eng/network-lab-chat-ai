import { formatRelativeTimestamp, formatTimestamp } from "@/lib/time";

interface TimestampItemProps {
  label: string;
  value: string | null | undefined;
  emptyLabel?: string;
}

export function TimestampItem({ label, value, emptyLabel = "-" }: TimestampItemProps) {
  if (!value) {
    return (
      <div className="space-y-1">
        {label && <p className="text-[0.72rem] uppercase tracking-[0.18em] text-slate-500">{label}</p>}
        <p className="text-sm text-slate-300">{emptyLabel}</p>
      </div>
    );
  }

  const absolute = formatTimestamp(value);
  const relative = formatRelativeTimestamp(value);

  return (
    <div className="space-y-1" title={absolute}>
      {label && <p className="text-[0.72rem] uppercase tracking-[0.18em] text-slate-500">{label}</p>}
      <p className="text-sm text-slate-200">{absolute}</p>
      <p className="text-xs text-slate-500">{relative}</p>
    </div>
  );
}
