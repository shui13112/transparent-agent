"use client";

import { CheckCircle, TerminalSquare, XCircle } from "lucide-react";

import type { ToolCall } from "@/lib/api";

export function ThoughtChain({
  toolCalls,
  status_msg,
}: {
  toolCalls: ToolCall[];
  status_msg?: string;
}) {
  if (!toolCalls.length) {
    return null;
  }

  const allSuccess = toolCalls.every((tc) => tc.success);
  const label = status_msg || `工具调用 ${toolCalls.length} 次`;

  return (
    <div className="mb-4 flex items-center gap-2 rounded-3xl border border-[rgba(212,106,74,0.18)] bg-[rgba(212,106,74,0.08)] px-4 py-3 text-sm">
      {allSuccess ? (
        <CheckCircle size={16} className="shrink-0 text-green-500" />
      ) : (
        <TerminalSquare size={16} className="shrink-0 text-[var(--color-ember)]" />
      )}
      <span className="text-[var(--color-ink-soft)]">{label}</span>
    </div>
  );
}
