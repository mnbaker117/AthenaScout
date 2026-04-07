import { useState, useEffect, useCallback } from "react";
import { useTheme } from "../theme";
import { api } from "../api";
import { usePersist } from "../hooks/usePersist";
import { Btn } from "../components/Btn";
import { Load } from "../components/Load";
import { VT } from "../components/VT";
import { SearchBar } from "../components/SearchBar";
import { BGrid, BList } from "../components/BookViews";
import { BookSidebar } from "../components/BookSidebar";

// ─── MAM Page ───────────────────────────────────────────────
export default function MAMPage({onNav}){const t=useTheme();
// Tab + section data
const[tab,setTab]=usePersist("mam_tab","upload");
const[books,setBooks]=useState([]);const[total,setTotal]=useState(0);
const[pg,setPg]=useState(1);const[q,setQ]=useState("");
const[sort,setSort]=usePersist("mam_sort","title");
const[vm,setVm]=usePersist("mam_vm","list");const[ld,setLd]=useState(true);
const perPage=50;
// Counts
const[counts,setCounts]=useState({upload:0,download:0,missing:0,unscanned:0});
// Scan
const[scanLimit,setScanLimit]=useState(100);
const[scanStarting,setScanStarting]=useState(false);
const[mamScan,setMamScan]=useState(null);
// Sidebar
const[sb,setSb]=useState(null);const[sbClosing,setSbClosing]=useState(false);

// Load counts + check running scan on mount
useEffect(()=>{
api.get("/mam/status").then(r=>{if(r.stats)setCounts({upload:r.stats.upload_candidates||0,download:r.stats.available_to_download||0,missing:r.stats.missing_everywhere||0,unscanned:r.stats.total_unscanned||0})}).catch(()=>{});
api.get("/mam/scan/status").then(r=>{if(r.running)setMamScan(r)}).catch(()=>{});
},[]);

// Load section data
const load=useCallback((page=1)=>{setLd(true);const p=new URLSearchParams({section:tab,search:q,sort,page:String(page),per_page:String(perPage)});api.get(`/mam/books?${p}`).then(d=>{setBooks(d.books||[]);setTotal(d.total||0);setPg(page);setLd(false)}).catch(()=>setLd(false))},[tab,q,sort]);
useEffect(()=>{load(1)},[load]);

// Scan polling
useEffect(()=>{if(!mamScan?.running)return;const iv=setInterval(()=>{api.get("/mam/scan/status").then(r=>{setMamScan(r);if(!r.running){clearInterval(iv);api.get("/mam/status").then(r2=>{if(r2.stats)setCounts({upload:r2.stats.upload_candidates||0,download:r2.stats.available_to_download||0,missing:r2.stats.missing_everywhere||0,unscanned:r2.stats.total_unscanned||0})}).catch(()=>{});load(1)}}).catch(()=>{})},5000);return()=>clearInterval(iv)},[mamScan?.running]);

const totalPages=Math.max(1,Math.ceil(total/perPage));
const switchTab=tb=>{setTab(tb);setQ("");setSort("title");setPg(1)};
const startScan=async()=>{setScanStarting(true);try{const r=await api.post(`/mam/scan?limit=${scanLimit}`);if(r.error){alert(r.error);setScanStarting(false);return}setMamScan({running:true,scanned:0,total:r.total||scanLimit,found:0,possible:0,not_found:0,errors:0,status:"scanning",type:"manual"})}catch{alert("Failed to start scan")}setScanStarting(false)};
const cancelScan=async()=>{try{await api.post("/mam/scan/cancel")}catch{}};
const closeSb=()=>{if(!sb)return;setSbClosing(true);setTimeout(()=>{setSb(null);setSbClosing(false)},200)};
const toggleSb=b=>{if(sb&&sb.id===b.id)closeSb();else{setSbClosing(false);setSb(b)}};
const onAction=async(act,id)=>{if(act==="hide")await api.post(`/books/${id}/hide`);if(act==="dismiss")await api.post(`/books/${id}/dismiss`);load(pg)};

const tabDefs=[{id:"upload",label:"Upload Candidates",color:t.grnt,icon:"↑",desc:"Books you own that aren't on MAM — potential uploads"},{id:"download",label:"Available on MAM",color:t.cyant||t.cyan,icon:"↓",desc:"Missing books found on MAM — ready to grab"},{id:"missing_everywhere",label:"Missing Everywhere",color:t.tg,icon:"∅",desc:"Neither you nor MAM have these books"}];
const activeTab=tabDefs.find(x=>x.id===tab)||tabDefs[0];
const countFor=id=>id==="upload"?counts.upload:id==="download"?counts.download:counts.missing;

return<div style={{display:"flex",flexDirection:"column",gap:16}}>

{/* Header */}
<div><h1 style={{fontSize:22,fontWeight:700,color:t.text,margin:0}}>MyAnonamouse</h1>
<p style={{fontSize:13,color:t.td,marginTop:4}}>{counts.unscanned>0?`${counts.unscanned} books not yet scanned`:"All books scanned"}</p></div>

{/* Manual Scan Card */}
<div style={{background:t.bg2,border:`1px solid ${t.border}`,borderRadius:12,padding:20}}>
<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",marginBottom:12}}>Manual Scan</div>

{mamScan?.running?<div>
<div style={{display:"flex",justifyContent:"space-between",fontSize:12,color:t.td,marginBottom:6}}>
<span>{mamScan.status==="paused"?"Paused (5 min between batches)":mamScan.status==="waiting (author scan running)"?"Waiting for author scan...":"Scanning..."} {mamScan.scanned||0} of {mamScan.total||"?"}</span>
</div>
<div style={{height:8,borderRadius:4,background:t.bg4,overflow:"hidden",marginBottom:8}}>
<div style={{width:`${mamScan.total?Math.round((mamScan.scanned||0)/(mamScan.total)*100):0}%`,height:"100%",borderRadius:4,background:t.accent,transition:"width 0.5s"}}/></div>
<div style={{display:"flex",gap:14,fontSize:12,marginBottom:10}}>
<span style={{color:t.grnt}}>Found: <b>{mamScan.found||0}</b></span>
<span style={{color:t.ylwt}}>Possible: <b>{mamScan.possible||0}</b></span>
<span style={{color:t.redt}}>Not found: <b>{mamScan.not_found||0}</b></span>
{(mamScan.errors||0)>0?<span style={{color:t.red}}>Errors: <b>{mamScan.errors}</b></span>:null}
</div>
<Btn size="sm" onClick={cancelScan} style={{background:t.red+"22",color:t.redt,border:`1px solid ${t.red}44`}}>Cancel scan</Btn>
</div>

:mamScan?.status==="complete"?<div>
<div style={{display:"flex",gap:14,fontSize:13,color:t.text2,padding:"8px 12px",background:t.grn+"15",borderRadius:8,border:`1px solid ${t.grn}33`,marginBottom:10}}>
<span>✓ Complete — {mamScan.scanned||0} scanned:</span>
<span style={{color:t.grnt}}>{mamScan.found||0} found</span>
<span style={{color:t.ylwt}}>{mamScan.possible||0} possible</span>
<span style={{color:t.redt}}>{mamScan.not_found||0} not found</span>
</div>
<div style={{display:"flex",alignItems:"center",gap:10}}>
<span style={{fontSize:12,color:t.tg}}>Scan</span>
<input type="number" value={scanLimit} onChange={e=>setScanLimit(parseInt(e.target.value)||"")} onBlur={()=>{if(!scanLimit||scanLimit<1)setScanLimit(100)}} style={{width:70,padding:"6px 8px",background:t.inp,border:`1px solid ${t.border}`,borderRadius:6,color:t.text2,fontSize:13,textAlign:"center"}}/>
<span style={{fontSize:12,color:t.tg}}>books</span>
<Btn size="sm" variant="accent" onClick={startScan} disabled={scanStarting||counts.unscanned===0}>{scanStarting?"Starting...":"Start Scan"}</Btn>
</div></div>

:<div style={{display:"flex",alignItems:"center",gap:10}}>
<span style={{fontSize:12,color:t.tg}}>Scan</span>
<input type="number" value={scanLimit} onChange={e=>setScanLimit(parseInt(e.target.value)||"")} onBlur={()=>{if(!scanLimit||scanLimit<1)setScanLimit(100)}} style={{width:70,padding:"6px 8px",background:t.inp,border:`1px solid ${t.border}`,borderRadius:6,color:t.text2,fontSize:13,textAlign:"center"}}/>
<span style={{fontSize:12,color:t.tg}}>books</span>
<Btn size="sm" variant="accent" onClick={startScan} disabled={scanStarting||counts.unscanned===0}>{scanStarting?"Starting...":"Start Scan"}</Btn>
{counts.unscanned===0?<span style={{fontSize:12,color:t.grnt}}>✓ All scanned</span>:null}
</div>}
</div>

{/* Tab Bar */}
<div style={{display:"flex",gap:0,borderBottom:`2px solid ${t.borderL}`,overflowX:"auto"}}>
{tabDefs.map(tb=><button key={tb.id} onClick={()=>switchTab(tb.id)} style={{padding:"10px 16px",background:"none",border:"none",borderBottom:tab===tb.id?`2px solid ${tb.color}`:"2px solid transparent",marginBottom:-2,cursor:"pointer",display:"flex",alignItems:"center",gap:6,fontSize:13,fontWeight:tab===tb.id?600:400,color:tab===tb.id?tb.color:t.tg,transition:"color 0.15s",whiteSpace:"nowrap",flexShrink:0}}><span>{tb.icon}</span><span>{tb.label}</span><span style={{background:tab===tb.id?tb.color+"22":t.bg4,color:tab===tb.id?tb.color:t.tg,padding:"1px 6px",borderRadius:10,fontSize:11,fontWeight:600}}>{countFor(tb.id)}</span></button>)}
</div>

{/* Section description + Upload button */}
<div style={{display:"flex",justifyContent:"space-between",alignItems:"center",flexWrap:"wrap",gap:8}}>
<p style={{fontSize:12,color:t.tg,fontStyle:"italic",margin:0}}>{activeTab.desc}</p>
{tab==="upload"?<a href="https://www.myanonamouse.net/tor/upload.php" target="_blank" rel="noopener noreferrer" style={{display:"inline-flex",alignItems:"center",gap:4,padding:"6px 14px",borderRadius:6,fontSize:12,fontWeight:600,textDecoration:"none",background:t.grn+"22",color:t.grnt,border:`1px solid ${t.grn}44`}}>Upload to MAM ↗</a>:null}
</div>

{/* Controls */}
<div className="bp-controls" style={{display:"flex",alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",gap:8}}>
<div style={{fontSize:13,color:t.td}}>{total} books</div>
<div className="bp-right" style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
<SearchBar value={q} onChange={v=>{setQ(v);setPg(1)}}/>
<select value={sort} onChange={e=>{setSort(e.target.value);setPg(1)}} style={{padding:"7px 10px",borderRadius:6,border:`1px solid ${t.border}`,background:t.inp,color:t.text2,fontSize:12}}><option value="title">Sort: Title</option><option value="author">Sort: Author</option><option value="date">Sort: Date</option><option value="series">Sort: Series</option></select>
<VT mode={vm} setMode={setVm}/>
</div></div>

{/* Book list */}
{ld?<Load/>:books.length===0?<div style={{textAlign:"center",padding:40,color:t.tg}}>No books in this section</div>:vm==="list"?<BList books={books} onAction={onAction} onBookClick={toggleSb} showAuthor={true} showMamLink={tab==="download"}/>:<BGrid books={books} onAction={onAction} onBookClick={toggleSb} showAuthor={true} showMamLink={tab==="download"}/>}

{/* Pagination */}
{totalPages>1&&!ld?<div style={{display:"flex",justifyContent:"center",gap:6,paddingTop:8}}>
<Btn size="sm" variant="ghost" onClick={()=>load(pg-1)} disabled={pg<=1}>← Prev</Btn>
<span style={{fontSize:12,color:t.tg,padding:"6px 8px"}}>{pg} / {totalPages}</span>
<Btn size="sm" variant="ghost" onClick={()=>load(pg+1)} disabled={pg>=totalPages}>Next →</Btn>
</div>:null}

{/* Sidebar */}
{sb?<BookSidebar book={sb} closing={sbClosing} onClose={closeSb} onAction={onAction} onEdit={()=>load(pg)}/>:null}

</div>}
