import { useState, useEffect, useCallback } from "react";
import { useTheme } from "../theme";
import { api } from "../api";
import { Ic } from "../icons";
import { usePersist } from "../hooks/usePersist";
import { Btn } from "../components/Btn";
import { Spin } from "../components/Spin";
import { Load } from "../components/Load";
import { PB } from "../components/PB";
import { VT } from "../components/VT";
import { Section } from "../components/Section";
import { BGrid, BList } from "../components/BookViews";
import { BookSidebar } from "../components/BookSidebar";
import { toast } from "../lib/toast";

// ─── Inline Series (for Author Detail) ─────────────────────
// NOTE: defined at module level (NOT inside AuthorDetailPage) to preserve
// component identity across re-renders. Inlining inside the parent would
// remount inputs on every keystroke (the focus-loss bug).
function IS({series,vm,onAction,onBookClick,collapsed,authorId}){const t=useTheme();const[ld,setLd]=useState(false);const[bks,setBks]=useState(null);const load=()=>{if(bks)return;setLd(true);api.get(`/series/${series.id}`).then(d=>{setBks(d.books||[]);setLd(false)}).catch(()=>setLd(false))};useEffect(()=>{load()},[]);
const isMulti=!!series.multi_author;
const header=isMulti?<span>{series.name} <span style={{fontSize:11,color:useTheme().cyant,fontWeight:600,textTransform:"none",background:useTheme().cyan+"22",padding:"2px 8px",borderRadius:4,marginLeft:4}}>shared series</span></span>:series.name;
// Separate regular books from omnibus entries for display
const regular=bks?bks.filter(b=>!b.is_omnibus):null;
const omnibus=bks?bks.filter(b=>b.is_omnibus):null;
// Count excludes omnibus entries
const regCount=regular?regular.length:(series.book_count||0);
const ownCount=regular?regular.filter(b=>b.owned===1).length:(series.owned_count||0);
const countStr=isMulti?`${ownCount}/${regCount} · ${series.book_count||0} total`:`${ownCount}/${regCount}`;
return<Section title={header} count={countStr} ownedCount={ownCount} totalCount={regCount} defaultOpen={!collapsed}>{ld?<Load/>:bks?<>
{vm==="list"?<BList books={regular} onAction={onAction} onBookClick={onBookClick} showAuthor={isMulti} highlightAuthorId={authorId}/>:<BGrid books={regular} onAction={onAction} onBookClick={onBookClick} showAuthor={isMulti} highlightAuthorId={authorId}/>}
{omnibus&&omnibus.length>0?<><div style={{display:"flex",alignItems:"center",gap:8,margin:"12px 0 8px"}}><div style={{flex:1,height:1,background:t.borderL}}/><span style={{fontSize:10,fontWeight:600,color:t.tg,textTransform:"uppercase",letterSpacing:"0.06em",flexShrink:0}}>Omnibus / Collections</span><div style={{flex:1,height:1,background:t.borderL}}/></div>
{vm==="list"?<BList books={omnibus} onAction={onAction} onBookClick={onBookClick} showAuthor={isMulti} highlightAuthorId={authorId}/>:<BGrid books={omnibus} onAction={onAction} onBookClick={onBookClick} showAuthor={isMulti} highlightAuthorId={authorId}/>}</>:null}
</>:null}</Section>}

// ─── Standalone Section ─────────────────────────────────────
function SA({books,vm,onAction,onBookClick,collapsed}){return<Section title="Standalone" count={books.length} defaultOpen={!collapsed}>{vm==="list"?<BList books={books} onAction={onAction} onBookClick={onBookClick}/>:<BGrid books={books} onAction={onAction} onBookClick={onBookClick}/>}</Section>}

// ─── Author Detail ──────────────────────────────────────────
export default function AuthorDetailPage({authorId,onNav}){const t=useTheme();const[a,setA]=useState(null);const[ld,setLd]=useState(true);const[ref,setRef]=useState(false);const[mamRef,setMamRef]=useState(false);const[vm,setVm]=usePersist("adp_vm","grid");const[rk,setRk]=useState(0);const[sb,setSb]=useState(null);const[sbClosing,setSbClosing]=useState(false);const[allCol,setAllCol]=useState(false);const[mamOn,setMamOn]=useState(false);
const[penLinks,setPenLinks]=useState([]);const[penQ,setPenQ]=useState("");const[penResults,setPenResults]=useState([]);const[penBusy,setPenBusy]=useState(false);
useEffect(()=>{if(!authorId)return;api.get(`/authors/${authorId}/pen-names`).then(r=>setPenLinks(r.links||[])).catch(()=>{})},[authorId]);
useEffect(()=>{if(penQ.length<2){setPenResults([]);return}const tm=setTimeout(()=>{api.get(`/authors?search=${encodeURIComponent(penQ)}`).then(r=>setPenResults((r.authors||[]).filter(x=>x.id!==parseInt(authorId)))).catch(()=>{})},300);return()=>clearTimeout(tm)},[penQ,authorId]);
const linkPen=async(aliasId)=>{setPenBusy(true);try{await api.post("/authors/link-pen-names",{canonical_author_id:parseInt(authorId),alias_author_id:aliasId});const r=await api.get(`/authors/${authorId}/pen-names`);setPenLinks(r.links||[]);setPenQ("");setPenResults([]);toast.success("Pen name linked")}catch(e){toast.error(e.message||"Link failed")}setPenBusy(false)};
const unlinkPen=async(linkId)=>{try{await api.del(`/authors/pen-name-link/${linkId}`);setPenLinks(penLinks.filter(l=>l.id!==linkId));toast.success("Pen name unlinked")}catch{}};
useEffect(()=>{api.get("/mam/status").then(r=>setMamOn(!!r.enabled)).catch(()=>{})},[]);
const closeSb=()=>{if(!sb)return;setSbClosing(true);setTimeout(()=>{setSb(null);setSbClosing(false)},200)};
const toggleSb=b=>{if(sb&&sb.id===b.id)closeSb();else{setSbClosing(false);setSb(b)}};
const loadA=useCallback((signal)=>{setLd(true);api.get(`/authors/${authorId}`,signal).then(d=>{setA(d);setLd(false)}).catch(e=>{if(!api.isAbort(e))console.error(e)})},[authorId]);useEffect(()=>{const c=new AbortController();loadA(c.signal);return()=>c.abort()},[loadA]);
// Author scans run as background tasks on the server. The flow:
//   1. Dispatch `athenascout:scan-started` so the Dashboard widget
//      shows it immediately.
//   2. Fire the POST without awaiting completion (the server returns
//      `{status: "started"}`).
//   3. Listen for `athenascout:scan-completed` from App's unified
//      poller and refresh the page data when it fires.
const refresh=async(full=false)=>{if(ref)return;setRef(true);try{const r=await api.post(`/authors/${authorId}/${full?"full-rescan":"lookup"}`);toast.info(`${full?"Full re-scan":"Source scan"} started for ${r.author||"author"}`);window.dispatchEvent(new CustomEvent("athenascout:scan-started"))}catch(e){toast.error(e.message||"Scan failed to start");setRef(false)}};
const scanMam=async()=>{if(mamRef)return;setMamRef(true);try{const r=await api.post(`/mam/scan-author/${authorId}`);if(r.status==="complete"){toast.info(r.message||"No un-scanned books for this author");setMamRef(false)}else{toast.info(`MAM scan started — ${r.total||0} books`);window.dispatchEvent(new CustomEvent("athenascout:scan-started"))}}catch(e){toast.error(e.message||"MAM scan failed to start");setMamRef(false)}};
// Listen for scan completion (broadcast by the unified poller in
// App-level Dashboard) and refresh this page's author data + book grid.
useEffect(()=>{const onDone=()=>{loadA();setRk(k=>k+1);setRef(false);setMamRef(false)};window.addEventListener("athenascout:scan-completed",onDone);return()=>window.removeEventListener("athenascout:scan-completed",onDone)},[loadA]);
const onAction=async(act,id)=>{const scrollY=window.scrollY;if(act==="hide")await api.post(`/books/${id}/hide`);if(act==="dismiss")await api.post(`/books/${id}/dismiss`);if(act==="delete")await api.del(`/books/${id}`);await loadA();requestAnimationFrame(()=>window.scrollTo(0,scrollY))};
if(ld)return<Load/>;if(!a)return<div style={{color:t.tf}}>Not found</div>;
const saOwned=(a.standalone_books||[]).filter(b=>b.owned===1).length;const saTotal=(a.standalone_books||[]).length;const serOwned=(a.series||[]).reduce((n,s)=>n+(s.owned_count||0),0);const serTotal=(a.series||[]).reduce((n,s)=>n+(s.book_count||0),0);const oc=saOwned+serOwned;const total=saTotal+serTotal;
return<div style={{display:"flex",flexDirection:"column",gap:24}}>
{/* Sticky author header */}
<div style={{position:"sticky",top:56,zIndex:40,background:t.bg+"ee",backdropFilter:"blur(8px)",padding:"12px 0"}}>
<Btn onClick={()=>onNav("authors")} style={{marginBottom:12,background:t.bg4,border:`1px solid ${t.border}`,borderRadius:8,padding:"8px 16px",fontSize:14}}>← Back to Authors</Btn>
<div className="author-header" style={{display:"flex",gap:20,alignItems:"flex-start"}}>
{a.image_url?<img src={a.image_url} alt="" style={{width:72,height:72,borderRadius:"50%",objectFit:"cover"}}/>:<div style={{width:72,height:72,borderRadius:"50%",background:t.bg4,display:"flex",alignItems:"center",justifyContent:"center",fontSize:28,fontWeight:700,color:t.tg}}>{a.name.charAt(0)}</div>}
<div style={{flex:1}}><h1 style={{fontSize:22,fontWeight:700,color:t.text,margin:0}}>{a.name}</h1>
{a.bio?<p style={{fontSize:13,color:t.td,marginTop:6,lineHeight:1.5,maxHeight:60,overflow:"hidden"}}>{a.bio}</p>:null}
<div style={{display:"flex",gap:16,marginTop:8,fontSize:13}}><span style={{color:t.grnt}}>{oc} owned</span><span style={{color:t.ylwt}}>{total-oc} missing</span><span style={{color:t.purt}}>{(a.series||[]).length} series</span></div>
{/* Pen-name links inline with author identity */}
<div style={{display:"flex",alignItems:"center",gap:6,marginTop:6,flexWrap:"wrap"}}>{penLinks.map(l=>{const other=l.canonical_author_id===parseInt(authorId)?{id:l.alias_author_id,name:l.alias_name}:{id:l.canonical_author_id,name:l.canonical_name};return<span key={l.id} style={{display:"inline-flex",alignItems:"center",gap:4,padding:"2px 8px",borderRadius:4,fontSize:11,background:t.purb,color:t.purt,border:`1px solid ${t.pur}33`}}><span style={{color:t.tg,fontSize:10}}>aka</span> <button onClick={()=>onNav("author",other.id)} style={{background:"none",border:"none",color:t.purt,cursor:"pointer",padding:0,fontSize:11,fontWeight:500}}>{other.name}</button><button onClick={()=>unlinkPen(l.id)} style={{background:"none",border:"none",color:t.tg,cursor:"pointer",padding:0,fontSize:12}}>×</button></span>})}<button onClick={()=>setPenQ(penQ||" ")} style={{background:"none",border:"none",color:t.td,cursor:"pointer",padding:"4px 10px",fontSize:12,borderRadius:5,border:`1px dashed ${t.border}`}}>+ pen name</button></div>
<div style={{marginTop:8}}><PB owned={oc} total={total}/></div></div>
<div className="author-controls" style={{display:"flex",gap:6,alignItems:"center",flexShrink:0}}>
<Btn size="sm" variant="ghost" onClick={loadA} title="Refresh" style={{height:34,width:34,padding:0,display:"inline-flex",alignItems:"center",justifyContent:"center"}}>{Ic.refresh}</Btn>
<Btn size="sm" variant="ghost" onClick={()=>setAllCol(!allCol)} title={allCol?"Expand All":"Collapse All"} style={{height:34,width:34,padding:0,display:"inline-flex",alignItems:"center",justifyContent:"center"}}>{allCol?Ic.expand:Ic.collapse}</Btn>
<VT mode={vm} setMode={setVm}/>
<Btn size="sm" onClick={()=>refresh(false)} disabled={ref} style={{height:34}}>{ref?<Spin/>:Ic.sync} Re-sync</Btn>
<Btn size="sm" onClick={()=>{if(confirm("Full Re-Scan visits every book page to refresh metadata. This may take a few minutes. Continue?"))refresh(true)}} disabled={ref} style={{height:34,background:t.ylw+"22",color:t.ylwt,border:`1px solid ${t.ylw}44`}}>{ref?<Spin/>:Ic.refresh} Full</Btn>
{mamOn?<Btn size="sm" onClick={scanMam} disabled={mamRef} title="Scan all un-scanned books for this author against MAM" style={{height:34,background:t.cyan+"22",color:t.cyant,border:`1px solid ${t.cyan}44`}}>{mamRef?<Spin/>:null} Scan MAM</Btn>:null}
</div></div></div>
{/* ── Pen Name Search (shown when user clicks "+ pen name") ── */}
{penQ.length>0?<div style={{background:t.bg2,border:`1px solid ${t.border}`,borderRadius:10,padding:"10px 14px",display:"flex",alignItems:"center",gap:8,fontSize:12}}>
<span style={{fontWeight:600,color:t.tg,textTransform:"uppercase",letterSpacing:"0.05em",flexShrink:0}}>Link Pen Name</span>
<div style={{position:"relative",flex:1,maxWidth:280}}><input autoFocus value={penQ.trim()?penQ:""} onChange={e=>setPenQ(e.target.value)} placeholder="Search for author..." style={{padding:"6px 10px",background:t.inp,border:`1px solid ${t.border}`,borderRadius:6,color:t.text2,fontSize:12,width:"100%"}}/>
{penResults.length>0?<div style={{position:"absolute",top:"100%",left:0,right:0,background:t.bg2,border:`1px solid ${t.border}`,borderRadius:"0 0 6px 6px",zIndex:10,boxShadow:"0 4px 12px rgba(0,0,0,0.3)",maxHeight:160,overflowY:"auto"}}>{penResults.map(r=><div key={r.id} onClick={()=>linkPen(r.id)} style={{padding:"8px 12px",cursor:"pointer",fontSize:12,color:t.text2,borderBottom:`1px solid ${t.borderL}`}}>{r.name} <span style={{color:t.tg}}>({r.total_books||0} books)</span></div>)}</div>:null}</div>
<button onClick={()=>{setPenQ("");setPenResults([])}} style={{background:"none",border:"none",color:t.tg,cursor:"pointer",fontSize:14,padding:"0 4px"}}>×</button>
</div>:null}
{(a.series||[]).map(s=><IS key={`${s.id}_${rk}`} series={s} vm={vm} onAction={onAction} onBookClick={toggleSb} collapsed={allCol} authorId={authorId}/>)}
{(a.standalone_books||[]).length>0?<SA books={a.standalone_books} vm={vm} onAction={onAction} onBookClick={toggleSb} collapsed={allCol}/>:null}
{sb?<BookSidebar book={sb} closing={sbClosing} onClose={closeSb} onAction={onAction} onEdit={loadA}/>:null}
</div>}
