"use client";

import { Bot, Send } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/form";
import { Panel } from "@/components/ui/panel";
import { useAIStatus } from "@/features/ai/api";

/** Entry point to Copilot from the Command Center; the run itself lives on /copilot. */
export function CopilotCard() {
  const status = useAIStatus();
  const router = useRouter();
  const [q, setQ] = useState("");
  if (!status.data?.enabled) return null;
  const go = (question: string) => router.push(`/copilot?q=${encodeURIComponent(question)}`);
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (q.trim()) go(q.trim());
  };
  return (
    <Panel title="Copilot" icon={<Bot />} description="Answers from your live operations" bodyClassName="p-3">
      <form onSubmit={onSubmit} className="flex gap-1.5" aria-label="Ask Copilot from the Command Center">
        <Input aria-label="Ask Copilot" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about today…" className="h-8" />
        <Button type="submit" size="icon" variant="primary" aria-label="Ask" disabled={!q.trim()}>
          <Send />
        </Button>
      </form>
      <button type="button" onClick={() => go("What needs my attention today?")} className="mt-2 text-xs text-accent hover:underline" data-testid="copilot-attention">
        What needs my attention today?
      </button>
    </Panel>
  );
}
