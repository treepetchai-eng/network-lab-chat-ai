import { cn } from "@/lib/utils";

function SkeletonBar({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "h-4 rounded-md bg-[length:200%_100%] animate-[shimmer_2.6s_linear_infinite]",
        "bg-gradient-to-r from-white/[0.03] via-white/[0.06] to-white/[0.03]",
        className,
      )}
    />
  );
}

export function SkeletonKpiCard() {
  return (
    <div className="rounded-xl border border-white/8 bg-white/[0.03] p-5">
      <SkeletonBar className="h-3 w-24" />
      <SkeletonBar className="mt-4 h-9 w-16" />
    </div>
  );
}

export function SkeletonTableRows({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, i) => (
        <tr key={i} className="border-b border-white/6">
          {Array.from({ length: cols }).map((_, j) => (
            <td key={j} className="px-4 py-3">
              <SkeletonBar className={j === 0 ? "w-3/4" : "w-1/2"} />
              {j === 0 && <SkeletonBar className="mt-2 h-3 w-1/2" />}
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

export function SkeletonSection({ lines = 3 }: { lines?: number }) {
  return (
    <div className="rounded-xl border border-white/8 bg-white/[0.03] p-5 space-y-3">
      <SkeletonBar className="h-5 w-32" />
      {Array.from({ length: lines }).map((_, i) => (
        <SkeletonBar key={i} className={i === lines - 1 ? "w-2/3" : "w-full"} />
      ))}
    </div>
  );
}
