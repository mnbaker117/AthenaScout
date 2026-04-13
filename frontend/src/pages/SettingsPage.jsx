import { useState, useEffect, useRef } from "react";
import { useTheme } from "../theme";
import { api } from "../api";
import { Btn } from "../components/Btn";
import { Spin } from "../components/Spin";
import { Load } from "../components/Load";

// ─── Language Multi-Select Dropdown ─────────────────────────
// MUST stay at module level (NOT inside SettingsPage). Defining
// helpers inside the parent render function makes React see them
// as new components every render and unmounts every input on each
// keystroke — the focus-loss bug we hit during early development.
function LangSelect({selected,options,onChange}){const t=useTheme();const[open,setOpen]=useState(false);const[q,setQ]=useState("");const ref=useRef(null);
useEffect(()=>{const h=e=>{if(ref.current&&!ref.current.contains(e.target))setOpen(false)};document.addEventListener("mousedown",h);return()=>document.removeEventListener("mousedown",h)},[]);
const filtered=(options||[]).filter(l=>l.toLowerCase().includes(q.toLowerCase()));
const toggle=lang=>{if(selected.includes(lang))onChange(selected.filter(l=>l!==lang));else onChange([...selected,lang])};
return<div ref={ref} style={{position:"relative",width:300}}>
<div onClick={()=>setOpen(!open)} style={{padding:"8px 12px",background:t.inp,border:`1px solid ${t.border}`,borderRadius:8,cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"space-between",minHeight:36}}><div style={{display:"flex",flexWrap:"wrap",gap:4}}>{selected.length===0?<span style={{color:t.tg,fontSize:13}}>Select languages...</span>:selected.map(l=><span key={l} style={{background:t.abg,color:t.ylwt,padding:"2px 8px",borderRadius:4,fontSize:11,fontWeight:500,display:"flex",alignItems:"center",gap:4}}>{l}<button onClick={e=>{e.stopPropagation();toggle(l)}} style={{background:"none",border:"none",color:t.ylwt,cursor:"pointer",padding:0,fontSize:13}}>×</button></span>)}</div><span style={{color:t.tg,fontSize:10}}>▼</span></div>
{open&&<div style={{position:"absolute",top:"100%",left:0,right:0,marginTop:4,background:t.bg2,border:`1px solid ${t.border}`,borderRadius:8,zIndex:50,maxHeight:240,overflow:"hidden",boxShadow:"0 4px 12px rgba(0,0,0,0.3)"}}>
<div style={{padding:8,borderBottom:`1px solid ${t.borderL}`}}><input autoFocus value={q} onChange={e=>setQ(e.target.value)} placeholder="Search languages..." style={{width:"100%",padding:"6px 10px",background:t.inp,border:`1px solid ${t.border}`,borderRadius:6,color:t.text2,fontSize:12}}/></div>
<div style={{maxHeight:200,overflowY:"auto"}}>{filtered.map(l=><div key={l} onClick={()=>toggle(l)} style={{padding:"8px 12px",cursor:"pointer",display:"flex",alignItems:"center",gap:8,fontSize:13,color:selected.includes(l)?t.ylwt:t.text2,background:selected.includes(l)?t.abg:"transparent"}}><span style={{width:16,height:16,borderRadius:4,border:`2px solid ${selected.includes(l)?t.accent:t.border}`,display:"flex",alignItems:"center",justifyContent:"center",fontSize:10,color:t.accent}}>{selected.includes(l)?"✓":""}</span>{l}</div>)}</div></div>}
</div>}

// ─── Settings Helpers (outside SettingsPage to prevent re-mount on state change) ───
function SF({label,desc,children,warn}){const t=useTheme();return<div style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"14px 0",borderBottom:`1px solid ${t.borderL}`}}><div style={{flex:1}}><div style={{fontSize:14,fontWeight:500,color:t.text2}}>{label}</div>{desc?<div style={{fontSize:12,color:t.tf,marginTop:2}}>{desc}</div>:null}{warn?<div style={{fontSize:11,color:t.ylwt,marginTop:2}}>⚠ {warn}</div>:null}</div><div>{children}</div></div>}
function STog({on,onToggle,disabled}){const t=useTheme();return<div onClick={disabled?undefined:onToggle} style={{width:44,height:24,borderRadius:12,background:on?t.grn:t.bg4,cursor:disabled?"not-allowed":"pointer",padding:3,transition:"background 0.2s",opacity:disabled?0.5:1}}><div style={{width:18,height:18,borderRadius:"50%",background:"#fff",transform:on?"translateX(20px)":"translateX(0)",transition:"transform 0.2s"}}/></div>}

function SSection({title,defaultOpen=true,children}){const t=useTheme();const[open,setOpen]=useState(defaultOpen);return<div style={{background:t.bg2,border:`1px solid ${t.border}`,borderRadius:12}}><div onClick={()=>setOpen(!open)} style={{display:"flex",alignItems:"center",gap:8,padding:"14px 20px",cursor:"pointer",userSelect:"none"}}><span style={{transform:open?"rotate(0)":"rotate(-90deg)",transition:"transform 0.2s",fontSize:11,color:t.tg}}>▼</span><span style={{fontSize:13,fontWeight:600,color:t.text,textTransform:"uppercase",letterSpacing:"0.05em"}}>{title}</span></div>{open?<div style={{padding:"0 20px 16px"}}>{children}</div>:null}</div>}

// ─── Settings ───────────────────────────────────────────────
export default function SettingsPage(){const t=useTheme();const[s,setS]=useState(null);const[sv,setSv]=useState(false);const[msg,setMsg]=useState("");
const[mamVld,setMamVld]=useState(false);const[mamRes,setMamRes]=useState(null);const[dragIdx,setDragIdx]=useState(null);const[testRun,setTestRun]=useState(false);const[testRes,setTestRes]=useState(null);const[newSrcPath,setNewSrcPath]=useState("");const[newSrcType,setNewSrcType]=useState("root");const[newSrcApp,setNewSrcApp]=useState("calibre");const[pathVld,setPathVld]=useState(false);const[pathRes,setPathRes]=useState(null);useEffect(()=>{api.get("/settings").then(setS).catch(console.error)},[]);
// Debounced author search for the "Clear scan data by author" field.
// Waits 300ms after the last keystroke before firing, and skips the
// search entirely for queries shorter than 2 characters. The naive
// onChange-fires-fetch-per-keystroke version triggered ~18 API calls
// for a 12-character author name; only the last result matters.
useEffect(()=>{const q=s?._scanClearQ||"";if(q.length<2){setS(o=>o&&o._scanClearResults?.length?{...o,_scanClearResults:[]}:o);return}const tm=setTimeout(()=>{api.get(`/authors?search=${encodeURIComponent(q)}`).then(r=>setS(o=>o?{...o,_scanClearResults:r.authors||[]}:o)).catch(()=>{})},300);return()=>clearTimeout(tm)},[s?._scanClearQ]);
const save=async()=>{setSv(true);setMsg("");try{const toSave={...s};if(s._editingKey&&s._newKey){toSave.hardcover_api_key=s._newKey}delete toSave._editingKey;delete toSave._newKey;delete toSave._editingMam;delete toSave._newMam;delete toSave._scanClearQ;delete toSave._scanClearResults;delete toSave._scanClearSel;delete toSave.hardcover_api_key_set;delete toSave.language_options;delete toSave._discovered_libraries;delete toSave._extra_mount_paths;delete toSave._newSrcApp;await api.post("/settings",toSave);setMsg("Saved!");upd("_editingKey",false);upd("_newKey","");const fresh=await api.get("/settings");setS(fresh);setTimeout(()=>setMsg(""),2000)}catch(e){setMsg("Error")}setSv(false)};
const doValidate=async()=>{setMamVld(true);setMamRes(null);try{const r=await api.post("/mam/validate");setMamRes(r);if(r.success){const fresh=await api.get("/settings");setS(fresh)}}catch(e){setMamRes({success:false,message:"Network error"})}setMamVld(false)};
const resetMam=async()=>{if(!confirm("Reset all MAM scan data? This clears mam_url and mam_status on every book. You will need to re-scan."))return;try{await api.post("/mam/reset");setMsg("MAM data cleared!");setTimeout(()=>setMsg(""),2000)}catch{}};
const reorderFmt=(from,to)=>{if(from===to)return;const arr=[...(s.mam_format_priority||[])];const[item]=arr.splice(from,1);arr.splice(to,0,item);upd("mam_format_priority",arr)};
const doTestScan=async()=>{setTestRun(true);setTestRes(null);try{const r=await api.post("/mam/test-scan");setTestRes(r)}catch(e){setTestRes({error:"Network error"})}setTestRun(false)};
// Logout: best-effort POST to clear the cookie, then hard-reload to
// reset all in-memory state and re-enter the auth flow cleanly.
const doLogout=async()=>{if(!confirm("Sign out of AthenaScout?"))return;try{await api.post("/auth/logout")}catch{}window.location.reload()};
// Local timeAgo with different return semantics from lib/format.js — returns null
// for falsy ts and lowercase strings ("just now", "5m ago"). DO NOT merge with shared one.
const timeAgo=ts=>{if(!ts)return null;const s=Math.floor(Date.now()/1000-ts);if(s<60)return"just now";if(s<3600)return`${Math.floor(s/60)}m ago`;if(s<86400)return`${Math.floor(s/3600)}h ago`;return`${Math.floor(s/86400)}d ago`};
if(!s)return<Load/>;const upd=(k,v)=>setS(o=>({...o,[k]:v}));
const ist={padding:"8px 12px",background:t.inp,border:`1px solid ${t.border}`,borderRadius:6,color:t.text2,fontSize:13};
const nist={...ist,width:80};
const numP=(key,def,min=0)=>({type:"number",min,value:s[key]===""?"":s[key]??def,onChange:e=>upd(key,e.target.value===""?"":parseInt(e.target.value)),onBlur:e=>{const v=parseInt(e.target.value);upd(key,isNaN(v)?def:Math.max(min,v))},style:nist});

return<div style={{paddingBottom:40}}>
<div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:20,flexWrap:"wrap",gap:12}}>
<h1 style={{fontSize:24,fontWeight:700,color:t.text,margin:0}}>Settings</h1>
<div style={{display:"flex",alignItems:"center",gap:12,flexWrap:"wrap"}}>
<Btn variant="accent" onClick={save} disabled={sv}>{sv?<Spin/>:"Save settings"}</Btn>
<Btn onClick={doLogout} title="Sign out of AthenaScout">Sign Out</Btn>
{msg&&<span style={{fontSize:13,color:["Saved!","Settings reset!","MAM data cleared!","Token saved!","Key saved!","Source data cleared!","All data cleared!","Libraries rescanned!"].includes(msg)?t.grnt:t.redt}}>{msg}</span>}
</div></div>

<div className="settings-grid" style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:20,alignItems:"start"}}>

{/* ═══════════ LEFT COLUMN ═══════════ */}
<div style={{display:"flex",flexDirection:"column",gap:20}}>

{/* ── LIBRARY ── */}
<SSection title="Library">

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"6px 0"}}>Discovered Libraries</div>
{(s._discovered_libraries||[]).length>0?<div style={{display:"flex",flexDirection:"column",gap:4,marginBottom:12}}>{(s._discovered_libraries||[]).map(l=><div key={l.slug} style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"8px 12px",borderRadius:6,background:l.active?t.accent+"12":t.bg4,border:`1px solid ${l.active?t.accent+"33":t.borderL}`}}><div><div style={{display:"flex",alignItems:"center",gap:6}}><span style={{fontSize:13}}>{l.content_type==="audiobook"?"🎧":"📖"}</span><span style={{fontSize:13,fontWeight:l.active?600:400,color:l.active?t.accent:t.text2}}>{l.name}{l.active?" (active)":""}</span><span style={{fontSize:10,padding:"1px 6px",borderRadius:4,background:t.bg4,color:t.tg}}>{l.app_type||"calibre"}</span></div><div style={{fontSize:11,color:t.tg,marginTop:1}}>{l.source_db_path}</div></div><span style={{fontSize:10,color:t.tg,fontFamily:"monospace"}}>{l.slug}</span></div>)}</div>:<div style={{fontSize:12,color:t.tg,padding:"8px 0",fontStyle:"italic"}}>No libraries discovered. Check your CALIBRE_PATH environment variable.</div>}

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"10px 0 6px"}}>Library Sources</div>
{(s.library_sources||[]).length>0?<div style={{display:"flex",flexDirection:"column",gap:4,marginBottom:8}}>{(s.library_sources||[]).map((src,i)=><div key={i} style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"6px 10px",borderRadius:6,background:t.bg4,border:`1px solid ${t.borderL}`}}><div><span style={{fontSize:12,color:t.text2}}>{src.path}</span><span style={{fontSize:10,color:t.tg,marginLeft:8}}>({src.type})</span><span style={{fontSize:10,color:t.tg,marginLeft:4}}>{src.app_type==="audiobookshelf"?"🎧":"📖"}</span></div><button onClick={()=>{const arr=[...(s.library_sources||[])];arr.splice(i,1);upd("library_sources",arr)}} style={{background:"none",border:"none",cursor:"pointer",color:t.redt,fontSize:14,padding:"0 4px"}}>×</button></div>)}</div>:<div style={{fontSize:12,color:t.tg,padding:"4px 0",fontStyle:"italic"}}>Using environment variable for library discovery. Add sources here to override.</div>}

{(s._extra_mount_paths||[]).length>0?<div style={{marginBottom:8}}><div style={{fontSize:11,color:t.tg,marginBottom:4}}>Available mount points (click to use as path):</div><div style={{display:"flex",flexWrap:"wrap",gap:4}}>{(s._extra_mount_paths||[]).map(p=><button key={p} onClick={()=>{setNewSrcPath(p);setPathRes(null)}} style={{padding:"3px 10px",borderRadius:4,fontSize:11,background:t.bg4,border:`1px solid ${t.borderL}`,color:t.accent,cursor:"pointer"}}>{p}</button>)}</div></div>:null}
<div style={{display:"flex",gap:6,alignItems:"flex-end",flexWrap:"wrap",padding:"4px 0 8px"}}>
<div style={{flex:1,minWidth:160}}><div style={{fontSize:11,color:t.tg,marginBottom:2}}>Path</div><input value={newSrcPath} onChange={e=>{setNewSrcPath(e.target.value);setPathRes(null)}} placeholder="/calibre" style={{...ist,width:"100%"}}/></div>
<div><div style={{fontSize:11,color:t.tg,marginBottom:2}}>Type</div><select value={newSrcType} onChange={e=>setNewSrcType(e.target.value)} style={{...ist,padding:"8px 10px"}}><option value="root">Root directory</option><option value="direct">Direct path</option></select></div>
<div><div style={{fontSize:11,color:t.tg,marginBottom:2}}>App</div><select value={newSrcApp||"calibre"} onChange={e=>setNewSrcApp(e.target.value)} style={{...ist,padding:"8px 10px"}}><option value="calibre">📖 Calibre (ebook)</option><option value="audiobookshelf" disabled>🎧 Audiobookshelf (coming soon)</option></select></div>
<Btn size="sm" onClick={async()=>{if(!newSrcPath.trim())return;setPathVld(true);setPathRes(null);try{const r=await api.post("/libraries/validate-path",{path:newSrcPath.trim(),type:newSrcType});setPathRes(r)}catch{setPathRes({valid:false,error:"Network error"})}setPathVld(false)}} disabled={pathVld||!newSrcPath.trim()}>{pathVld?"Validating...":"Validate"}</Btn>
<Btn size="sm" variant="accent" onClick={()=>{if(!newSrcPath.trim())return;const arr=[...(s.library_sources||[]),{path:newSrcPath.trim(),type:newSrcType,app_type:newSrcApp||"calibre"}];upd("library_sources",arr);setNewSrcPath("");setPathRes(null);setNewSrcApp("calibre")}} disabled={!newSrcPath.trim()}>Add</Btn>
</div>
{pathRes?<div style={{fontSize:12,padding:"4px 0",color:pathRes.valid?t.grnt:t.redt}}>{pathRes.valid?`✓ Found ${pathRes.libraries_found} library(s): ${pathRes.details.map(d=>d.name).join(", ")}`:`✗ ${pathRes.error}`}</div>:null}

<Btn size="sm" onClick={async()=>{try{await save();const r=await api.post("/libraries/rescan");if(r.libraries){const fresh=await api.get("/settings");setS(fresh)}setMsg("Libraries rescanned!")}catch{setMsg("Rescan failed")}}} style={{marginTop:4}}>Rescan libraries</Btn>

<div style={{borderTop:`1px solid ${t.borderL}`,marginTop:12,paddingTop:8}}>
<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"6px 0"}}>Calibre Integration</div>
</div>
<SF label="Calibre Web URL" desc="Full URL to your Calibre-Web instance, including port (e.g. http://192.168.1.100:8083). Enables deep links to individual books in the sidebar."><div style={{display:"flex",alignItems:"center",gap:8}}><input value={s.calibre_web_url||""} onChange={e=>upd("calibre_web_url",e.target.value)} placeholder="http://192.168.1.100:8083" style={{...ist,width:220}}/>{s.calibre_web_url?<a href={s.calibre_web_url} target="_blank" rel="noopener noreferrer" style={{fontSize:12,color:t.accent,textDecoration:"none"}}>Test ↗</a>:null}</div></SF>
<SF label="Calibre Library URL" desc="Full URL to your Calibre content server or management interface, including port (e.g. https://192.168.1.100:8181). Adds a quick-access button on the dashboard."><div style={{display:"flex",alignItems:"center",gap:8}}><input value={s.calibre_url||""} onChange={e=>upd("calibre_url",e.target.value)} placeholder="https://10.0.10.20:8787" style={{...ist,width:220}}/>{s.calibre_url?<a href={s.calibre_url} target="_blank" rel="noopener noreferrer" style={{fontSize:12,color:t.accent,textDecoration:"none"}}>Test ↗</a>:null}</div></SF>
<SF label="Library sync interval (minutes)" desc="Set to 0 to disable auto-sync"><input {...numP("library_sync_interval_minutes",60)}/></SF>

<div style={{borderTop:`1px solid ${t.borderL}`,marginTop:12,paddingTop:8}}>
<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"6px 0"}}>Hermeece Integration</div>
</div>
<SF label="Hermeece URL" desc="Full URL to your Hermeece instance (e.g. http://10.0.10.20:8686). Enables 'Send to Hermeece' buttons on Found MAM matches for automatic download."><div style={{display:"flex",alignItems:"center",gap:8}}><input value={s.hermeece_url||""} onChange={e=>upd("hermeece_url",e.target.value)} placeholder="http://10.0.10.20:8686" style={{...ist,width:220}}/>{s.hermeece_url?<a href={s.hermeece_url} target="_blank" rel="noopener noreferrer" style={{fontSize:12,color:t.accent,textDecoration:"none"}}>Test ↗</a>:null}</div></SF>

</SSection>

{/* ── SOURCES ── */}
<SSection title="Sources">

<SF label="Hardcover API Key" desc="Get from hardcover.app → Account → API">{s.hardcover_api_key_set&&!s._editingKey?<div style={{display:"flex",alignItems:"center",gap:12}}><span style={{fontSize:14,color:t.tm,letterSpacing:"3px"}}>••••••••</span><Btn size="sm" onClick={()=>upd("_editingKey",true)}>Change</Btn></div>:<div style={{display:"flex",flexDirection:"column",gap:6}}><input value={s._editingKey?s._newKey||"":s.hardcover_api_key||""} onChange={e=>s._editingKey?upd("_newKey",e.target.value):upd("hardcover_api_key",e.target.value)} placeholder="Bearer eyJ..." style={{...ist,width:220}}/>{s._editingKey?<div style={{display:"flex",gap:6,marginTop:2}}><Btn size="sm" variant="accent" onClick={async()=>{const nk=s._newKey||"";if(!nk)return;setSv(true);try{await api.post("/settings",{hardcover_api_key:nk});upd("_editingKey",false);upd("_newKey","");const fresh=await api.get("/settings");setS(fresh);setMsg("Key saved!")}catch{setMsg("Error")}setSv(false)}}>Save Key</Btn><Btn size="sm" variant="ghost" onClick={()=>upd("_editingKey",false)}>Cancel</Btn></div>:null}</div>}</SF>

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"10px 0 6px"}}>Sources (in priority order)</div>
<SF label="1. Goodreads (Primary)" desc="Web scraping, most complete for series & dates"><div style={{display:"flex",alignItems:"center",gap:10}}><span style={{fontSize:12,color:s.goodreads_enabled!==false?t.grnt:t.tg,fontWeight:600}}>{s.goodreads_enabled!==false?"Active":"Disabled"}</span><STog on={s.goodreads_enabled!==false} onToggle={()=>upd("goodreads_enabled",!(s.goodreads_enabled!==false))}/></div></SF>
<SF label="2. Hardcover" desc="GraphQL API, requires key above"><div style={{display:"flex",alignItems:"center",gap:10}}><span style={{fontSize:12,color:s.hardcover_api_key_set&&s.hardcover_enabled!==false?t.grnt:t.tg,fontWeight:600}}>{!s.hardcover_api_key_set?"No key set":(s.hardcover_enabled!==false?"Active":"Disabled")}</span><STog on={s.hardcover_enabled!==false} onToggle={()=>upd("hardcover_enabled",!(s.hardcover_enabled!==false))} disabled={!s.hardcover_api_key_set}/></div></SF>
<SF label="3. Kobo" desc="Web scraping for ebooks"><div style={{display:"flex",alignItems:"center",gap:10}}><span style={{fontSize:12,color:s.kobo_enabled?t.grnt:t.tg,fontWeight:600}}>{s.kobo_enabled?"Active":"Disabled"}</span><STog on={s.kobo_enabled} onToggle={()=>upd("kobo_enabled",!s.kobo_enabled)}/></div></SF>
<SF label="4. Amazon" desc="Web scraping for series data. Best source for standalone vs series confirmation. Slower due to anti-bot measures."><div style={{display:"flex",alignItems:"center",gap:10}}><span style={{fontSize:12,color:s.amazon_enabled?t.grnt:t.tg,fontWeight:600}}>{s.amazon_enabled?"Active":"Disabled"}</span><STog on={!!s.amazon_enabled} onToggle={()=>upd("amazon_enabled",!s.amazon_enabled)}/></div></SF>
<SF label="5. IBDB" desc="REST API, no auth required. Supplementary source for ISBN and publisher backfill."><div style={{display:"flex",alignItems:"center",gap:10}}><span style={{fontSize:12,color:s.ibdb_enabled?t.grnt:t.tg,fontWeight:600}}>{s.ibdb_enabled?"Active":"Disabled"}</span><STog on={!!s.ibdb_enabled} onToggle={()=>upd("ibdb_enabled",!s.ibdb_enabled)}/></div></SF>
<SF label="6. Google Books" desc="Public API, no auth required. Supplementary source for ISBN, publisher, and description."><div style={{display:"flex",alignItems:"center",gap:10}}><span style={{fontSize:12,color:s.google_books_enabled?t.grnt:t.tg,fontWeight:600}}>{s.google_books_enabled?"Active":"Disabled"}</span><STog on={!!s.google_books_enabled} onToggle={()=>upd("google_books_enabled",!s.google_books_enabled)}/></div></SF>

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"10px 0 6px"}}>Rate Limits (seconds between requests)</div>
<SF label="Goodreads"><input {...numP("rate_goodreads",2)}/></SF>
<SF label="Hardcover"><input {...numP("rate_hardcover",1)}/></SF>
<SF label="Kobo"><input {...numP("rate_kobo",3)}/></SF>
<SF label="Amazon"><input {...numP("rate_amazon",2)}/></SF>
<SF label="IBDB"><input {...numP("rate_ibdb",1)}/></SF>
<SF label="Google Books"><input {...numP("rate_google_books",0)}/></SF>

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"10px 0 6px"}}>Languages</div>
<SF label="Preferred languages" desc="Only track books in these languages"><LangSelect selected={s.languages||[]} options={s.language_options||[]} onChange={v=>upd("languages",v)}/></SF>

<SF label="Source lookup interval (days)" desc="Set to 0 to disable auto-lookup"><input {...numP("lookup_interval_days",3)}/></SF>

</SSection>

</div>

{/* ═══════════ RIGHT COLUMN ═══════════ */}
<div style={{display:"flex",flexDirection:"column",gap:20}}>

{/* ── MAM ── */}
<SSection title="MyAnonamouse">

<SF label="MAM Session ID" desc={'Get from MAM → Preferences → Security → Generate Session. Set "Allow Session to set Dynamic Seedbox" to No. Works with both IP-locked and ASN-locked tokens.'}>{s.mam_session_id&&!s._editingMam?<div style={{display:"flex",alignItems:"center",gap:12}}><span style={{fontSize:14,color:t.tm,letterSpacing:"3px"}}>••••••••</span><Btn size="sm" onClick={()=>{upd("_editingMam",true);upd("_newMam","")}}>Change</Btn></div>:<div style={{display:"flex",flexDirection:"column",gap:6}}><input value={s._editingMam?s._newMam||"":s.mam_session_id||""} onChange={e=>s._editingMam?upd("_newMam",e.target.value):upd("mam_session_id",e.target.value)} placeholder="Paste session token..." style={{...ist,width:220}}/>{s._editingMam?<div style={{display:"flex",gap:6,marginTop:2}}><Btn size="sm" variant="accent" onClick={async()=>{const nk=s._newMam||"";if(!nk)return;setSv(true);try{await api.post("/settings",{mam_session_id:nk});upd("_editingMam",false);upd("_newMam","");const fresh=await api.get("/settings");setS(fresh);setMamRes(null);setMsg("Token saved!")}catch{setMsg("Error")}setSv(false)}}>Save token</Btn><Btn size="sm" variant="ghost" onClick={()=>{upd("_editingMam",false);upd("_newMam","")}}>Cancel</Btn></div>:null}</div>}</SF>

<SF label="Validate connection" desc="Tests search auth against MAM servers"><div style={{display:"flex",flexDirection:"column",gap:6,alignItems:"flex-end"}}><div style={{display:"flex",alignItems:"center",gap:10}}><Btn size="sm" variant="accent" onClick={doValidate} disabled={mamVld||!s.mam_session_id}>{mamVld?<><Spin/> Testing...</>:"Validate"}</Btn>{mamRes&&mamRes.success?<span style={{fontSize:12,fontWeight:600,color:t.grnt}}>✓ Connected</span>:!mamRes&&s.mam_validation_ok!==false&&s.last_mam_validated_at?<span style={{fontSize:11,color:t.tg}}>Last validated: {timeAgo(s.last_mam_validated_at)}</span>:null}</div>{mamRes&&!mamRes.success?<div style={{fontSize:12,color:t.redt,maxWidth:300,textAlign:"right"}}>{mamRes.message||"Validation failed"}</div>:!mamRes&&s.mam_validation_ok===false?<div style={{fontSize:12,color:t.redt}}>⚠ Last validation failed — update token and re-validate</div>:null}</div></SF>

<SF label="Enable MAM features" desc={s.mam_enabled?"MAM integration active across the app":"Validate session first, then enable"}><STog on={!!s.mam_enabled} onToggle={async()=>{try{const r=await api.post("/mam/toggle");upd("mam_enabled",r.enabled);window.dispatchEvent(new CustomEvent("athenascout:mam-state-changed"))}catch{}}} disabled={!s.mam_session_id}/></SF>

{s.mam_enabled?<>

<SF label="Request delay (seconds)" desc="Pause between MAM API calls during scans. Minimum 1 second."><input {...numP("rate_mam",2,1)}/></SF>

<SF label="Format priority" desc="Drag to reorder. Top format is preferred when multiple are available on MAM."><div style={{display:"flex",flexDirection:"column",gap:4,minWidth:120}}>{(s.mam_format_priority||[]).map((fmt,i)=><div key={fmt} draggable onDragStart={()=>setDragIdx(i)} onDragOver={e=>{e.preventDefault()}} onDrop={()=>{reorderFmt(dragIdx,i);setDragIdx(null)}} onDragEnd={()=>setDragIdx(null)} style={{display:"flex",alignItems:"center",gap:8,padding:"5px 10px",borderRadius:6,background:dragIdx===i?t.accent+"22":t.bg4,border:`1px solid ${dragIdx===i?t.accent:t.border}`,cursor:"grab",fontSize:13,color:t.text2,transition:"background 0.15s"}}><span style={{color:t.tg,fontSize:11,fontWeight:600,width:16}}>{i+1}</span><span style={{fontWeight:500,textTransform:"uppercase",letterSpacing:"0.05em"}}>{fmt}</span><span style={{marginLeft:"auto",color:t.tg,fontSize:10}}>⋮⋮</span></div>)}</div></SF>

<div style={{padding:"10px 0",fontSize:12,color:t.tg,fontStyle:"italic",borderBottom:`1px solid ${t.borderL}`}}>Note: MAM session tokens expire periodically. If scans start failing, generate a new token and re-validate.</div>

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"10px 0 6px"}}>Scan Settings</div>
<SF label="MAM scan interval (minutes)" desc="How often automatic MAM scans run. Default 360 (6 hours). Set to 0 to disable. Scheduled scans run a single 150-book batch per cycle — use the Dashboard MAM Scan button to scan all remaining books, or Full Library Scan below for a long bounded run."><input {...numP("mam_scan_interval_minutes",360)}/></SF>

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"10px 0 6px"}}>Test Scan</div>
<div style={{padding:"8px 0 12px"}}>
<div style={{display:"flex",alignItems:"center",gap:10,marginBottom:8}}>
<Btn size="sm" variant="accent" onClick={doTestScan} disabled={testRun}>{testRun?<><Spin/> Scanning 10 books...</>:"Run test scan (10 books)"}</Btn>
</div>
{testRes?<div style={{background:t.bg4,borderRadius:8,padding:"10px 14px",fontSize:13}}>{testRes.error?<span style={{color:t.redt}}>{testRes.error}</span>:<div style={{display:"flex",gap:16,flexWrap:"wrap",color:t.text2}}><span>Scanned: <b>{testRes.scanned||0}</b></span><span style={{color:t.grnt}}>Found: <b>{testRes.found||0}</b></span><span style={{color:t.ylwt}}>Possible: <b>{testRes.possible||0}</b></span><span style={{color:t.redt}}>Not found: <b>{testRes.not_found||0}</b></span>{testRes.errors>0?<span style={{color:t.red}}>Errors: <b>{testRes.errors}</b></span>:null}</div>}</div>:null}
</div>

{/* Reset / wipe operations moved to the full-width Data Management
    section at the bottom of the page so all destructive operations
    live in one visually-distinct place. The MAM Full Library Scan
    trigger lives on the Dashboard under Heavy Tasks. */}

</>:null}

</SSection>

{/* ── SCANNING ── */}
<SSection title="Scanning">

<SF label="Author scanning" desc="Enable source scanning for authors (Goodreads, Hardcover, etc). Disabling cancels any running scan."><STog on={s.author_scanning_enabled!==false} onToggle={async()=>{try{const r=await api.post("/scanning/author/toggle");upd("author_scanning_enabled",r.enabled)}catch{}}}/></SF>
<SF label="Library-only source scans" desc="Only enrich metadata on books you already own — never add Missing or Upcoming books from source scans. Useful for polishing your existing library before turning the discovery firehose on. Series links and metadata still update on owned books."><STog on={s.author_scan_owned_only===true} onToggle={()=>upd("author_scan_owned_only",!s.author_scan_owned_only)}/></SF>
<SF label="Exclude audiobook editions" desc="Filter out audiobook-only editions during source scans. Catches titles with 'Audible Audio', 'Audio CD', '(Narrator)' markers. Prevents audiobook entries from cluttering an ebook library."><STog on={s.exclude_audiobooks!==false} onToggle={()=>upd("exclude_audiobooks",!(s.exclude_audiobooks!==false))}/></SF>
<SF label="MAM scanning" desc={s.mam_scanning_enabled!==false?"MAM scans active — disable to stop all MAM scanning":"MAM scanning disabled — MAM features (badges, pages) still work"}>{s.mam_enabled?<STog on={s.mam_scanning_enabled!==false} onToggle={async()=>{try{const r=await api.post("/scanning/mam/toggle");upd("mam_scanning_enabled",r.enabled)}catch{}}}/>:<span style={{fontSize:12,color:t.tg}}>MAM not enabled</span>}</SF>
<SF label="Verbose logging" desc="Show detailed debug output in Docker logs. Logs individual book decisions, page visit details, and merge operations."><STog on={!!s.verbose_logging} onToggle={()=>upd("verbose_logging",!s.verbose_logging)}/></SF>
<div style={{padding:"10px 0 0",fontSize:12,color:t.tg,fontStyle:"italic"}}>Disabling a scan type cancels any running scan and prevents future scans. MAM features (badges, pages) remain visible when MAM scanning is off. Set scan intervals to 0 to disable only scheduled scans.</div>

</SSection>

{/* ── DATA MANAGEMENT (in right column) ── */}
<SSection title="Data Management">

<div style={{padding:"4px 0 10px",fontSize:12,color:t.tg,fontStyle:"italic"}}>Per-author and bulk cleanup operations. The MAM Full Library Scan and Sources Full Re-Scan triggers live on the Dashboard under Heavy Tasks — this section is just for clearing existing data.</div>

<SF label="Clear scan data by author" desc={s.mam_enabled?"Search for authors, then clear their source or MAM scan data":"Search for authors, then clear their source scan data (enable MAM to also clear MAM data)"}>
<div style={{display:"flex",flexDirection:"column",gap:8,minWidth:220}}>
<div style={{position:"relative"}}>
<input value={s._scanClearQ||""} onChange={e=>upd("_scanClearQ",e.target.value)} placeholder="Search authors..." style={{width:"100%",padding:"6px 8px",background:t.inp,border:`1px solid ${t.border}`,borderRadius:6,color:t.text2,fontSize:13}}/>
{(s._scanClearResults||[]).length>0?<div style={{position:"absolute",top:"100%",left:0,right:0,maxHeight:160,overflowY:"auto",background:t.bg2,border:`1px solid ${t.border}`,borderRadius:"0 0 6px 6px",zIndex:10,boxShadow:"0 4px 12px rgba(0,0,0,0.3)"}}>
{(s._scanClearResults||[]).map(a=><div key={a.id} onClick={()=>{const cur=s._scanClearSel||[];if(!cur.find(x=>x.id===a.id))upd("_scanClearSel",[...cur,{id:a.id,name:a.name}]);upd("_scanClearQ","");upd("_scanClearResults",[])}} style={{padding:"6px 10px",cursor:"pointer",fontSize:12,color:t.text2,borderBottom:`1px solid ${t.borderL}`}}>{a.name} <span style={{color:t.tg}}>({a.total_books||0} books)</span></div>)}
</div>:null}
</div>
{(s._scanClearSel||[]).length>0?<div style={{display:"flex",flexWrap:"wrap",gap:4}}>
{(s._scanClearSel||[]).map(a=><span key={a.id} style={{display:"inline-flex",alignItems:"center",gap:4,padding:"2px 8px",borderRadius:4,fontSize:11,background:t.purb,color:t.purt,border:`1px solid ${t.pur}33`}}>{a.name}<button onClick={()=>upd("_scanClearSel",(s._scanClearSel||[]).filter(x=>x.id!==a.id))} style={{background:"none",border:"none",cursor:"pointer",color:t.purt,padding:0,fontSize:13}}>×</button></span>)}
<button onClick={()=>upd("_scanClearSel",[])} style={{background:"none",border:"none",cursor:"pointer",color:t.tg,fontSize:11,padding:"2px 4px"}}>clear all</button>
</div>:null}
{(s._scanClearSel||[]).length>0?<div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
<Btn size="sm" onClick={async()=>{if(!confirm(`Clear SOURCE scan data for ${(s._scanClearSel||[]).length} author(s)? This will DELETE all discovered books.`))return;setSv(true);try{await api.post("/authors/clear-scan-data",{author_ids:(s._scanClearSel||[]).map(a=>a.id),clear_source:true,clear_mam:false});upd("_scanClearSel",[]);setMsg("Source data cleared!")}catch{setMsg("Error")}setSv(false)}} style={{background:t.ylw+"22",color:t.ylwt,border:`1px solid ${t.ylw}44`}}>Clear Source</Btn>
{s.mam_enabled?<Btn size="sm" onClick={async()=>{if(!confirm(`Clear MAM scan data for ${(s._scanClearSel||[]).length} author(s)?`))return;setSv(true);try{await api.post("/authors/clear-scan-data",{author_ids:(s._scanClearSel||[]).map(a=>a.id),clear_source:false,clear_mam:true});upd("_scanClearSel",[]);setMsg("MAM data cleared!")}catch{setMsg("Error")}setSv(false)}} style={{background:t.cyan+"22",color:t.cyant,border:`1px solid ${t.cyan}44`}}>Clear MAM</Btn>:null}
{s.mam_enabled?<Btn size="sm" onClick={async()=>{if(!confirm(`Clear ALL scan data for ${(s._scanClearSel||[]).length} author(s)? This will DELETE all discovered books AND reset MAM status.`))return;setSv(true);try{await api.post("/authors/clear-scan-data",{author_ids:(s._scanClearSel||[]).map(a=>a.id),clear_source:true,clear_mam:true});upd("_scanClearSel",[]);setMsg("All data cleared!")}catch{setMsg("Error")}setSv(false)}} style={{background:t.red+"22",color:t.redt,border:`1px solid ${t.red}44`}}>Clear Both</Btn>:null}
</div>:null}
</div>
</SF>

<SF label="Wipe ALL source scan data" desc="Delete every discovered (non-Calibre, non-owned) book and reset every author's last-scanned timestamp. Owned books and MAM data are kept. Use this for a full source clean-slate.">
<Btn size="sm" onClick={async()=>{if(!confirm("Reset ALL source scan data?\n\nThis will DELETE every discovered book across the entire library and reset every author's last-scanned timestamp so future scans treat them as never-scanned.\n\nOwned books and MAM data are NOT affected.\n\nThis cannot be undone."))return;setSv(true);try{const r=await api.post("/sources/reset");setMsg(`Source data reset — ${r.books_deleted||0} books deleted`);setTimeout(()=>setMsg(""),4000)}catch(e){setMsg(`Error: ${e.message||e}`)}setSv(false)}} style={{background:t.red+"22",color:t.redt,border:`1px solid ${t.red}44`}}>Wipe all source data</Btn>
</SF>

{s.mam_enabled?<SF label="Wipe ALL MAM scan data" desc="Clear mam_url and mam_status on every book. Every book becomes 'unscanned' and will be re-checked on the next MAM scan.">
<Btn size="sm" onClick={resetMam} style={{background:t.red+"22",color:t.redt,border:`1px solid ${t.red}44`}}>Wipe all MAM data</Btn>
</SF>:null}

</SSection>

</div>

</div>

{/* ── DANGER ZONE (full-width) ── */}
<div style={{marginTop:20}}>
<div style={{background:t.bg2,border:`1px solid ${t.red}44`,borderRadius:12}}>
<div style={{display:"flex",alignItems:"center",gap:8,padding:"14px 20px",borderBottom:`1px solid ${t.red}22`}}>
<span style={{fontSize:13,fontWeight:600,color:t.redt,textTransform:"uppercase",letterSpacing:"0.05em",display:"flex",alignItems:"center",gap:8}}><span style={{fontSize:14}}>⚠</span> Danger Zone</span>
</div>
<div style={{padding:"14px 20px",display:"flex",justifyContent:"space-between",alignItems:"center",gap:16,flexWrap:"wrap"}}>
<div style={{flex:"1 1 300px"}}><div style={{fontSize:14,fontWeight:500,color:t.text2}}>Reset everything</div><div style={{fontSize:12,color:t.tf,marginTop:2}}>Clears API keys, MAM session, Calibre URLs, source toggles, and every other customization. You'll need to re-enter all values — Docker environment variables are only used for initial setup and will not be restored.</div></div>
<Btn onClick={async()=>{if(!confirm("Reset ALL settings to defaults?\n\nThis will clear your API keys, MAM session, Calibre URLs, source toggles, and all other customizations.\n\nYou will need to re-enter any values — Docker environment variables are only used for initial setup and will not be restored.\n\nThis cannot be undone."))return;setSv(true);try{await api.post("/settings/reset");const fresh=await api.get("/settings");setS(fresh);setMamRes(null);setTestRes(null);setMsg("Settings reset!")}catch{setMsg("Error")}setSv(false)}} style={{background:t.red+"22",color:t.redt,border:`1px solid ${t.red}44`,fontWeight:600,whiteSpace:"nowrap"}}>Reset all settings to defaults</Btn>
</div>
</div>
</div>

</div>}
