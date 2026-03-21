import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}

export function ConfirmDialog({ open, onOpenChange, onConfirm }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md border-white/10 bg-[linear-gradient(180deg,rgba(8,13,24,0.96),rgba(7,11,20,0.9))] text-white shadow-[0_30px_80px_rgba(2,7,18,0.58)] backdrop-blur-2xl">
        <DialogHeader>
          <DialogTitle className="text-lg text-white">Start a new chat?</DialogTitle>
          <DialogDescription className="text-slate-400">
            This clears the active session memory immediately and opens a fresh workspace.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2 sm:gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)} className="border border-white/10 bg-white/[0.03] text-slate-200 hover:bg-white/[0.06]">
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm} className="bg-rose-500/20 text-rose-100 hover:bg-rose-500/28">
            New Chat
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
