import { useState, useEffect, useCallback } from "react";
import { useTheme } from "../theme";
import { api } from "../api";
import { Ic } from "../icons";
import { usePersist } from "../hooks/usePersist";
import { Btn } from "../components/Btn";
import { Load } from "../components/Load";
import { VT } from "../components/VT";
import { SearchBar } from "../components/SearchBar";
import { Section } from "../components/Section";
import { BGrid, BList } from "../components/BookViews";
import { BookSidebar } from "../components/BookSidebar";
import { ExportModal } from "../components/ExportModal";

// ─── Books Page (Library/Missing/Upcoming) ──────────────────
export default function BooksPage({title,subtitle,apiPath="/books",extraParams={},showAuthor=true,exportFilter}){const t=useTheme();const[bks,setBks]=useState([]);const[total,setTotal]=useState(0);const[pg,setPg]=useState(1);const[ld,setLd]=useState(true);const[q,setQ]=usePersist(`bp_${title}_q`,"");const[vm,setVm]=usePersist(`bp_${title}_vm`,"grid");const[grp,setGrp]=usePersist(`bp_${title}_grp`,"all");const[sort,setSort]=usePersist(`bp_${title}_sort`,"title");const[sb,setSb]=useState(null);const[sbClosing,setSbClosing]=useState(false);const[allCollapsed,setAllCollapsed]=useState(false);const[showExp,setShowExp]=useState(false);
const[mamFilter,setMamFilter]=usePersist(`bp_${title}_mam`,"");const[mamOn,setMamOn]=useState(false);
const closeSb=()=>{if(!sb)return;setSbClosing(true);setTimeout(()=>{setSb(null);setSbClosing(false)},200)};
const toggleSb=b=>{if(sb&&sb.id===b.id)closeSb();else{setSbClosing(false);setSb(b)}};
const isGrouped=grp!=="all";
const perPage=isGrouped?5000:60;
const sortParam=grp==="author"?"author":grp==="series"?"series":sort;
const load=useCallback((page=1)=>{setLd(true);const p=new URLSearchParams({search:q,sort:sortParam,per_page:perPage,page,...extraParams});if(mamFilter)p.set("mam_status",mamFilter);api.get(`${apiPath}?${p}`).then(d=>{setBks(d.books);setTotal(d.total);setPg(page);setLd(false)}).catch(()=>setLd(false))},[q,sortParam,apiPath,grp,mamFilter]);
useEffect(()=>{load(1)},[load]);
useEffect(()=>{api.get("/mam/status").then(r=>setMamOn(!!r.enabled)).catch(()=>{})},[]);
const totalPages=Math.max(1,Math.ceil(total/perPage));
const onAction=async(act,id)=>{if(act==="hide")await api.post(`/books/${id}/hide`);if(act==="dismiss")await api.post(`/books/${id}/dismiss`);if(act==="delete")await api.del(`/books/${id}`);load(pg)};
const dismissable=bks.filter(b=>!!b.is_new).length;

// Group books
let content;
if(grp==="author"&&bks.length>0){const groups={};bks.forEach(b=>{const k=b.author_name||"Unknown";if(!groups[k])groups[k]=[];groups[k].push(b)});content=Object.entries(groups).sort(([a],[b])=>a.localeCompare(b)).map(([name,books])=><Section key={name} title={name} count={books.length} defaultOpen={!allCollapsed}>{vm==="list"?<BList books={books} onAction={onAction} onBookClick={toggleSb} showAuthor={false}/>:<BGrid books={books} onAction={onAction} onBookClick={toggleSb}/>}</Section>)}
else if(grp==="series"&&bks.length>0){const groups={};bks.forEach(b=>{const k=b.series_name||"Standalone";if(!groups[k])groups[k]=[];groups[k].push(b)});content=Object.entries(groups).sort(([a],[b])=>a==="Standalone"?1:b==="Standalone"?-1:a.localeCompare(b)).map(([name,books])=><Section key={name} title={name} count={books.length} defaultOpen={!allCollapsed}>{vm==="list"?<BList books={books} onAction={onAction} onBookClick={toggleSb} showAuthor={showAuthor}/>:<BGrid books={books} onAction={onAction} onBookClick={toggleSb}/>}</Section>)}
else{content=vm==="list"?<BList books={bks} onAction={onAction} onBookClick={toggleSb} showAuthor={showAuthor}/>:<BGrid books={bks} onAction={onAction} onBookClick={toggleSb}/>}

return<div style={{display:"flex",flexDirection:"column",gap:16}}>
{/* Sticky sub-header */}
<div className="bp-sticky" style={{position:"sticky",top:56,zIndex:40,background:t.bg+"ee",backdropFilter:"blur(8px)",padding:"12px 0",marginTop:-12}}>
<div className="bp-controls" style={{display:"flex",alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",gap:8}}>
<div><h1 style={{fontSize:22,fontWeight:700,color:t.text,margin:0}}>{title}</h1><p style={{fontSize:12,color:t.tf,margin:0}}>{total} {subtitle}</p></div>
<div className="bp-right" style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
<SearchBar value={q} onChange={v=>{setQ(v);setPg(1)}}/>
{!isGrouped&&<select value={sort} onChange={e=>{setSort(e.target.value);setPg(1)}} style={{padding:"7px 10px",borderRadius:6,border:`1px solid ${t.border}`,background:t.inp,color:t.text2,fontSize:12}}><option value="title">Sort: Title</option><option value="author">Sort: Author</option><option value="date">Sort: Date</option><option value="added">Sort: Added</option></select>}
{mamOn?<select value={mamFilter} onChange={e=>{setMamFilter(e.target.value);setPg(1)}} style={{padding:"7px 10px",borderRadius:6,border:`1px solid ${t.border}`,background:mamFilter?t.accent+"22":t.inp,color:mamFilter?t.accent:t.text2,fontSize:12}}><option value="">MAM: All</option><option value="found">MAM: Found</option><option value="possible">MAM: Possible</option><option value="not_found">MAM: Not Found</option><option value="unscanned">MAM: Unscanned</option></select>:null}
<select value={grp} onChange={e=>{setGrp(e.target.value);setPg(1)}} style={{padding:"7px 10px",borderRadius:6,border:`1px solid ${t.border}`,background:t.inp,color:t.text2,fontSize:12}}><option value="all">All</option><option value="author">Group: Author</option><option value="series">Group: Series</option></select>
{isGrouped&&<Btn size="sm" variant="ghost" onClick={()=>setAllCollapsed(!allCollapsed)}>{allCollapsed?Ic.expand:Ic.collapse} {allCollapsed?"Expand":"Collapse"} All</Btn>}
<VT mode={vm} setMode={setVm}/>
{dismissable>0?<Btn size="sm" variant="ghost" onClick={async()=>{await api.post("/books/dismiss-all");load(pg)}}>Dismiss all ({dismissable})</Btn>:null}
{exportFilter?<Btn size="sm" variant="ghost" onClick={()=>setShowExp(true)}>{Ic.book} Export</Btn>:null}
</div></div></div>
{ld?<Load/>:<>{content}{!isGrouped&&totalPages>1&&<div style={{display:"flex",justifyContent:"center",gap:8,padding:20,alignItems:"center"}}><Btn size="sm" disabled={pg<=1} onClick={()=>{load(pg-1);window.scrollTo(0,0)}}>← Prev</Btn><span style={{fontSize:13,color:t.td}}>Page {pg} of {totalPages}</span><Btn size="sm" disabled={pg>=totalPages} onClick={()=>{load(pg+1);window.scrollTo(0,0)}}>Next →</Btn></div>}</>}
{sb&&<BookSidebar book={sb} closing={sbClosing} onClose={closeSb} onAction={onAction} onEdit={()=>load(pg)}/>}
{showExp?<ExportModal onClose={()=>setShowExp(false)} defaultFilter={exportFilter}/>:null}
</div>}
