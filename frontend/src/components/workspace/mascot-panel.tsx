"use client";

import { motion } from "framer-motion";
import { Bot, Cpu, Network, ShieldCheck } from "lucide-react";

const orbitIcons = [
  { icon: Network, label: "Path", top: "8%", left: "12%", delay: 0.4 },
  { icon: ShieldCheck, label: "Safe", top: "16%", right: "8%", delay: 0.8 },
  { icon: Cpu, label: "CLI", bottom: "20%", left: "6%", delay: 1.2 },
  { icon: Bot, label: "AI", bottom: "8%", right: "12%", delay: 1.6 },
];

interface MascotPanelProps {
  isStreaming: boolean;
  phase: "idle" | "listening" | "grounding" | "planning" | "executing" | "summarizing";
}

export function MascotPanel({ isStreaming, phase }: MascotPanelProps) {
  const isBusy = isStreaming || phase !== "idle";
  const isTyping = phase === "executing";
  const isThinking = phase === "planning" || phase === "grounding";
  const showIdeaLight = isThinking || phase === "summarizing";

  // ---- Body bounce ----
  const hoodieY =
    isTyping ? [0, -3, 1, -2, 0.5, -3, 0] :
    isThinking ? [0, -3, 0] :
    phase === "listening" ? [0, -1.5, 0] :
    [0, -1, 0];
  const hoodieSpeed = isTyping ? 0.45 : isThinking ? 2.1 : 2.4;

  // ---- Laptop glow ----
  const laptopGlow =
    isTyping ? [0.7, 1, 0.7, 1, 0.8] :
    phase === "summarizing" ? [0.55, 0.9, 0.65] :
    phase === "listening" ? [0.35, 0.55, 0.4] :
    [0.45, 0.7, 0.5];

  // ---- Head tilt ----
  const headTilt =
    isThinking ? [0, -3, 2, -1, 0] :
    isTyping ? [0, -1.5, 0.8, -1, 0] :
    phase === "summarizing" ? [0, 1.2, 0] :
    phase === "listening" ? [0, 1.8, 0] :
    [0, 0.6, 0];
  const headSpeed = isTyping ? 0.5 : isThinking ? 1.6 : 1.9;

  // ---- Arms: fast alternating typing when executing ----
  const leftArmY =
    isTyping ? [0, -6, 2, -5, 1, -7, 0] :
    isThinking ? [0, -1.4, 0] :
    phase === "summarizing" ? [0, -1, 0] :
    [0, 0.8, 0];
  const rightArmY =
    isTyping ? [0, 3, -5, 4, -6, 2, 0] :
    isThinking ? [0, 1.2, 0] :
    phase === "summarizing" ? [0, 0.8, 0] :
    [0, -0.6, 0];
  const leftArmSpeed = isTyping ? 0.32 : 2.4;
  const rightArmSpeed = isTyping ? 0.28 : 2.2;

  // ---- Screen sweep ----
  const screenSweepX =
    isTyping ? [-34, 108] :
    isThinking ? [-24, 92] :
    phase === "summarizing" ? [-18, 84] :
    null;

  // ---- Mouth shape ----
  const mouthPath =
    phase === "summarizing" ? "M133 128c8 7 17 7 25 0" :
    isTyping ? "M136 130c6 2 13 2 19 0" :
    "M136 129c6 4 13 4 19 0";

  return (
    <motion.aside
      initial={{ opacity: 0, x: -16 }}
      animate={{ opacity: 0.999, x: 0 }}
      className="relative flex h-full min-h-0 flex-col overflow-hidden rounded-[32px] border border-white/10 bg-[linear-gradient(180deg,rgba(9,16,29,0.9),rgba(7,12,22,0.72))] px-5 pb-6 pt-5 shadow-[0_30px_80px_rgba(3,8,18,0.45)] backdrop-blur-2xl"
    >
      <div className="absolute inset-x-8 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(125,211,252,0.65),transparent)]" />
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(56,189,248,0.16),transparent_40%),radial-gradient(circle_at_bottom,rgba(6,182,212,0.08),transparent_30%)]" />

      {/* Phase label */}
      <div className="relative z-10 flex items-center justify-center gap-2 pb-2">
        <motion.span
          key={phase}
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          className="rounded-full border border-cyan-300/14 bg-cyan-400/8 px-3 py-1 text-[0.6rem] font-medium uppercase tracking-[0.2em] text-cyan-200/70"
        >
          {phase === "idle" ? "Ready" : phase === "listening" ? "Listening..." : phase === "grounding" ? "Grounding..." : phase === "planning" ? "Planning..." : phase === "executing" ? "⚡ Executing" : "Summarizing..."}
        </motion.span>
      </div>

      <div className="relative z-10 flex min-h-0 flex-1 items-center justify-center py-3">
        <div className="w-full rounded-[28px] border border-white/8 bg-[linear-gradient(180deg,rgba(255,255,255,0.035),rgba(255,255,255,0.018))] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
          <div className="relative mx-auto flex aspect-[4/5] max-w-[15rem] items-center justify-center overflow-hidden rounded-[24px] border border-white/6 bg-[radial-gradient(circle_at_top,rgba(34,211,238,0.08),transparent_38%),rgba(255,255,255,0.015)]">
            {orbitIcons.map(({ icon: Icon, label, delay, ...style }) => (
              <motion.div
                key={label}
                className="absolute flex items-center gap-2 rounded-full border border-cyan-300/14 bg-[#0b1728]/85 px-2.5 py-1.5 text-[0.64rem] text-cyan-100/80 backdrop-blur-xl"
                style={style}
                animate={{ y: [0, -6, 0] }}
                transition={{ duration: 4.8, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut", delay }}
              >
                <Icon className="h-3 w-3" />
                <span>{label}</span>
              </motion.div>
            ))}
            <svg viewBox="0 0 260 300" className="h-full w-full">
            <defs>
              <radialGradient id="laptopGlow" cx="50%" cy="50%" r="65%">
                <stop offset="0%" stopColor="rgba(103,232,249,0.7)" />
                <stop offset="100%" stopColor="rgba(103,232,249,0)" />
              </radialGradient>
              <linearGradient id="hoodie" x1="0" x2="1">
                <stop offset="0%" stopColor="#0f172a" />
                <stop offset="100%" stopColor="#18263d" />
              </linearGradient>
              <linearGradient id="hair" x1="0" x2="1">
                <stop offset="0%" stopColor="#050608" />
                <stop offset="55%" stopColor="#101319" />
                <stop offset="100%" stopColor="#22262d" />
              </linearGradient>
              <linearGradient id="glasses" x1="0" x2="1">
                <stop offset="0%" stopColor="#020202" />
                <stop offset="100%" stopColor="#121212" />
              </linearGradient>
            </defs>

            {/* Floor shadow */}
            <ellipse cx="130" cy="250" rx="72" ry="20" fill="rgba(27,44,76,0.56)" />

            {/* ---- Idea lightbulb ---- */}
            {showIdeaLight ? (
              <motion.g
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: [0.45, 1, 0.58], y: [2, -2, 1], scale: [0.97, 1.05, 1] }}
                transition={{ duration: isThinking ? 1.05 : 1.45, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
              >
                <motion.circle cx="143" cy="40" r="28" fill="rgba(34,211,238,0.08)"
                  animate={{ opacity: [0.12, 0.32, 0.14], scale: [0.94, 1.22, 1] }}
                  transition={{ duration: 1.35, repeat: Number.POSITIVE_INFINITY, ease: "easeOut" }}
                />
                <motion.circle cx="143" cy="40" r="21" fill="rgba(56,189,248,0.12)"
                  animate={{ opacity: [0.18, 0.44, 0.2], scale: [0.96, 1.14, 1] }}
                  transition={{ duration: 1.1, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
                />
                <motion.circle cx="143" cy="40" r="15" fill="#67e8f9"
                  animate={{ fill: ["#67e8f9", "#f8fafc", "#67e8f9"], opacity: [0.72, 1, 0.8] }}
                  transition={{ duration: 1.15, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
                />
                <motion.circle cx="143" cy="40" r="9" fill="#ecfeff"
                  animate={{ scale: [0.9, 1.08, 0.94], opacity: [0.7, 1, 0.82] }}
                  transition={{ duration: 0.95, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
                />
                <motion.circle cx="143" cy="40" r="24" fill="none" stroke="rgba(103,232,249,0.55)"
                  strokeWidth="1.4" strokeDasharray="4 8"
                  animate={{ rotate: [0, 360] }}
                  transition={{ duration: 8, repeat: Number.POSITIVE_INFINITY, ease: "linear" }}
                  style={{ transformOrigin: "143px 40px" }}
                />
                <motion.path d="M143 18l4 8h9l-7 6 3 9-9-5-9 5 3-9-7-6h9Z" fill="#f8fafc"
                  animate={{ scale: [0.96, 1.08, 1], opacity: [0.7, 1, 0.8] }}
                  transition={{ duration: 1.05, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
                  style={{ transformOrigin: "143px 29px" }}
                />
                <motion.path d="M131 50h8l-5 8h7l-10 12 3-9h-6Z" fill="#22d3ee" stroke="rgba(236,254,255,0.75)"
                  strokeWidth="1" strokeLinejoin="round"
                  animate={{ x: [0, -1, 0], y: [0, 1.2, 0], opacity: [0.65, 1, 0.72] }}
                  transition={{ duration: 0.95, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
                />
              </motion.g>
            ) : null}

            {/* ---- Sweat drops when executing ---- */}
            {isTyping ? (
              <>
                <motion.circle cx="100" cy="88" r="3" fill="#67e8f9"
                  animate={{ y: [0, 18, 18], opacity: [0.8, 0.4, 0], scale: [0.8, 1.1, 0.5] }}
                  transition={{ duration: 1.2, repeat: Number.POSITIVE_INFINITY, ease: "easeIn", delay: 0.2 }}
                />
                <motion.circle cx="190" cy="92" r="2.5" fill="#67e8f9"
                  animate={{ y: [0, 16, 16], opacity: [0.7, 0.3, 0], scale: [0.7, 1, 0.4] }}
                  transition={{ duration: 1.4, repeat: Number.POSITIVE_INFINITY, ease: "easeIn", delay: 0.6 }}
                />
                <motion.circle cx="108" cy="78" r="2" fill="#a5f3fc"
                  animate={{ y: [0, 14, 14], opacity: [0.6, 0.2, 0], scale: [0.6, 0.9, 0.3] }}
                  transition={{ duration: 1.0, repeat: Number.POSITIVE_INFINITY, ease: "easeIn", delay: 0.9 }}
                />
              </>
            ) : null}

            {/* ---- Laptop glow ---- */}
            <motion.ellipse
              cx="146" cy="182" rx="64" ry="46"
              fill="url(#laptopGlow)" opacity="0.85"
              animate={{ opacity: laptopGlow, scale: isTyping ? [1, 1.08, 1, 1.05, 1] : [1, 1.02, 1] }}
              transition={{ duration: isTyping ? 0.6 : 2.4, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
            />

            {/* ---- Body + Arms ---- */}
            <motion.g
              animate={{ y: hoodieY }}
              transition={{ duration: hoodieSpeed, repeat: Number.POSITIVE_INFINITY, ease: isTyping ? [0.25, 0.1, 0.25, 1] : "easeInOut" }}
            >
              {/* Hoodie body */}
              <path d="M90 222c10-44 35-74 69-74s54 28 62 72" fill="url(#hoodie)" stroke="rgba(125,211,252,0.24)" strokeWidth="2" />

              {/* Left arm — fast hammering on keyboard */}
              <motion.path
                d="M93 221c3-23 15-46 37-57l-7 56Z"
                fill="#14233a" opacity="0.85"
                animate={{ y: leftArmY, rotate: isTyping ? [0, -3, 1, -2, 0] : [0] }}
                transition={{
                  duration: leftArmSpeed,
                  repeat: Number.POSITIVE_INFINITY,
                  ease: isTyping ? [0.25, 0.1, 0.25, 1] : "easeInOut",
                }}
                style={{ transformOrigin: "115px 180px" }}
              />

              {/* Right arm — alternating with left */}
              <motion.path
                d="M209 221c-2-23-13-45-33-57l4 56Z"
                fill="#14233a" opacity="0.85"
                animate={{ y: rightArmY, rotate: isTyping ? [0, 3, -1, 2, 0] : [0] }}
                transition={{
                  duration: rightArmSpeed,
                  repeat: Number.POSITIVE_INFINITY,
                  ease: isTyping ? [0.25, 0.1, 0.25, 1] : "easeInOut",
                  delay: isTyping ? 0.14 : 0.08,
                }}
                style={{ transformOrigin: "190px 180px" }}
              />

              {/* Head */}
              <motion.g
                animate={{ rotate: headTilt }}
                transition={{ duration: headSpeed, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
                style={{ transformOrigin: "142px 110px" }}
              >
                {/* Face */}
                <path d="M180 109c0 22-17 38-38 41h-5c-21-3-38-19-38-41 0-24 17-42 41-42s40 18 40 42Z" fill="#f2d7bf" />
                {/* Hair layers */}
                <path d="M101 95c11-29 58-42 91-5-1 10-7 16-16 19-5-10-15-18-29-21-17-3-31 1-46 7Z" fill="url(#hair)" />
                <path d="M109 73c18-19 58-20 77 9-10-3-18-2-26 1-6-6-16-8-27-6-8 1-15 3-24 9Z" fill="url(#hair)" />
                <path d="M96 100c6-21 17-32 38-40-6 12-2 21 1 28-12 7-25 12-39 12Z" fill="url(#hair)" opacity="0.95" />
                <path d="M141 61c18-4 37 3 49 19-11-2-19 0-26 4-6-9-12-15-23-23Z" fill="url(#hair)" opacity="0.88" />
                <path d="M117 79c7-12 19-18 34-17 17 1 30 10 38 25-7-4-16-6-27-5-13 1-24 4-45 14 1-6-1-12 0-17Z" fill="url(#hair)" />
                <path d="M132 88c16-7 28-8 40-4 8 3 12 8 14 13-12 2-20 0-28-3-9-4-15-5-26-6Z" fill="#456789" opacity="0.45" />
                {/* Dimple */}
                <path d="M132 144c5 3 10 4 16 4 6 0 11-1 16-4-6 8-12 12-16 12s-10-4-16-12Z" fill="rgba(7,10,14,0.16)" />
                {/* Glasses */}
                <rect x="112" y="101" width="31" height="13" rx="2.5" fill="url(#glasses)" stroke="rgba(15,23,42,0.96)" strokeWidth="2.6" />
                <rect x="147" y="101" width="31" height="13" rx="2.5" fill="url(#glasses)" stroke="rgba(15,23,42,0.96)" strokeWidth="2.6" />
                <path d="M143 108h4" stroke="rgba(15,23,42,0.88)" strokeWidth="3" strokeLinecap="round" />
                <path d="M113 108l-8-2" stroke="rgba(15,23,42,0.88)" strokeWidth="2.2" strokeLinecap="round" />
                <path d="M177 108l8-2" stroke="rgba(15,23,42,0.88)" strokeWidth="2.2" strokeLinecap="round" />

                {/* Glasses reflection */}
                <motion.path
                  d="M170 106c9 1 15 7 18 17"
                  fill="none" stroke="#7fd8ff" strokeLinecap="round" strokeWidth="3" opacity="0.8"
                  animate={isBusy ? { opacity: [0.35, 1, 0.35] } : { opacity: 0.45 }}
                  transition={{ duration: 1.8, repeat: Number.POSITIVE_INFINITY }}
                />

                {/* Eyebrows — concentrated look when executing */}
                <motion.g
                  animate={isTyping ? { y: [0, -1.5, 0, -1.2, 0] } : isBusy ? { y: [0, -0.4, 0] } : { y: 0 }}
                  transition={{ duration: isTyping ? 0.5 : 3, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
                >
                  <motion.path
                    d="M123 97c5-2 10-2 14 0"
                    fill="none" stroke="#1f2937" strokeWidth="2.1" strokeLinecap="round"
                    animate={isTyping ? { d: ["M123 97c5-2 10-2 14 0", "M122 95c5-3 11-3 16 0", "M123 97c5-2 10-2 14 0"] } : { opacity: isBusy ? [0.2, 0.5, 0.2] : 0.28 }}
                    transition={{ duration: isTyping ? 0.5 : 3.6, repeat: Number.POSITIVE_INFINITY }}
                  />
                  <motion.path
                    d="M151 97c5-2 10-2 14 0"
                    fill="none" stroke="#1f2937" strokeWidth="2.1" strokeLinecap="round"
                    animate={isTyping ? { d: ["M151 97c5-2 10-2 14 0", "M150 95c5-3 11-3 16 0", "M151 97c5-2 10-2 14 0"] } : { opacity: isBusy ? [0.2, 0.5, 0.2] : 0.28 }}
                    transition={{ duration: isTyping ? 0.5 : 3.6, repeat: Number.POSITIVE_INFINITY }}
                  />
                </motion.g>

                {/* Mouth — changes per phase */}
                <motion.path
                  d={mouthPath}
                  fill="none" stroke="#8b5e58" strokeLinecap="round" strokeWidth="2.6"
                  animate={{ d: mouthPath }}
                  transition={{ duration: 0.3 }}
                />

                {/* Listening waves */}
                {phase === "listening" ? (
                  <>
                    <motion.path d="M179 102c7 2 12 7 15 14" fill="none" stroke="#9be7ff" strokeLinecap="round" strokeWidth="2.5"
                      animate={{ opacity: [0.2, 0.85, 0.2], x: [0, 1.5, 0] }}
                      transition={{ duration: 1.25, repeat: Number.POSITIVE_INFINITY }}
                    />
                    <motion.path d="M184 96c10 3 17 10 20 20" fill="none" stroke="#9be7ff" strokeLinecap="round" strokeWidth="2"
                      animate={{ opacity: [0.1, 0.65, 0.1], x: [0, 2, 0] }}
                      transition={{ duration: 1.45, repeat: Number.POSITIVE_INFINITY, delay: 0.08 }}
                    />
                  </>
                ) : null}
              </motion.g>

              {/* Torso */}
              <rect x="101" y="140" width="82" height="88" rx="16" fill="url(#hoodie)" />
              <path d="M126 141c4 16 11 22 16 22s12-6 16-22" fill="none" stroke="rgba(125,211,252,0.24)" strokeWidth="2" />
            </motion.g>

            {/* ---- Laptop ---- */}
            <motion.g
              animate={isTyping ? { y: [0, -1.5, 0.5, -1, 0] } : { y: 0 }}
              transition={{ duration: isTyping ? 0.4 : 1.05, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
            >
              {/* Laptop base */}
              <rect x="73" y="177" width="114" height="18" rx="9" fill="#0d1728" stroke="rgba(125,211,252,0.18)" strokeWidth="2" />
              <rect x="68" y="189" width="126" height="14" rx="7" fill="#101d33" />

              {/* Keyboard key presses when executing */}
              {isTyping ? (
                <g>
                  <motion.rect x="82" y="180" width="8" height="5" rx="1" fill="rgba(103,232,249,0.3)"
                    animate={{ opacity: [0, 0.8, 0], y: [0, 1, 0] }}
                    transition={{ duration: 0.25, repeat: Number.POSITIVE_INFINITY, delay: 0 }}
                  />
                  <motion.rect x="96" y="180" width="8" height="5" rx="1" fill="rgba(103,232,249,0.25)"
                    animate={{ opacity: [0, 0.7, 0], y: [0, 1, 0] }}
                    transition={{ duration: 0.3, repeat: Number.POSITIVE_INFINITY, delay: 0.12 }}
                  />
                  <motion.rect x="110" y="180" width="8" height="5" rx="1" fill="rgba(103,232,249,0.3)"
                    animate={{ opacity: [0, 0.8, 0], y: [0, 1, 0] }}
                    transition={{ duration: 0.22, repeat: Number.POSITIVE_INFINITY, delay: 0.08 }}
                  />
                  <motion.rect x="124" y="180" width="8" height="5" rx="1" fill="rgba(103,232,249,0.2)"
                    animate={{ opacity: [0, 0.6, 0], y: [0, 1, 0] }}
                    transition={{ duration: 0.28, repeat: Number.POSITIVE_INFINITY, delay: 0.18 }}
                  />
                  <motion.rect x="138" y="180" width="8" height="5" rx="1" fill="rgba(103,232,249,0.25)"
                    animate={{ opacity: [0, 0.7, 0], y: [0, 1, 0] }}
                    transition={{ duration: 0.24, repeat: Number.POSITIVE_INFINITY, delay: 0.05 }}
                  />
                  <motion.rect x="152" y="180" width="8" height="5" rx="1" fill="rgba(103,232,249,0.2)"
                    animate={{ opacity: [0, 0.5, 0], y: [0, 1, 0] }}
                    transition={{ duration: 0.26, repeat: Number.POSITIVE_INFINITY, delay: 0.15 }}
                  />
                  <motion.rect x="166" y="180" width="8" height="5" rx="1" fill="rgba(103,232,249,0.3)"
                    animate={{ opacity: [0, 0.8, 0], y: [0, 1, 0] }}
                    transition={{ duration: 0.2, repeat: Number.POSITIVE_INFINITY, delay: 0.1 }}
                  />
                </g>
              ) : null}

              {/* Screen */}
              <rect x="80" y="158" width="118" height="52" rx="14" fill="#09121f" stroke="rgba(125,211,252,0.28)" strokeWidth="2" />
              <rect x="88" y="166" width="102" height="36" rx="10" fill="#0b1f35" />

              {/* Screen sweep */}
              {screenSweepX ? (
                <motion.rect
                  x="88" y="166" width="28" height="36" rx="10"
                  fill="rgba(125,211,252,0.12)"
                  animate={{ x: screenSweepX, opacity: [0, 0.45, 0] }}
                  transition={{ duration: isTyping ? 0.6 : 1.45, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
                />
              ) : null}

              {/* Screen text lines — more frantic when executing */}
              <motion.path d="M98 176h72" stroke="rgba(147,197,253,0.26)" strokeWidth="3" strokeLinecap="round"
                animate={isTyping
                  ? { opacity: [0.2, 0.9, 0.3, 0.8, 0.2], pathLength: [0.3, 1, 0.5, 1, 0.3] }
                  : { opacity: [0.24, 0.72, 0.3] }
                }
                transition={{ duration: isTyping ? 0.55 : 1.7, repeat: Number.POSITIVE_INFINITY, delay: 0.08 }}
              />
              <motion.path d="M98 184h44" stroke="rgba(103,232,249,0.55)" strokeWidth="3" strokeLinecap="round"
                animate={isTyping
                  ? { opacity: [0.3, 1, 0.4, 1, 0.3], pathLength: [0.2, 0.8, 1, 0.6, 0.2] }
                  : { opacity: [0.4, 1, 0.45] }
                }
                transition={{ duration: isTyping ? 0.45 : 1.2, repeat: Number.POSITIVE_INFINITY }}
              />
              <motion.path d="M98 192h58" stroke="rgba(103,232,249,0.4)" strokeWidth="3" strokeLinecap="round"
                animate={isTyping
                  ? { opacity: [0.2, 0.8, 0.5, 1, 0.2], pathLength: [0.4, 1, 0.3, 0.9, 0.4] }
                  : { opacity: [0.3, 0.9, 0.35] }
                }
                transition={{ duration: isTyping ? 0.5 : 1.5, repeat: Number.POSITIVE_INFINITY, delay: 0.18 }}
              />

              {/* Cursor blink on screen when executing */}
              {isTyping ? (
                <motion.rect x="168" y="184" width="2" height="8" rx="1" fill="#67e8f9"
                  animate={{ opacity: [1, 0, 1] }}
                  transition={{ duration: 0.5, repeat: Number.POSITIVE_INFINITY }}
                />
              ) : null}
            </motion.g>

            {/* ---- Flying code particles when executing ---- */}
            {isTyping ? (
              <>
                <motion.text x="58" y="160" fill="#67e8f9" fontSize="9" fontFamily="monospace" opacity="0.6"
                  animate={{ y: [160, 130], x: [58, 42], opacity: [0.6, 0] }}
                  transition={{ duration: 1.8, repeat: Number.POSITIVE_INFINITY, delay: 0 }}
                >{`{ }`}</motion.text>
                <motion.text x="200" y="155" fill="#a5f3fc" fontSize="8" fontFamily="monospace" opacity="0.5"
                  animate={{ y: [155, 125], x: [200, 215], opacity: [0.5, 0] }}
                  transition={{ duration: 2.0, repeat: Number.POSITIVE_INFINITY, delay: 0.5 }}
                >{`</>`}</motion.text>
                <motion.text x="72" y="140" fill="#7dd3fc" fontSize="7" fontFamily="monospace" opacity="0.4"
                  animate={{ y: [140, 115], x: [72, 55], opacity: [0.4, 0] }}
                  transition={{ duration: 1.5, repeat: Number.POSITIVE_INFINITY, delay: 0.8 }}
                >CLI</motion.text>
                <motion.text x="188" y="142" fill="#67e8f9" fontSize="7" fontFamily="monospace" opacity="0.4"
                  animate={{ y: [142, 118], x: [188, 205], opacity: [0.4, 0] }}
                  transition={{ duration: 1.6, repeat: Number.POSITIVE_INFINITY, delay: 1.2 }}
                >SSH</motion.text>
              </>
            ) : null}
            </svg>
          </div>
        </div>
      </div>

      {/* Phase-specific status text */}
      <div className="relative z-10 mt-3 text-center">
        <motion.p
          key={phase}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="text-[0.68rem] leading-5 text-slate-500"
        >
          {phase === "idle" && "Waiting for your command..."}
          {phase === "listening" && "Listening to your input..."}
          {phase === "grounding" && "Looking up device inventory..."}
          {phase === "planning" && "Analyzing the best approach..."}
          {isTyping && "Hammering commands into devices! 🔥"}
          {phase === "summarizing" && "Compiling the final answer..."}
        </motion.p>
      </div>
    </motion.aside>
  );
}
