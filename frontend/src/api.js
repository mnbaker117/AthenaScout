// API client for AthenaScout backend.
//
// Wraps fetch() with /api prefix and JSON handling.
// All methods throw on non-2xx responses with the status code as the message.
//
// Usage:
//   import { api } from "./api";
//   const books = await api.get("/books");
//   await api.post("/libraries/active", { slug: "my-library" });
//
// Phase 22B.3 Stage 2A: any 401 response dispatches a global
// "athenascout:auth-required" event that App.jsx listens for to redirect
// to the login screen. The thrown Error also carries a `.status` property
// so callers (and the wrapper below) can detect HTTP failures by code.
//
// Phase 22B.3 Batch B.1: all methods accept an optional AbortSignal as the
// last argument. Callers can pass `controller.signal` and call
// `controller.abort()` on component unmount / page change to drop in-flight
// requests. Aborted fetches throw DOMException("AbortError"), which callers
// can ignore via `.catch(e=>{if(e.name!=="AbortError")throw e})` or the
// `api.isAbort(e)` helper below.

// Throws an Error whose message is FastAPI's `detail` field if available,
// otherwise the bare status code. The error carries a `.status` property
// (numeric HTTP status) so callers can branch on specific failures.
async function _check(r){if(r.ok)return r.json();let detail=String(r.status);try{const j=await r.json();if(j&&j.detail)detail=typeof j.detail==="string"?j.detail:JSON.stringify(j.detail)}catch{}const err=new Error(detail);err.status=r.status;if(r.status===401)window.dispatchEvent(new CustomEvent("athenascout:auth-required"));throw err}
export const api={
  get:async(u,signal)=>_check(await fetch(`/api${u}`,signal?{signal}:undefined)),
  post:async(u,b,signal)=>{const o={method:"POST"};if(b){o.headers={"Content-Type":"application/json"};o.body=JSON.stringify(b)}if(signal)o.signal=signal;return _check(await fetch(`/api${u}`,o))},
  put:async(u,b,signal)=>{const o={method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)};if(signal)o.signal=signal;return _check(await fetch(`/api${u}`,o))},
  del:async(u,signal)=>_check(await fetch(`/api${u}`,signal?{method:"DELETE",signal}:{method:"DELETE"})),
  isAbort:e=>e&&(e.name==="AbortError"||e.code===20),
};
