// API client for AthenaScout backend.
//
// Wraps fetch() with /api prefix and JSON handling.
// All methods throw on non-2xx responses with the status code as the message.
//
// Usage:
//   import { api } from "./api";
//   const books = await api.get("/books");
//   await api.post("/libraries/active", { slug: "my-library" });

export const api={get:async u=>{const r=await fetch(`/api${u}`);if(!r.ok)throw new Error(r.status);return r.json()},post:async(u,b)=>{const o={method:"POST"};if(b){o.headers={"Content-Type":"application/json"};o.body=JSON.stringify(b)}const r=await fetch(`/api${u}`,o);if(!r.ok)throw new Error(r.status);return r.json()},put:async(u,b)=>{const r=await fetch(`/api${u}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});if(!r.ok)throw new Error(r.status);return r.json()},del:async u=>{const r=await fetch(`/api${u}`,{method:"DELETE"});if(!r.ok)throw new Error(r.status);return r.json()}};
