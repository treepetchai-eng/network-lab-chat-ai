"use client";
import Image from "next/image";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "./confirm-dialog";
import { useState } from "react";

interface Props {
  onNewChat: () => void;
  hasMessages: boolean;
}

export function Header({ onNewChat, hasMessages }: Props) {
  const [showConfirm, setShowConfirm] = useState(false);

  const handleNewChat = () => {
    if (hasMessages) {
      setShowConfirm(true);
    } else {
      onNewChat();
    }
  };

  return (
    <>
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-card/80 backdrop-blur-sm px-5">
        <div className="flex items-center gap-3">
          <Image src="/logo.svg" alt="Network Copilot" width={32} height={32} />
          <div>
            <h1 className="text-sm font-semibold text-foreground">
              Network Copilot
            </h1>
            <p className="text-[0.65rem] leading-tight text-muted-foreground">
              AI-powered network assistant
            </p>
          </div>
        </div>

        <Button
          variant="outline"
          size="sm"
          onClick={handleNewChat}
          className="gap-1.5 text-muted-foreground hover:text-foreground"
        >
          <Plus className="h-3.5 w-3.5" />
          New Chat
        </Button>
      </header>
      <ConfirmDialog
        open={showConfirm}
        onOpenChange={setShowConfirm}
        onConfirm={() => {
          setShowConfirm(false);
          onNewChat();
        }}
      />
    </>
  );
}
