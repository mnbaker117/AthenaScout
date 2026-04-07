// sessionStorage-backed React state hook.
//
// Behaves like useState() but persists the value to sessionStorage under
// the prefixed key "cl_<key>". On mount, reads any existing value from
// sessionStorage. On change, writes the new value back. Storage failures
// (e.g., privacy mode) are silently swallowed and the hook degrades to
// plain useState.
//
// Usage:
//   const [page, setPage] = usePersist("current_page", "dashboard");
import { useState, useEffect } from "react";

export function usePersist(key,def){const k=`cl_${key}`;const[v,setV]=useState(()=>{try{const s=sessionStorage.getItem(k);return s?JSON.parse(s):def}catch{return def}});useEffect(()=>{try{sessionStorage.setItem(k,JSON.stringify(v))}catch{}},[k,v]);return[v,setV]}
