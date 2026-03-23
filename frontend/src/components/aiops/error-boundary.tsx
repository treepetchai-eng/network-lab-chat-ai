"use client";

import { Component, type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

interface Props {
  children: ReactNode;
  fallbackMessage?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class AIOpsErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="rounded-[1.2rem] border border-rose-400/20 bg-rose-400/[0.06] p-8 text-center">
          <AlertTriangle className="mx-auto h-8 w-8 text-rose-300" />
          <p className="mt-3 text-[0.95rem] font-medium text-rose-100">
            {this.props.fallbackMessage ?? "Something went wrong loading this page"}
          </p>
          <p className="mt-2 text-[0.84rem] text-rose-200/70">
            {this.state.error?.message ?? "An unexpected error occurred."}
          </p>
          <button
            onClick={() => {
              this.setState({ hasError: false, error: null });
              window.location.reload();
            }}
            className="mt-4 inline-flex items-center gap-2 rounded-lg border border-rose-300/20 bg-rose-400/[0.1] px-4 py-2 text-[0.85rem] text-rose-100 transition hover:bg-rose-400/[0.15]"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Reload Page
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
