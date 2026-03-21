"use client";

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { OPS_CONTROL_CLASS } from "@/lib/ops-ui";

interface ResolveDialogProps {
  open: boolean;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (notes: string) => void;
}

export function ResolveDialog({ open, busy, onOpenChange, onConfirm }: ResolveDialogProps) {
  const [notes, setNotes] = useState("");

  function handleConfirm() {
    const trimmed = notes.trim();
    if (!trimmed) return;
    onConfirm(trimmed);
  }

  function handleOpenChange(next: boolean) {
    if (!next) setNotes("");
    onOpenChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md border-white/10 bg-[linear-gradient(180deg,rgba(8,13,24,0.96),rgba(7,11,20,0.9))] text-white shadow-[0_30px_80px_rgba(2,7,18,0.58)] backdrop-blur-2xl">
        <DialogHeader>
          <DialogTitle className="text-base text-white">Resolve incident</DialogTitle>
          <DialogDescription className="text-slate-400">
            Provide resolution notes before closing. These notes are saved with the incident record.
          </DialogDescription>
        </DialogHeader>

        <textarea
          rows={4}
          placeholder="e.g. Link came back up after provider ticket resolved, monitored for 10 min — stable."
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          className={`${OPS_CONTROL_CLASS} resize-none`}
        />

        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => handleOpenChange(false)}
            className="border border-white/10 bg-white/[0.03] text-slate-200 hover:bg-white/[0.06]"
          >
            Cancel
          </Button>
          <Button
            variant="outline"
            disabled={busy || !notes.trim()}
            onClick={handleConfirm}
            className="border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10"
          >
            {busy ? "Resolving…" : "Resolve"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
