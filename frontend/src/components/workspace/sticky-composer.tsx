"use client";

import { useCallback, useRef, useState, type KeyboardEvent } from "react";
import { SendButton } from "@/components/workspace/send-button";

const MAX_MESSAGE_LENGTH = 2000;

interface StickyComposerProps {
  onSend: (message: string) => void;
  disabled: boolean;
  onDraftChange?: (value: string) => void;
}

export function StickyComposer({ onSend, disabled, onDraftChange }: StickyComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const resize = () => {
    if (!textareaRef.current) return;
    textareaRef.current.style.height = "auto";
    textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
  };

  const handleSend = useCallback(() => {
    const next = value.trim();
    if (!next || disabled) return;
    onSend(next);
    setValue("");
    onDraftChange?.("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [disabled, onDraftChange, onSend, value]);

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  };

  const hasValue = value.trim().length > 0;

  return (
    <div className="relative mx-auto w-full max-w-[48rem] px-1 sm:px-0">
      <div className="rounded-2xl sm:rounded-[1.85rem] border border-white/10 bg-[linear-gradient(180deg,rgba(10,16,28,0.96),rgba(8,12,20,0.88))] p-2 sm:p-2.5 shadow-[0_20px_60px_rgba(1,6,18,0.42)] backdrop-blur-2xl transition-shadow duration-300 focus-within:shadow-[0_20px_60px_rgba(1,6,18,0.42),0_0_0_1px_rgba(34,211,238,0.12)]">
        <div className="flex items-end gap-2 sm:gap-3 rounded-xl sm:rounded-[1.45rem] border border-cyan-300/10 bg-white/[0.03] px-3 py-2.5 sm:px-4 sm:py-3.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] transition-colors duration-300 focus-within:border-cyan-300/18">
          <textarea
            ref={textareaRef}
            value={value}
            maxLength={MAX_MESSAGE_LENGTH}
            onChange={(event) => {
              const next = event.target.value.slice(0, MAX_MESSAGE_LENGTH);
              setValue(next);
              onDraftChange?.(next);
              resize();
            }}
            onKeyDown={handleKeyDown}
            rows={1}
            disabled={disabled}
            placeholder="Ask Network Copilot..."
            className="max-h-[200px] min-h-[44px] sm:min-h-[52px] flex-1 resize-none bg-transparent py-1.5 sm:py-2 text-[0.92rem] sm:text-[0.98rem] leading-6 sm:leading-7 text-slate-50 outline-none placeholder:text-slate-500 disabled:opacity-40"
          />
          <SendButton disabled={disabled || !hasValue} active={hasValue && !disabled} onClick={handleSend} />
        </div>
        {/* Bottom bar with hints */}
        <div className="mt-1.5 flex items-center justify-between px-2 sm:px-3">
          <div className="flex items-center gap-1.5 text-[0.58rem] sm:text-[0.64rem] text-slate-600">
            <kbd className="rounded border border-white/8 bg-white/[0.03] px-1 py-px font-mono text-[0.54rem] text-slate-500">⏎</kbd>
            <span>send</span>
            <span className="mx-0.5 text-slate-700">·</span>
            <kbd className="rounded border border-white/8 bg-white/[0.03] px-1 py-px font-mono text-[0.54rem] text-slate-500">⇧⏎</kbd>
            <span>new line</span>
          </div>
          {value.length > MAX_MESSAGE_LENGTH * 0.8 && (
            <span className={`text-[0.58rem] sm:text-[0.64rem] tabular-nums ${value.length >= MAX_MESSAGE_LENGTH ? "text-rose-400" : "text-slate-500"}`}>
              {value.length}/{MAX_MESSAGE_LENGTH}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
