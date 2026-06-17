"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp, MessageSquare, Plus, Trash2 } from "lucide-react";

import { useAppStore } from "@/lib/store";

function preview(text: string) {
  return text.length > 72 ? `${text.slice(0, 72)}...` : text;
}

export function Sidebar() {
  const {
    sessions,
    currentSessionId,
    selectSession,
    createNewSession,
    removeSession,
    messages,
    compressedContext
  } = useAppStore();

  const [expandedContext, setExpandedContext] = useState(false);

  const turnToolCounts = useMemo(() => {
    const map = new Map<string, number>();
    let currentToolCount = 0;
    let currentAssistantIds: string[] = [];

    const flushTurn = () => {
      for (const id of currentAssistantIds) {
        map.set(id, currentToolCount);
      }
      currentAssistantIds = [];
      currentToolCount = 0;
    };

    for (const m of messages) {
      if (m.role === "user") {
        flushTurn();
      } else {
        currentAssistantIds.push(m.id);
        currentToolCount += m.toolCalls.length;
      }
    }
    flushTurn();

    return map;
  }, [messages]);

  const displayMessages = useMemo(
    () =>
      messages.filter((m) => {
        if (m.role === "user") return true;
        // 过滤掉无内容的纯工具调用消息（兼容旧会话格式）
        if (!m.content.trim() && m.toolCalls.length > 0) return false;
        return true;
      }),
    [messages],
  );

  return (
    <aside className="panel flex h-[calc(100vh-3rem)] flex-col rounded-[30px] p-4">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.28em] text-[var(--color-ink-soft)]">
            Sessions
          </p>
          <h2 className="text-lg font-semibold tracking-[-0.04em]">会话与原始消息</h2>
        </div>
        <button
          className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[rgba(15,139,141,0.12)] text-ocean"
          onClick={() => void createNewSession()}
          type="button"
        >
          <Plus size={18} />
        </button>
      </div>

      <div className="flex-1 min-h-0 space-y-4 overflow-y-auto pr-1">
        {/* Sessions */}
        <div className="space-y-2">
          {sessions.map((session) => (
            <div
              className={`rounded-3xl border px-4 py-3 transition ${
                session.id === currentSessionId
                  ? "border-transparent bg-[rgba(15,139,141,0.16)]"
                  : "border-[var(--color-line)] bg-white/45"
              }`}
              key={session.id}
            >
              <button
                className="w-full text-left"
                onClick={() => void selectSession(session.id)}
                type="button"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="font-medium">{session.title}</p>
                    <p className="mt-1 text-xs text-[var(--color-ink-soft)]">
                      {session.message_count} 条消息
                    </p>
                  </div>
                  <MessageSquare className="mt-1 text-[var(--color-ink-soft)]" size={16} />
                </div>
              </button>
              <button
                className="mt-3 flex items-center gap-2 text-xs text-[var(--color-ember)]"
                onClick={() => void removeSession(session.id)}
                type="button"
              >
                <Trash2 size={14} />
                删除
              </button>
            </div>
          ))}
        </div>

        {/* Raw Messages */}
        <div className="rounded-[24px] border border-[var(--color-line)] bg-white/40 p-3">
          <p className="text-xs uppercase tracking-[0.28em] text-[var(--color-ink-soft)]">
            Raw Messages
          </p>
          <div className="mt-3 space-y-3">
            {compressedContext && (
              <div className="rounded-2xl border border-[var(--color-ocean)]/30 bg-[rgba(15,139,141,0.06)] px-3 py-2">
                <div className="mb-1 flex items-center justify-between text-xs uppercase tracking-[0.2em] text-ocean">
                  <span>compressed context</span>
                  <button
                    className="text-ocean"
                    onClick={() => setExpandedContext((prev) => !prev)}
                    type="button"
                  >
                    {expandedContext ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </button>
                </div>
                <p className="text-sm text-[var(--color-ink-soft)]">
                  {expandedContext ? compressedContext : preview(compressedContext)}
                </p>
              </div>
            )}
            {displayMessages.map((message) => (
              <div
                className="rounded-2xl border border-[var(--color-line)] bg-white/60 px-3 py-2"
                key={message.id}
              >
                <div className="mb-1 flex items-center justify-between text-xs uppercase tracking-[0.2em] text-[var(--color-ink-soft)]">
                  <span>{message.role}</span>
                  {message.role === "assistant" && (turnToolCounts.get(message.id) ?? 0) > 0 && (
                    <span>{turnToolCounts.get(message.id)} tools</span>
                  )}
                </div>
                <p className="text-sm text-[var(--color-ink-soft)]">{preview(message.content)}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </aside>
  );
}
