import { cn } from "@/lib/utils";

interface LoadingSkeletonProps {
  lines?: number;
  className?: string;
}

export function LoadingSkeleton({ lines = 3, className }: LoadingSkeletonProps) {
  return (
    <div className={cn("space-y-3", className)} aria-hidden="true">
      {Array.from({ length: lines }).map((_, index) => (
        <div
          key={index}
          className={cn(
            "h-3.5 rounded-full bg-[linear-gradient(110deg,rgba(88,105,148,0.12),rgba(104,214,255,0.28),rgba(88,105,148,0.12))] bg-[length:200%_100%] animate-[shimmer_2.6s_linear_infinite]",
            index === lines - 1 ? "w-2/3" : "w-full",
          )}
        />
      ))}
    </div>
  );
}
