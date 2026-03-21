"use client";
import { useRef, useState, useCallback, type KeyboardEvent } from "react";
import { ArrowUp } from "lucide-react";

const MAX_MESSAGE_LENGTH = 2000;

interface Props {
  onSend: (message: string) => void;
  disabled: boolean;
}

export function ChatInput({ onSend, disabled }: Props) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [value, disabled, onSend]);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = () => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
    }
  };

  const hasValue = value.trim().length > 0;

  return (
    <div className="shrink-0 pb-3 pt-2">
      <div className="relative rounded-2xl border border-border bg-card/80 backdrop-blur-sm shadow-sm transition-shadow focus-within:shadow-md focus-within:shadow-primary/10 focus-within:border-primary/40">
        <textarea
          ref={textareaRef}
          value={value}
          maxLength={MAX_MESSAGE_LENGTH}
          onChange={(e) => { setValue(e.target.value.slice(0, MAX_MESSAGE_LENGTH)); handleInput(); }}
          onKeyDown={handleKeyDown}
          placeholder="Ask Network Copilot..."
          disabled={disabled}
          rows={1}
          className="w-full resize-none bg-transparent px-4 pt-3 pb-11 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none disabled:opacity-40"
        />
        <div className="absolute bottom-2 right-2">
          <button
            onClick={handleSend}
            disabled={disabled || !hasValue}
            className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary text-primary-foreground transition-colors hover:bg-primary/80 disabled:bg-muted disabled:text-muted-foreground"
          >
            <ArrowUp className="h-4 w-4" strokeWidth={2.5} />
          </button>
        </div>
      </div>
      <p className="mt-1.5 text-center text-[0.65rem] text-muted-foreground">
        LLMs can make mistakes. Verify important information.
      </p>
    </div>
  );
}
