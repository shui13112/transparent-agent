"use client";

import { ChatPanel } from "@/components/chat/ChatPanel";
import { InspectorPanel } from "@/components/editor/InspectorPanel";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Navbar } from "@/components/layout/Navbar";
import { ResizeHandle } from "@/components/layout/ResizeHandle";
import { Sidebar } from "@/components/layout/Sidebar";
import { AppProvider, useAppStore } from "@/lib/store";

function MainLayout() {
  const { sidebarWidth, inspectorWidth, setSidebarWidth, setInspectorWidth } = useAppStore();

  return (
    <main className="min-h-screen p-4 md:p-6">
      <div className="mx-auto flex max-w-[1800px] flex-col gap-4">
        <Navbar />
        <div className="flex min-h-[calc(100vh-var(--navbar-offset))] gap-0">
          <div className="sticky top-6 self-start" style={{ width: sidebarWidth }}>
            <Sidebar />
          </div>
          <ResizeHandle onResize={(delta) => setSidebarWidth(Math.max(260, sidebarWidth + delta))} />
          <ChatPanel />
          <ResizeHandle
            onResize={(delta) => setInspectorWidth(Math.max(320, inspectorWidth - delta))}
          />
          <div className="sticky top-6 self-start" style={{ width: inspectorWidth }}>
            <InspectorPanel />
          </div>
        </div>
      </div>
    </main>
  );
}

export default function Page() {
  return (
    <AppProvider>
      <ErrorBoundary>
        <MainLayout />
      </ErrorBoundary>
    </AppProvider>
  );
}
