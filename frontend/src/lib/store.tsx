"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import {
  compressSession,
  createSession,
  deleteSession,
  getRagMode,
  getSessionHistory,
  getSessionTokens,
  getWebContent,
  listSessions,
  renameSession,
  setRagMode,
  streamChat,
  type RetrievalResult,
  type SessionSummary,
  type ToolCall,
  type WebContent
} from "@/lib/api";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls: ToolCall[];
  retrievals: RetrievalResult[];
  status_msg?: string;
};

type TokenStats = {
  system_tokens: number;
  message_tokens: number;
  total_tokens: number;
};

type AppStore = {
  sessions: SessionSummary[];
  currentSessionId: string | null;
  messages: Message[];
  isStreaming: boolean;
  isLoading: boolean;
  ragMode: boolean;
  sidebarWidth: number;
  inspectorWidth: number;
  tokenStats: TokenStats | null;
  compressedContext: string | null;
  createNewSession: () => Promise<void>;
  selectSession: (sessionId: string) => Promise<void>;
  sendMessage: (value: string) => Promise<void>;
  toggleRagMode: () => Promise<void>;
  renameCurrentSession: (title: string) => Promise<void>;
  removeSession: (sessionId: string) => Promise<void>;
  compressCurrentSession: () => Promise<void>;
  setSidebarWidth: (width: number) => void;
  setInspectorWidth: (width: number) => void;
  webContent: WebContent | null;
  fetchWebContent: (url: string) => Promise<void>;
};

const StoreContext = createContext<AppStore | null>(null);

function makeId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function toUiMessages(history: Awaited<ReturnType<typeof getSessionHistory>>["messages"]) {
  return history.map((message) => ({
    id: makeId(),
    role: message.role,
    content: message.content ?? "",
    toolCalls: (message.tool_calls ?? []).map((tc) => ({
      ...tc,
      success: tc.success ?? false,
    })),
    retrievals: [],
    status_msg: message.tool_status,
  }));
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [ragMode, setRagModeState] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(308);
  const [inspectorWidth, setInspectorWidth] = useState(360);
  const [tokenStats, setTokenStats] = useState<TokenStats | null>(null);
  const [webContent, setWebContent] = useState<WebContent | null>(null);
  const [compressedContext, setCompressedContext] = useState<string | null>(null);


  async function refreshSessions() {
    try {
      setSessions(await listSessions());
    } catch (error) {
      console.error("刷新会话列表失败:", error);
    }
  }

  async function refreshSessionDetails(sessionId: string) {
    try {
      const [history, tokens] = await Promise.all([
        getSessionHistory(sessionId),
        getSessionTokens(sessionId)
      ]);
      setMessages(toUiMessages(history.messages));
      setTokenStats(tokens);
      setCompressedContext(history.compressed_context ?? null);
    } catch (error) {
      console.error("刷新会话详情失败:", error);
    }
  }

  async function createNewSession() {
    try {
      const created = await createSession();
      await refreshSessions();
      setCurrentSessionId(created.id);
      setMessages([]);
      setTokenStats(null);
      setWebContent(null);
      setCompressedContext(null);
    } catch (error) {
      console.error("创建会话失败:", error);
    }
  }

  async function selectSession(sessionId: string) {
    setIsLoading(true);
    setCurrentSessionId(sessionId);
    setWebContent(null);
    try {
      await refreshSessionDetails(sessionId);
    } catch (error) {
      console.error("切换会话失败:", error);
    } finally {
      setIsLoading(false);
    }
  }

  async function ensureSession() {
    if (currentSessionId) {
      return currentSessionId;
    }

    const created = await createSession();
    setCurrentSessionId(created.id);
    await refreshSessions();
    return created.id;
  }

  async function sendMessage(value: string) {
    if (!value.trim() || isStreaming) {
      return;
    }

    const sessionId = await ensureSession();
    const userMessage: Message = {
      id: makeId(),
      role: "user",
      content: value.trim(),
      toolCalls: [],
      retrievals: []
    };
    const assistantMessage: Message = {
      id: makeId(),
      role: "assistant",
      content: "",
      toolCalls: [],
      retrievals: [],
      status_msg: undefined,
    };

    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setIsStreaming(true);

    let activeAssistantId = assistantMessage.id;
    let streamError: string | null = null;

    const patchAssistant = (updater: (message: Message) => Message) => {
      setMessages((prev) =>
        prev.map((message) => (message.id === activeAssistantId ? updater(message) : message))
      );
    };

    try {
      await streamChat(
        { message: value.trim(), session_id: sessionId },
        {
          onEvent(event, data) {
            if (event === "retrieval") {
              patchAssistant((message) => ({
                ...message,
                retrievals: (data.results as RetrievalResult[]) ?? []
              }));
              return;
            }

            if (event === "token") {
              patchAssistant((message) => ({
                ...message,
                content: `${message.content}${String(data.content ?? "")}`
              }));
              return;
            }

            if (event === "tool_start") {
              patchAssistant((message) => ({
                ...message,
                toolCalls: [
                  ...message.toolCalls,
                  {
                    tool: String(data.tool ?? "tool"),
                    input: String(data.input ?? ""),
                    success: false
                  }
                ]
              }));
              return;
            }

            if (event === "tool_end") {
              patchAssistant((message) => ({
                ...message,
                toolCalls: message.toolCalls.map((toolCall, index, list) =>
                  index === list.length - 1
                    ? { ...toolCall, success: Boolean(data.success) }
                    : toolCall
                )
              }));
              return;
            }

            if (event === "tool_status") {
              patchAssistant((message) => ({
                ...message,
                status_msg: String(data.status_msg ?? "")
              }));
              return;
            }

            if (event === "new_response") {
              // 不再创建新的 assistant 消息，工具调用和内容合并到同一条消息
              return;
            }

            if (event === "done") {
              const finalContent = String(data.content ?? "");
              patchAssistant((message) =>
                message.content
                  ? message
                  : {
                      ...message,
                      content: finalContent
                    }
              );
              return;
            }

            if (event === "title") {
              void refreshSessions();
              return;
            }

            if (event === "error") {
              streamError = String(data.error ?? "unknown error");
              patchAssistant((message) => ({
                ...message,
                content:
                  message.content || `请求失败: ${streamError}`
              }));
            }
          }
        }
      );
    } finally {
      setIsStreaming(false);
      if (!streamError) {
        try {
          await refreshSessions();
          await refreshSessionDetails(sessionId);
        } catch (error) {
          console.error("刷新会话状态失败:", error);
        }
      }
    }
  }

  async function toggleRagMode() {
    const next = !ragMode;
    setRagModeState(next);
    try {
      await setRagMode(next);
    } catch (error) {
      setRagModeState(!next);
      throw error;
    }
  }

  async function renameCurrentSession(title: string) {
    if (!currentSessionId || !title.trim()) {
      return;
    }
    try {
      await renameSession(currentSessionId, title.trim());
      await refreshSessions();
    } catch (error) {
      console.error("重命名会话失败:", error);
    }
  }

  async function removeSession(sessionId: string) {
    try {
      await deleteSession(sessionId);
      await refreshSessions();
      if (currentSessionId === sessionId) {
        const nextSessions = await listSessions();
        setSessions(nextSessions);
        if (nextSessions.length) {
          setCurrentSessionId(nextSessions[0].id);
          await refreshSessionDetails(nextSessions[0].id);
        } else {
          setCurrentSessionId(null);
          setMessages([]);
          setTokenStats(null);
        }
      }
    } catch (error) {
      console.error("删除会话失败:", error);
    }
  }

  async function compressCurrentSession() {
    if (!currentSessionId) {
      return;
    }
    try {
      await compressSession(currentSessionId);
      await refreshSessionDetails(currentSessionId);
      await refreshSessions();
    } catch (error) {
      console.error("压缩会话失败:", error);
    }
  }

  async function fetchWebContent(url: string) {
    if (!currentSessionId) return;
    try {
      const result = await getWebContent(url, currentSessionId);
      setWebContent(result);
    } catch (error) {
      console.error("获取网页内容失败:", error);
    }
  }

  useEffect(() => {
    void (async () => {
      try {
        const [initialSessions, rag] = await Promise.all([
          listSessions(),
          getRagMode()
        ]);

        setSessions(initialSessions);
        setRagModeState(rag.enabled);

        if (initialSessions.length) {
          setCurrentSessionId(initialSessions[0].id);
          await refreshSessionDetails(initialSessions[0].id);
        } else {
          const created = await createSession();
          setCurrentSessionId(created.id);
          setSessions([created]);
        }
      } catch (error) {
        console.error("应用初始化失败:", error);
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  const value: AppStore = {
    sessions,
    currentSessionId,
    messages,
    isStreaming,
    isLoading,
    ragMode,
    sidebarWidth,
    inspectorWidth,
    tokenStats,
    compressedContext,
    createNewSession,
    selectSession,
    sendMessage,
    toggleRagMode,
    renameCurrentSession,
    removeSession,
    compressCurrentSession,
    setSidebarWidth,
    setInspectorWidth,
    webContent,
    fetchWebContent
  };

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useAppStore() {
  const value = useContext(StoreContext);
  if (!value) {
    throw new Error("useAppStore must be used inside AppProvider");
  }
  return value;
}
