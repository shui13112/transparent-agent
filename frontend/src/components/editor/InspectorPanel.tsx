"use client";

import { ExternalLink } from "lucide-react";

import { useAppStore } from "@/lib/store";

export function InspectorPanel() {
  const { webContent } = useAppStore();

  return (
    <aside className="panel flex h-[calc(100vh-3rem)] flex-col rounded-[30px] p-4">
      <div className="mb-4">
        <p className="text-xs uppercase tracking-[0.28em] text-[var(--color-ink-soft)]">
          Inspector
        </p>
        <h2 className="text-lg font-semibold tracking-[-0.04em]">来源内容</h2>
      </div>

      {!webContent ? (
        <div className="flex flex-1 items-center justify-center text-sm text-[var(--color-ink-soft)]">
          点击消息中的来源按钮查看详情
        </div>
      ) : (
        <div className="flex flex-1 flex-col overflow-hidden">
          <div className="mb-3 space-y-1">
            <h3 className="text-sm font-semibold leading-snug">
              {webContent.title || "无标题"}
            </h3>
            <a
              className="inline-flex items-center gap-1 break-all text-xs text-[var(--color-ocean)] hover:underline"
              href={webContent.url}
              rel="noopener noreferrer"
              target="_blank"
            >
              <ExternalLink size={12} />
              {webContent.url}
            </a>
          </div>
          <pre className="flex-1 overflow-auto whitespace-pre-wrap rounded-[20px] bg-[rgba(13,37,48,0.04)] p-4 text-sm leading-relaxed text-[var(--color-ink)]">
            {webContent.content}
          </pre>
        </div>
      )}
    </aside>
  );
}
