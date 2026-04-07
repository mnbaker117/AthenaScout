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

// ─── Inline Series (for Author Detail) ─────────────────────
// NOTE: defined at module level (NOT inside AuthorDetailPage) to preserve
// component identity across re-renders. Inlining inside the parent would
// remount inputs on every keystroke (the focus-loss bug).
function IS({series,vm,onAction,onBookClick,collapsed,authorId}){const t=useTheme();const[ld,setLd]=useState(false);const[bks,setBks]=useState(null);const load=()=>{if(bks)return;setLd(true);api.get(`/series/${series.id}`).then(d=>{setBks(d.books||[]);setLd(false)}).catch(()=>setLd(false))};useEffect(()=>{load()},[]);
const isMulti=!!series.multi_author;
const header=isMulti?<span>{series.name} <span style={{fontSize:11,color:useTheme().cyant,fontWeight:600,textTransform:"none",background:useTheme().cyan+"22",padding:"2px 8px",borderRadius:4,marginLeft:4}}>shared series</span></span>:series.name;
const countStr=isMulti?`${series.owned_count||0}/${series.author_book_count||0} · ${series.book_count||0} total`:`${series.owned_count||0}/${series.book_count||0}`;
return<Section title={header} count={countStr} ownedCount={series.owned_count} totalCount={isMulti?series.author_book_count:series.book_count} defaultOpen={!collapsed}>{ld?<Load/>:bks?(vm==="list"?<BList books={bks} onAction={onAction} onBookClick={onBookClick} showAuthor={isMulti} highlightAuthorId={authorId}/>:<BGrid books={bks} onAction={onAction} onBookClick={onBookClick} showAuthor={isMulti} highlightAuthorId={authorId}/>):null}</Section>}

// ─── Standalone Section ─────────────────────────────────────
function SA({books,vm,onAction,onBookClick,collapsed}){return<Section title="Standalone" count={books.length} defaultOpen={!collapsed}>{vm==="list"?<BList books={books} onAction={onAction} onBookClick={onBookClick}/>:<BGrid books={books} onAction={onAction} onBookClick={onBookClick}/>}</Section>}

// ─── Author Detail ──────────────────────────────────────────
export default function AuthorDetailPage({authorId,onNav}){const t=useTheme();const[a,setA]=useState(null);const[ld,setLd]=useState(true);const[ref,setRef]=useState(false);const[vm,setVm]=usePersist("adp_vm","grid");const[rk,setRk]=useState(0);const[sb,setSb]=useState(null);const[sbClosing,setSbClosing]=useState(false);const[allCol,setAllCol]=useState(false);
const closeSb=()=>{if(!sb)return;setSbClosing(true);setTimeout(()=>{setSb(null);setSbClosing(false)},200)};
const toggleSb=b=>{if(sb&&sb.id===b.id)closeSb();else{setSbClosing(false);setSb(b)}};
const loadA=useCallback(()=>{setLd(true);api.get(`/authors/${authorId}`).then(d=>{setA(d);setLd(false)}).catch(console.error)},[authorId]);useEffect(()=>{loadA()},[loadA]);
const refresh=async(full=false)=>{setRef(true);try{await api.post(`/authors/${authorId}/${full?"full-rescan":"lookup"}`);await loadA();setRk(k=>k+1)}catch(e){console.error(e)}setRef(false)};
const onAction=async(act,id)=>{if(act==="hide")await api.post(`/books/${id}/hide`);if(act==="dismiss")await api.post(`/books/${id}/dismiss`);if(act==="delete")await api.del(`/books/${id}`);loadA()};
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
<div style={{marginTop:8}}><PB owned={oc} total={total}/></div></div>
<div className="author-controls" style={{display:"flex",gap:6,alignItems:"center",flexShrink:0}}>
<Btn size="sm" variant="ghost" onClick={loadA} title="Refresh" style={{height:34,width:34,padding:0,display:"inline-flex",alignItems:"center",justifyContent:"center"}}>{Ic.refresh}</Btn>
<Btn size="sm" variant="ghost" onClick={()=>setAllCol(!allCol)} title={allCol?"Expand All":"Collapse All"} style={{height:34,width:34,padding:0,display:"inline-flex",alignItems:"center",justifyContent:"center"}}>{allCol?Ic.expand:Ic.collapse}</Btn>
<VT mode={vm} setMode={setVm}/>
<Btn size="sm" onClick={()=>refresh(false)} disabled={ref} style={{height:34}}>{ref?<Spin/>:Ic.sync} Re-sync</Btn>
<Btn size="sm" variant="ghost" onClick={()=>{if(confirm("Full Re-Scan visits every book page to refresh metadata. This may take a few minutes. Continue?"))refresh(true)}} disabled={ref} style={{height:34}}>{Ic.refresh} Full</Btn>
</div></div></div>
{(a.series||[]).map(s=><IS key={`${s.id}_${rk}`} series={s} vm={vm} onAction={onAction} onBookClick={toggleSb} collapsed={allCol} authorId={authorId}/>)}
{(a.standalone_books||[]).length>0?<SA books={a.standalone_books} vm={vm} onAction={onAction} onBookClick={toggleSb} collapsed={allCol}/>:null}
{sb?<BookSidebar book={sb} closing={sbClosing} onClose={closeSb} onAction={onAction} onEdit={loadA}/>:null}
</div>}
