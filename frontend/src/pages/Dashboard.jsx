import { useState, useEffect } from "react";
import { useTheme } from "../theme";
import { api } from "../api";
import { Ic } from "../icons";
import { pct, timeAgo } from "../lib/format";
import { Btn } from "../components/Btn";
import { Spin } from "../components/Spin";
import { Load } from "../components/Load";

export default function Dashboard({onNav,libs=[],activeLib="",switchLib}){const t=useTheme();const[d,setD]=useState(null);const[sy,setSy]=useState(false);const[lookupScan,setLookupScan]=useState(null);const[mamScan,setMamScan]=useState(null);useEffect(()=>{api.get("/stats").then(setD).catch(console.error)},[]);
useEffect(()=>{api.get("/lookup/status").then(r=>{if(r.running||(r.status&&r.status!=="idle"))setLookupScan(r)}).catch(()=>{});api.get("/mam/scan/status").then(r=>{if(r.running||r.status==="complete")setMamScan(r)}).catch(()=>{})},[]);
useEffect(()=>{if(!lookupScan?.running)return;const iv=setInterval(()=>{api.get("/lookup/status").then(r=>{setLookupScan(r);if(!r.running){clearInterval(iv);api.get("/stats").then(setD)}}).catch(()=>{})},3000);return()=>clearInterval(iv)},[lookupScan?.running]);
useEffect(()=>{if(!mamScan?.running)return;const iv=setInterval(()=>{api.get("/mam/scan/status").then(r=>{setMamScan(r);if(!r.running)clearInterval(iv)}).catch(()=>{})},5000);return()=>clearInterval(iv)},[mamScan?.running]);
useEffect(()=>{if(mamScan?.running)return;const iv=setInterval(()=>{api.get("/mam/scan/status").then(r=>{if(r.running)setMamScan(r)}).catch(()=>{})},30000);return()=>clearInterval(iv)},[mamScan?.running]);
if(!d)return<Load/>;
const p=pct(d.owned_books,d.total_books);
return<div style={{display:"flex",flexDirection:"column",gap:24}}>

{libs.length>1?<div style={{marginBottom:16,display:"flex",alignItems:"center",gap:12}}><span style={{fontSize:13,fontWeight:500,color:t.tf}}>Library:</span><select value={activeLib} onChange={e=>switchLib(e.target.value)} style={{padding:"7px 28px 7px 12px",borderRadius:8,border:`1px solid ${t.border}`,background:t.bg2,color:t.accent,fontSize:14,fontWeight:600,cursor:"pointer",appearance:"none",WebkitAppearance:"none",backgroundImage:`url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23888'/%3E%3C/svg%3E")`,backgroundRepeat:"no-repeat",backgroundPosition:"right 10px center"}}>{libs.map(l=><option key={l.slug} value={l.slug}>{l.content_type==="audiobook"?"🎧 ":"📖 "}{l.name}</option>)}</select></div>:null}
{/* Hero */}
<div style={{background:t.bg2,border:`1px solid ${t.border}`,borderRadius:16,padding:28}}>
<div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:20}}>
<div><h1 style={{fontSize:26,fontWeight:700,color:t.text,margin:0}}>Your Library</h1>
<p style={{fontSize:14,color:t.td,marginTop:4}}>{d.owned_books} of {d.total_books} books owned</p></div>
<div style={{textAlign:"right"}}><span style={{fontSize:32,fontWeight:700,color:p===100?t.grnt:p>75?t.ylwt:t.text}}>{p}%</span>
<div style={{fontSize:11,color:t.tg}}>complete</div></div></div>
<div style={{height:8,borderRadius:4,background:t.bg4,overflow:"hidden"}}><div style={{width:`${p}%`,height:"100%",borderRadius:4,background:p===100?t.grn:p>50?`linear-gradient(90deg,${t.grn},${t.ylw})`:t.ylw,transition:"width 0.5s"}}/></div>
</div>

{/* Stat cards */}
<div className="dash-stats" style={{display:"grid",gridTemplateColumns:"repeat(auto-fit, minmax(140px, 1fr))",gap:12}}>
{[
  {label:"Owned",value:d.owned_books,color:t.grnt,icon:"📚",nav:()=>onNav("library")},
  {label:"Missing",value:d.missing_books,color:t.ylwt,icon:"🔍",nav:()=>onNav("missing")},
  {label:"New Finds",value:d.new_books,color:t.redt,icon:"✨"},
  {label:"Authors",value:d.authors,color:t.purt,icon:"✍",nav:()=>onNav("authors")},
  {label:"Series",value:d.total_series,color:t.cyant,icon:"📖"},
  {label:"Upcoming",value:d.upcoming_books||0,color:t.cyant,icon:"📅",nav:()=>onNav("upcoming")},
].map(c=><div key={c.label} onClick={c.nav} style={{background:t.bg2,border:`1px solid ${t.border}`,borderRadius:12,padding:"16px 18px",cursor:c.nav?"pointer":"default",transition:"border-color 0.2s"}}><div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}><span style={{fontSize:20}}>{c.icon}</span><span style={{fontSize:24,fontWeight:700,color:c.color}}>{c.value}</span></div><div style={{fontSize:12,color:t.td,marginTop:6}}>{c.label}</div></div>)}
</div>

{d.mam_enabled&&d.mam?<div onClick={()=>onNav("mam")} style={{background:t.bg2,border:`1px solid ${t.border}`,borderRadius:12,padding:"14px 20px",cursor:"pointer",display:"flex",alignItems:"center",gap:20,flexWrap:"wrap",transition:"border-color 0.2s"}}>
<span style={{fontSize:13,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.04em"}}>MAM</span>
<div style={{display:"flex",alignItems:"center",gap:6}}><span style={{fontSize:16,color:t.grnt}}>↑</span><span style={{fontSize:20,fontWeight:700,color:t.grnt}}>{d.mam.upload_candidates||0}</span><span style={{fontSize:12,color:t.td}}>Upload Candidates</span></div>
<div style={{display:"flex",alignItems:"center",gap:6}}><span style={{fontSize:16,color:t.cyant}}>↓</span><span style={{fontSize:20,fontWeight:700,color:t.cyant}}>{d.mam.available_to_download||0}</span><span style={{fontSize:12,color:t.td}}>Available on MAM</span></div>
<div style={{display:"flex",alignItems:"center",gap:6}}><span style={{fontSize:16,color:t.tg}}>∅</span><span style={{fontSize:20,fontWeight:700,color:t.tg}}>{d.mam.missing_everywhere||0}</span><span style={{fontSize:12,color:t.td}}>Missing Everywhere</span></div>
{d.mam.total_unscanned>0?<div style={{marginLeft:"auto",fontSize:12,color:t.ylwt,fontStyle:"italic"}}>{d.mam.total_unscanned} unscanned</div>:null}
</div>:null}

{/* Actions */}
<div style={{background:t.bg2,border:`1px solid ${t.border}`,borderRadius:12,padding:20,display:"flex",gap:20,flexWrap:"wrap"}}>
<div style={{flex:"1 1 320px"}}>
<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",marginBottom:12}}>Actions</div>
<div style={{display:"flex",gap:10,flexWrap:"wrap",alignItems:"center"}}>
<Btn variant="accent" onClick={async()=>{setSy(true);try{await api.post("/sync/calibre")}catch{}setSy(false);api.get("/stats").then(setD)}} disabled={sy}>{sy?<Spin/>:Ic.sync} Sync Library</Btn>
<Btn onClick={async()=>{try{const r=await api.post("/sync/lookup");if(r.error){alert(r.error)}else if(r.due===0){const st=await api.get("/lookup/status");setLookupScan(st)}else{setLookupScan({running:true,checked:0,total:r.due||0,current_author:"",new_books:0,status:"scanning",type:"lookup"})}}catch{}}} disabled={lookupScan?.running}>{lookupScan?.running&&lookupScan?.type==="lookup"?<Spin/>:Ic.search} Scan Sources</Btn>
<Btn variant="ghost" onClick={async()=>{if(!confirm("Full Re-Scan visits every book page to refresh all metadata. This can take several minutes for large libraries. Continue?"))return;try{const r=await api.post("/sync/full-rescan");if(r.error){alert(r.error)}else{setLookupScan({running:true,checked:0,total:0,current_author:"",new_books:0,status:"scanning",type:"full_rescan"})}}catch{}}} disabled={lookupScan?.running}>{lookupScan?.running&&lookupScan?.type==="full_rescan"?<Spin/>:Ic.refresh} Full Re-Scan</Btn>
{d.mam_enabled&&d.mam_scanning_enabled!==false?<Btn onClick={async()=>{try{const r=await api.post("/mam/scan");if(r.error){alert(r.error)}else{setMamScan({running:true,scanned:0,total:r.total||0,found:0,possible:0,not_found:0,errors:0,status:"scanning",type:"manual"})}}catch{}}} disabled={mamScan?.running}>{mamScan?.running?<Spin/>:Ic.search} MAM Scan</Btn>:null}
</div>
<div style={{display:"flex",gap:16,marginTop:12,fontSize:12,color:t.tg}}>
<span>{d.last_calibre_check?.at?`Last checked: ${timeAgo(d.last_calibre_check.at)}${d.last_calibre_check.synced?" (synced)":" (no changes)"}`:`Last sync: ${timeAgo(d.last_calibre_sync?.finished_at)}`}</span>
<span>Last lookup: {timeAgo(d.last_lookup?.finished_at)}</span>
</div>

{/* ── Scan Progress ── */}
{lookupScan&&lookupScan.status!=="idle"?<div style={{marginTop:12,background:t.bg4,borderRadius:8,padding:"10px 14px"}}>{lookupScan.running?<div>
<div style={{display:"flex",justifyContent:"space-between",fontSize:12,color:t.td,marginBottom:6}}>
<span>{lookupScan.type==="full_rescan"?"Full Re-Scan":"Scanning sources..."} {lookupScan.current_author?`— ${lookupScan.current_author}`:""}</span>
<span style={{fontSize:11,color:t.tg}}>{lookupScan.checked} of {lookupScan.total} authors</span></div>
<div style={{height:6,borderRadius:3,background:t.bg,overflow:"hidden",marginBottom:6}}><div style={{width:`${lookupScan.total>0?Math.round(lookupScan.checked/lookupScan.total*100):0}%`,height:"100%",borderRadius:3,background:t.accent,transition:"width 0.5s"}}/></div>
<div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}><span style={{fontSize:11,color:t.tg}}>New books found: <b style={{color:t.grnt}}>{lookupScan.new_books}</b></span><Btn size="sm" onClick={async()=>{try{await api.post("/lookup/cancel");const r=await api.get("/lookup/status");setLookupScan(r)}catch{}}} style={{background:t.red+"22",color:t.redt,border:`1px solid ${t.red}44`,padding:"2px 8px",fontSize:11}}>Stop</Btn></div>
</div>:<div style={{fontSize:13,color:lookupScan.status==="complete"?t.grnt:t.redt}}>{lookupScan.status==="complete"?`${lookupScan.type==="full_rescan"?"Full Re-Scan":"Source Scan"} Complete — ${lookupScan.checked} authors checked, ${lookupScan.new_books} new books found`:`Source Scan: ${lookupScan.status}`}</div>}</div>:null}

{mamScan&&mamScan.status!=="idle"?<div style={{marginTop:12,background:t.bg4,borderRadius:8,padding:"10px 14px"}}>{mamScan.running?<div>
<div style={{display:"flex",justifyContent:"space-between",fontSize:12,color:t.td,marginBottom:6}}>
<span>{mamScan.status==="paused"?"Paused — resuming in 5 min":mamScan.status==="waiting (author scan running)"?"Waiting for author scan...":mamScan.type==="scheduled"?"Scheduled scan running...":"Scanning MAM..."}{" "}{mamScan.scanned} of {mamScan.total} books{mamScan.remaining?(()=>{const rem=mamScan.remaining-(mamScan.scanned||0);return rem>0?` (${rem.toLocaleString()} total remaining)`:""})():""}</span>
<span style={{fontSize:11,textTransform:"capitalize",color:t.tg}}>{mamScan.type||"scan"}</span></div>
<div style={{height:6,borderRadius:3,background:t.bg,overflow:"hidden",marginBottom:6}}><div style={{width:`${mamScan.total>0?Math.round(mamScan.scanned/mamScan.total*100):0}%`,height:"100%",borderRadius:3,background:mamScan.status==="paused"?t.ylw:t.accent,transition:"width 0.5s"}}/></div>
<div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}><div style={{display:"flex",gap:12,fontSize:11,color:t.tg}}><span style={{color:t.grnt}}>Found: {mamScan.found}</span><span style={{color:t.ylwt}}>Possible: {mamScan.possible}</span><span style={{color:t.redt}}>Not found: {mamScan.not_found}</span>{mamScan.errors>0?<span style={{color:t.red}}>Errors: {mamScan.errors}</span>:null}</div><Btn size="sm" onClick={async()=>{try{await api.post("/mam/scan/cancel");const r=await api.get("/mam/scan/status");setMamScan(r)}catch{}}} style={{background:t.red+"22",color:t.redt,border:`1px solid ${t.red}44`,padding:"2px 8px",fontSize:11}}>Stop</Btn></div>
</div>:<div style={{fontSize:13}}><span style={{color:mamScan.status==="complete"?t.grnt:t.redt}}>{mamScan.status==="complete"?(()=>{const rem=mamScan.remaining!=null?mamScan.remaining-(mamScan.scanned||0):(mamScan.total||0)-(mamScan.scanned||0);return`MAM Scan Complete — ${mamScan.scanned} scanned: ${mamScan.found} found, ${mamScan.possible} possible, ${mamScan.not_found} not found${mamScan.errors>0?`, ${mamScan.errors} errors`:""}${rem>0?` · ${rem.toLocaleString()} unscanned`:""}`})():`MAM Scan: ${mamScan.status}`}</span></div>}</div>:null}

{d.mam_enabled?<div style={{fontSize:11,color:t.tg,marginTop:6,fontStyle:"italic"}}>MAM Scan checks all books missing MAM data (100 per batch, 5-min pauses between batches).</div>:null}
</div>
<div style={{flex:"0 0 auto",display:"flex",flexDirection:"column",gap:6,borderLeft:`1px solid ${t.borderL}`,paddingLeft:20,justifyContent:"center"}}>
{d.calibre_web_url?<button onClick={()=>window.open(d.calibre_web_url,"_blank")} style={{display:"flex",alignItems:"center",gap:8,padding:"8px 14px",background:t.accent+"18",border:`1px solid ${t.accent}33`,borderRadius:8,cursor:"pointer",fontSize:13,fontWeight:500,color:t.accent,whiteSpace:"nowrap"}}>📖 Calibre Web <span style={{fontSize:10,opacity:0.6}}>↗</span></button>:null}
{d.calibre_url?<button onClick={()=>window.open(d.calibre_url,"_blank")} style={{display:"flex",alignItems:"center",gap:8,padding:"8px 14px",background:t.pur+"18",border:`1px solid ${t.pur}33`,borderRadius:8,cursor:"pointer",fontSize:13,fontWeight:500,color:t.purt,whiteSpace:"nowrap"}}>📚 Calibre Library <span style={{fontSize:10,opacity:0.6}}>↗</span></button>:null}
<button onClick={()=>onNav("hidden")} style={{display:"flex",alignItems:"center",gap:8,padding:"8px 14px",background:t.bg4,border:`1px solid ${t.border}`,borderRadius:8,cursor:"pointer",fontSize:13,fontWeight:500,color:t.td,whiteSpace:"nowrap"}}>{Ic.hide} Hidden ({d.hidden_books||0})</button>
</div>
</div>

{/* Quick nav */}
<div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit, minmax(140px, 1fr))",gap:10}}>
{[{label:"Library",icon:"📖",pg:"library"},{label:"Authors",icon:"◉",pg:"authors"},{label:"Missing",icon:"◌",pg:"missing"},{label:"Upcoming",icon:"📅",pg:"upcoming"},{label:"Settings",icon:"⚙",pg:"settings"}].map(n=><button key={n.pg} onClick={()=>onNav(n.pg)} style={{background:t.bg2,border:`1px solid ${t.border}`,borderRadius:10,padding:"14px 16px",cursor:"pointer",display:"flex",alignItems:"center",gap:10,fontSize:14,fontWeight:500,color:t.text2}}><span style={{fontSize:18}}>{n.icon}</span>{n.label}</button>)}
</div>
</div>}
