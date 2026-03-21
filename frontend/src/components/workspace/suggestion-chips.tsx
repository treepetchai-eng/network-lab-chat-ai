"use client";

import { motion } from "framer-motion";

interface SuggestionChipsProps {
  suggestions: string[];
  onSelect: (value: string) => void;
  disabled?: boolean;
}

export function SuggestionChips({ suggestions, onSelect, disabled }: SuggestionChipsProps) {
  return (
    <div className="flex flex-wrap justify-center gap-2 sm:gap-3">
      {suggestions.map((suggestion, index) => (
        <motion.button
          key={suggestion}
          type="button"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 + index * 0.05 }}
          onClick={() => onSelect(suggestion)}
          disabled={disabled}
          className="group rounded-full border border-white/10 bg-white/[0.045] px-3 py-1.5 sm:px-4 sm:py-2 text-xs sm:text-sm text-slate-300 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] backdrop-blur-xl transition duration-300 hover:border-cyan-300/22 hover:bg-cyan-400/[0.08] hover:text-cyan-50 hover:shadow-[0_0_0_1px_rgba(34,211,238,0.08),0_16px_34px_rgba(34,211,238,0.1)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          <span className="bg-[linear-gradient(90deg,#e2f7ff,#a5f3fc)] bg-clip-text text-transparent group-hover:text-transparent">{suggestion}</span>
        </motion.button>
      ))}
    </div>
  );
}
