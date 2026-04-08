// Lightweight toast notification dispatcher.
//
// Replaces alert() / confirm() popups for transient status messages
// (scan started, scan rejected, MAM finished, etc). The actual
// rendering lives in components/Toaster.jsx — this module just fires
// a window event so any non-React caller (api.js error handlers,
// global window listeners, etc) can pop a toast without importing
// React.
//
// Usage:
//   import { toast } from "./lib/toast";
//   toast.info("Scan started for Brandon Sanderson");
//   toast.success("MAM scan complete");
//   toast.error("An author scan is already running");
//
// kinds: info | success | warn | error
//
// The Toaster component listens for "athenascout:toast" events and
// renders an iOS-style banner stack at the top of the viewport that
// auto-dismisses after ~5s and is click-to-dismiss.

function fire(kind, msg) {
  try {
    window.dispatchEvent(new CustomEvent("athenascout:toast", {
      detail: { kind, msg: String(msg ?? "") },
    }));
  } catch (_) {}
}

export const toast = {
  info:    (msg) => fire("info", msg),
  success: (msg) => fire("success", msg),
  warn:    (msg) => fire("warn", msg),
  error:   (msg) => fire("error", msg),
};
