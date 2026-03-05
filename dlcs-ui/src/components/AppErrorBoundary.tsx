import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = {
  children: ReactNode;
};

type State = {
  hasError: boolean;
  message: string;
};

const CACHE_KEYS = [
  "dl.cache.posts",
  "dl.activeRunId",
  "dl.activeRunLogs",
  "dl.activeRunStart",
];

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: "" };

  static getDerivedStateFromError(error: unknown): State {
    return {
      hasError: true,
      message: error instanceof Error ? error.message : String(error),
    };
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    // Keep diagnostics in console for fast local debugging.
    // eslint-disable-next-line no-console
    console.error("DLens UI runtime error", error, info);
  }

  private handleReset = () => {
    for (const key of CACHE_KEYS) {
      localStorage.removeItem(key);
    }
    window.location.reload();
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: "24px", background: "#f3f6fb" }}>
        <div style={{ maxWidth: "760px", width: "100%", background: "#fff", border: "1px solid #d5deef", borderRadius: "16px", padding: "20px" }}>
          <h1 style={{ margin: 0, fontSize: "28px", color: "#0f172a" }}>UI 發生錯誤</h1>
          <p style={{ margin: "10px 0 0", color: "#475569", lineHeight: 1.6 }}>
            前端遇到執行期錯誤，已攔截避免白屏。可先重載並清除本地快取。
          </p>
          <pre style={{ marginTop: "14px", whiteSpace: "pre-wrap", wordBreak: "break-word", background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: "10px", padding: "12px", color: "#334155" }}>
            {this.state.message || "Unknown runtime error"}
          </pre>
          <button
            type="button"
            onClick={this.handleReset}
            style={{ marginTop: "12px", background: "#2558f5", color: "#fff", border: 0, borderRadius: "999px", padding: "10px 16px", cursor: "pointer" }}
          >
            重新載入（清快取）
          </button>
        </div>
      </div>
    );
  }
}
