import { Component } from "react";

export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error("ErrorBoundary caught:", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div style={{ padding: 32, textAlign: "center", color: "#ef6464" }}>
            <h2 style={{ fontSize: 18, marginBottom: 8 }}>
              Something went wrong
            </h2>
            <p style={{ fontSize: 13, color: "#8a8e9b", marginBottom: 16 }}>
              {this.state.error?.message || "Unknown error"}
            </p>
            <button
              onClick={() => {
                this.setState({ hasError: false, error: null });
                this.props.onReset?.();
              }}
              style={{
                padding: "8px 16px",
                borderRadius: 8,
                border: "1px solid #2e3242",
                background: "#1f2230",
                color: "#d6d8df",
                cursor: "pointer",
                fontSize: 14,
              }}
            >
              Go to Dashboard
            </button>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
