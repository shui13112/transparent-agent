"use client";

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Globe } from "lucide-react";

import { RetrievalCard } from "@/components/chat/RetrievalCard";
import { ThoughtChain } from "@/components/chat/ThoughtChain";
import type { RetrievalResult, ToolCall } from "@/lib/api";
import { useAppStore } from "@/lib/store";

type Segment = { type: "text"; value: string } | { type: "source-btn"; url: string };

function parseContent(content: string): Segment[] {
  const segments: Segment[] = [];
  const regex = /\[transparent agent:\s*(.*?)\]/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(content)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: "text", value: content.slice(lastIndex, match.index) });
    }
    segments.push({ type: "source-btn", url: match[1].trim() });
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < content.length) {
    segments.push({ type: "text", value: content.slice(lastIndex) });
  }
  return segments;
}

function shortUrl(url: string): string {
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "");
    return host.length > 40 ? host.slice(0, 37) + "..." : host;
  } catch {
    return url.length > 44 ? url.slice(0, 41) + "..." : url;
  }
}

export function ChatMessage({
  role,
  content,
  toolCalls,
  retrievals,
  status_msg,
}: {
  role: "user" | "assistant";
  content: string;
  toolCalls: ToolCall[];
  retrievals: RetrievalResult[];
  status_msg?: string;
}) {
  const isUser = role === "user";
  const fetchWebContent = useAppStore().fetchWebContent;

  const segments = useMemo(
    () => (isUser ? [] : parseContent(content)),
    [content, isUser]
  );

  const hasSourceButtons = segments.length > 0;

  return (
    <article
      className={`max-w-[90%] rounded-[28px] px-5 py-4 ${
        isUser
          ? "ml-auto bg-[rgba(13,37,48,0.92)] text-white"
          : "panel mr-auto text-[var(--color-ink)]"
      }`}
    >
      {!isUser && <RetrievalCard results={retrievals} />}
      {!isUser && <ThoughtChain toolCalls={toolCalls} status_msg={status_msg} />}
      <div className={isUser ? "whitespace-pre-wrap leading-7" : "markdown"}>
        {isUser ? (
          content
        ) : hasSourceButtons ? (
          segments.map((seg, i) =>
            seg.type === "text" ? (
              <ReactMarkdown key={i} remarkPlugins={[remarkGfm]}>
                {seg.value}
              </ReactMarkdown>
            ) : (
              <button
                key={i}
                type="button"
                className="my-1 inline-flex items-center gap-1.5 rounded-full border border-[var(--color-ocean)] px-3 py-1 text-xs text-[var(--color-ocean)] transition-colors hover:bg-[rgba(15,139,141,0.10)]"
                onClick={() => fetchWebContent(seg.url)}
              >
                <Globe size={13} />
                {shortUrl(seg.url)}
              </button>
            )
          )
        ) : (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {content || status_msg || "正在思考..."}
          </ReactMarkdown>
        )}
      </div>
    </article>
  );
}
