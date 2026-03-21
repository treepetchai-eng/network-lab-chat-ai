import { ArrowUp } from "lucide-react";
import { cn } from "@/lib/utils";

interface SendButtonProps {
  disabled: boolean;
  active: boolean;
  onClick: () => void;
}

export function SendButton({ disabled, active, onClick }: SendButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "group relative inline-flex h-10 w-10 items-center justify-center rounded-full border transition-all duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/30",
        active
          ? "border-cyan-300/22 bg-[linear-gradient(135deg,rgba(34,211,238,0.35),rgba(59,130,246,0.38))] text-white shadow-[0_12px_28px_rgba(34,211,238,0.18)] hover:translate-y-[-1px] hover:shadow-[0_14px_34px_rgba(34,211,238,0.24)] hover:brightness-110 active:scale-95"
          : "border-white/10 bg-white/[0.05] text-slate-500",
        disabled && "cursor-not-allowed opacity-50",
      )}
    >
      {/* Subtle glow ring on active */}
      {active && (
        <span className="absolute inset-0 rounded-full bg-cyan-400/10 blur-md transition-opacity group-hover:bg-cyan-400/15" />
      )}
      <ArrowUp className="relative h-4.5 w-4.5" />
    </button>
  );
}
