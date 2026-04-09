// API client for the AthenaScout backend.
//
// Thin wrapper over fetch() that prefixes /api, handles JSON request
// bodies and responses, and throws an Error on any non-2xx response.
// The thrown Error carries a `.status` property (numeric HTTP code) so
// callers can branch on specific failures.
//
// Two app-wide behaviors worth knowing about:
//
//  1. Any 401 response dispatches the `athenascout:auth-required`
//     window event. App.jsx listens for it and drops the user back to
//     the login screen — no callers have to handle the auth case
//     individually.
//
//  2. Every method accepts an optional AbortSignal as its last
//     argument. Pass `controller.signal` and call `controller.abort()`
//     on component unmount / page change to drop in-flight requests.
//     Aborted fetches throw `DOMException("AbortError")`; the helper
//     `api.isAbort(e)` recognizes the shape so call sites can ignore
//     them cleanly.
//
// Usage:
//   import { api } from "./api";
//   const books = await api.get("/books");
//   await api.post("/libraries/active", { slug: "my-library" });

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
