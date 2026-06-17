"use client";

import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="flex min-h-screen items-center justify-center p-8">
          <div className="rounded-[32px] border border-[var(--color-line)] bg-white/55 p-10 text-center">
            <h1 className="text-2xl font-semibold tracking-[-0.04em]">应用发生错误</h1>
            <p className="mt-3 text-sm text-[var(--color-ink-soft)]">
              {this.state.error?.message ?? "未知错误"}
            </p>
            <button
              className="mt-6 rounded-full bg-ocean px-6 py-2 text-sm text-white"
              onClick={() => this.setState({ hasError: false, error: null })}
              type="button"
            >
              重试
            </button>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}
