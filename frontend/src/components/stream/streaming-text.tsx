import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { MarkdownRenderer } from "@/components/markdown/markdown-renderer";

interface Props {
  tokens: string;
  isComplete: boolean;
  finalContent?: string;
  /** Called once when the typewriter animation catches up to the final content. */
  onAnimationComplete?: () => void;
}

/**
 * Streaming text with character-by-character reveal.
 *
 * During live streaming we render lightweight plain text, then switch to the
 * full Markdown renderer once the final response is complete.
 */
export function StreamingText({ tokens, isComplete, finalContent, onAnimationComplete }: Props) {
  const [visibleLen, setVisibleLen] = useState(0);
  const rafRef = useRef<number | null>(null);
  const lastTickRef = useRef<number>(0);
  const targetRef = useRef("");
  const completedRef = useRef(false);

  // Stable callback ref to avoid re-triggering effects
  const onCompleteRef = useRef(onAnimationComplete);
  useEffect(() => {
    onCompleteRef.current = onAnimationComplete;
  }, [onAnimationComplete]);

  const targetText = useMemo(
    () => (isComplete && finalContent ? finalContent : tokens),
    [finalContent, isComplete, tokens],
  );

  useEffect(() => {
    targetRef.current = targetText;
  }, [targetText]);

  // When target shrinks (e.g. reset), schedule the clamp outside the effect body
  useEffect(() => {
    if (targetText.length < visibleLen) {
      const frame = requestAnimationFrame(() => {
        setVisibleLen(targetText.length);
      });
      return () => cancelAnimationFrame(frame);
    }
  }, [targetText, visibleLen]);

  useEffect(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);

    // Cadence: visible characters per frame tick, delay between ticks (ms).
    // Tuned for a professional "streaming" feel that matches GPT-style UIs.
    const cadenceForBacklog = (backlog: number) => {
      if (backlog > 500) return { chars: 12, delay: 6 };
      if (backlog > 300) return { chars: 8, delay: 8 };
      if (backlog > 150) return { chars: 5, delay: 10 };
      if (backlog > 60)  return { chars: 3, delay: 14 };
      if (backlog > 20)  return { chars: 2, delay: 18 };
      return { chars: 1, delay: 24 };
    };

    const animate = (ts: number) => {
      if (!lastTickRef.current) lastTickRef.current = ts;

      setVisibleLen((cur) => {
        const target = targetRef.current;
        if (cur >= target.length) return target.length;

        const backlog = target.length - cur;
        const { chars, delay } = cadenceForBacklog(backlog);

        if (ts - lastTickRef.current < delay) {
          rafRef.current = requestAnimationFrame(animate);
          return cur;
        }

        lastTickRef.current = ts;
        const next = Math.min(cur + chars, target.length);
        if (next < target.length) {
          rafRef.current = requestAnimationFrame(animate);
        } else {
          rafRef.current = null;
          lastTickRef.current = 0;
        }
        return next;
      });
    };

    lastTickRef.current = 0;
    rafRef.current = requestAnimationFrame(animate);

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [targetText]);

  // Once complete AND the animation has caught up, signal parent & show final Markdown
  const animationDone = isComplete && finalContent && visibleLen >= finalContent.length;
  const visibleText = targetText.slice(0, visibleLen);
  const deferredVisibleText = useDeferredValue(visibleText);

  useEffect(() => {
    if (animationDone && !completedRef.current) {
      completedRef.current = true;
      // Small delay so users see the last few chars before the view switches
      const t = setTimeout(() => onCompleteRef.current?.(), 120);
      return () => clearTimeout(t);
    }
  }, [animationDone]);

  if (animationDone) {
    return <MarkdownRenderer content={finalContent!} />;
  }

  const stillAnimating = visibleLen < targetText.length;

  return (
    <div className="relative">
      <div className="ui-copy whitespace-pre-wrap break-words text-slate-100">
        {deferredVisibleText}
      </div>
      {stillAnimating && (
        <span className="inline-block h-[1.1em] w-[2.5px] translate-y-0.5 rounded-full bg-cyan-300 align-baseline animate-stream-caret ml-0.5 shadow-[0_0_8px_rgba(103,232,249,0.6),0_0_16px_rgba(103,232,249,0.3)]" />
      )}
    </div>
  );
}
