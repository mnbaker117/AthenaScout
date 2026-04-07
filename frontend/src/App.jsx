import { useState, useEffect, useCallback, useRef } from "react";
import { THEMES, TC, useTheme } from "./theme";
import { api } from "./api";
import { Ic } from "./icons";
import { usePersist } from "./hooks/usePersist";
import { NAV } from "./lib/constants";
import { Btn } from "./components/Btn";
import { Spin } from "./components/Spin";
import { Load } from "./components/Load";
import { PB } from "./components/PB";
import { VT } from "./components/VT";
import { SearchBar } from "./components/SearchBar";
import { Section } from "./components/Section";
import { BGrid, BList } from "./components/BookViews";
import { BookSidebar } from "./components/BookSidebar";
import { AddBookModal } from "./components/AddBookModal";
import { UrlSearchModal } from "./components/UrlSearchModal";
import { ExportModal } from "./components/ExportModal";
import { SetupWizard } from "./components/SetupWizard";
import Dashboard from "./pages/Dashboard";
import ImportExportPage from "./pages/ImportExportPage";
import HiddenPage from "./pages/HiddenPage";
import AuthorsPage from "./pages/AuthorsPage";

// ─── Inline Series (for Author Detail) ─────────────────────
function IS({series,vm,onAction,onBookClick,collapsed,authorId}){const t=useTheme();const[ld,setLd]=useState(false);const[bks,setBks]=useState(null);const load=()=>{if(bks)return;setLd(true);api.get(`/series/${series.id}`).then(d=>{setBks(d.books||[]);setLd(false)}).catch(()=>setLd(false))};useEffect(()=>{load()},[]);
const isMulti=!!series.multi_author;
const header=isMulti?<span>{series.name} <span style={{fontSize:11,color:useTheme().cyant,fontWeight:600,textTransform:"none",background:useTheme().cyan+"22",padding:"2px 8px",borderRadius:4,marginLeft:4}}>shared series</span></span>:series.name;
const countStr=isMulti?`${series.owned_count||0}/${series.author_book_count||0} · ${series.book_count||0} total`:`${series.owned_count||0}/${series.book_count||0}`;
return<Section title={header} count={countStr} ownedCount={series.owned_count} totalCount={isMulti?series.author_book_count:series.book_count} defaultOpen={!collapsed}>{ld?<Load/>:bks?(vm==="list"?<BList books={bks} onAction={onAction} onBookClick={onBookClick} showAuthor={isMulti} highlightAuthorId={authorId}/>:<BGrid books={bks} onAction={onAction} onBookClick={onBookClick} showAuthor={isMulti} highlightAuthorId={authorId}/>):null}</Section>}

// ─── Standalone Section ─────────────────────────────────────
function SA({books,vm,onAction,onBookClick,collapsed}){return<Section title="Standalone" count={books.length} defaultOpen={!collapsed}>{vm==="list"?<BList books={books} onAction={onAction} onBookClick={onBookClick}/>:<BGrid books={books} onAction={onAction} onBookClick={onBookClick}/>}</Section>}

// ─── Books Page (Library/Missing/Upcoming) ──────────────────
function BP({title,subtitle,apiPath="/books",extraParams={},showAuthor=true,exportFilter}){const t=useTheme();const[bks,setBks]=useState([]);const[total,setTotal]=useState(0);const[pg,setPg]=useState(1);const[ld,setLd]=useState(true);const[q,setQ]=usePersist(`bp_${title}_q`,"");const[vm,setVm]=usePersist(`bp_${title}_vm`,"grid");const[grp,setGrp]=usePersist(`bp_${title}_grp`,"all");const[sort,setSort]=usePersist(`bp_${title}_sort`,"title");const[sb,setSb]=useState(null);const[sbClosing,setSbClosing]=useState(false);const[allCollapsed,setAllCollapsed]=useState(false);const[showExp,setShowExp]=useState(false);
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

// ─── Author Detail ──────────────────────────────────────────
function ADP({authorId,onNav}){const t=useTheme();const[a,setA]=useState(null);const[ld,setLd]=useState(true);const[ref,setRef]=useState(false);const[vm,setVm]=usePersist("adp_vm","grid");const[rk,setRk]=useState(0);const[sb,setSb]=useState(null);const[sbClosing,setSbClosing]=useState(false);const[allCol,setAllCol]=useState(false);
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

// ─── Language Multi-Select Dropdown ─────────────────────────
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

// ─── Database Browser ───────────────────────────────────────
function DBP(){const t=useTheme();
const[tables,setTables]=useState([]);
const[tab,setTab]=usePersist("db_tab","books");
const[schema,setSchema]=useState(null);
const[rows,setRows]=useState([]);
const[total,setTotal]=useState(0);
const[pg,setPg]=useState(1);
const[ld,setLd]=useState(true);
const[q,setQ]=useState("");
const[sort,setSort]=usePersist("db_sort","id");
const[sortDir,setSortDir]=usePersist("db_dir","asc");
const perPage=50;
const[showNames,setShowNames]=useState(false);
const[fkLookup,setFkLookup]=useState({authors:{},series:{}});
const[fkLoading,setFkLoading]=useState(false);
// Editing state
const[edits,setEdits]=useState({});
const[editCell,setEditCell]=useState(null);// {rowId, col}
const[editVal,setEditVal]=useState("");
const[saveMsg,setSaveMsg]=useState(null);
const[showAdd,setShowAdd]=useState(false);
const[newRow,setNewRow]=useState({});
const[deleting,setDeleting]=useState(null);
const editCount=Object.keys(edits).length;

// Load table list on mount
useEffect(()=>{api.get("/db/tables").then(r=>setTables(r.tables||[])).catch(()=>{})},[]);

// Load schema when tab changes
useEffect(()=>{setSchema(null);api.get(`/db/table/${tab}/schema`).then(setSchema).catch(()=>{})},[tab]);
// Warn before leaving with unsaved edits
useEffect(()=>{if(editCount===0)return;const handler=e=>{e.preventDefault();e.returnValue=""};window.addEventListener("beforeunload",handler);return()=>window.removeEventListener("beforeunload",handler)},[editCount]);
// Paginated FK lookup: fetches all rows from a table in 500-row batches
useEffect(()=>{if(!showNames||tab!=="books")return;
let cancelled=false;
const fetchAll=async(table,keyCol,valCol)=>{const map={};let page=1;while(true){const r=await api.get(`/db/table/${table}?per_page=500&sort=id&page=${page}`);const rws=r.rows||[];rws.forEach(row=>{map[row[keyCol]]=row[valCol]});if(rws.length<500||cancelled)break;page++}return map};
setFkLoading(true);
Promise.all([fetchAll("authors","id","name"),fetchAll("series","id","name")]).then(([am,sm])=>{if(!cancelled)setFkLookup({authors:am,series:sm})}).catch(()=>{}).finally(()=>{if(!cancelled)setFkLoading(false)});
return()=>{cancelled=true};
},[showNames,tab]);

// Load rows when tab/page/sort/search changes
const load=useCallback(()=>{setLd(true);const p=new URLSearchParams({page:String(pg),per_page:String(perPage),sort,sort_dir:sortDir});if(q)p.set("search",q);api.get(`/db/table/${tab}?${p}`).then(d=>{setRows(d.rows||[]);setTotal(d.total||0);setLd(false)}).catch(()=>setLd(false))},[tab,pg,sort,sortDir,q]);
useEffect(()=>{load()},[load]);

const totalPages=Math.max(1,Math.ceil(total/perPage));
const switchTab=tb=>{if(editCount>0&&!confirm("You have unsaved changes. Switch tables and discard them?"))return;setTab(tb);setPg(1);setQ("");setSort("id");setSortDir("asc");setEdits({});setSaveMsg(null);setShowAdd(false);setNewRow({})};
const toggleSort=col=>{if(sort===col){setSortDir(d=>d==="asc"?"desc":"asc")}else{setSort(col);setSortDir("asc")}setPg(1)};

// Edit helpers
const startEdit=(rowId,col,currentVal)=>{if(col.pk)return;setEditCell({rowId,col:col.name});setEditVal(currentVal===null||currentVal===undefined?"":String(currentVal))};
const confirmEdit=(rowId,colName,originalVal)=>{const key=`${rowId}:${colName}`;const newVal=editVal;const origStr=originalVal===null||originalVal===undefined?"":String(originalVal);if(newVal===origStr){setEdits(e=>{const n={...e};delete n[key];return n})}else{setEdits(e=>({...e,[key]:newVal}))}setEditCell(null)};
const cancelEdit=()=>setEditCell(null);
const discardAll=()=>{setEdits({});setSaveMsg(null)};
const getEditedVal=(rowId,colName,originalVal)=>{const key=`${rowId}:${colName}`;return key in edits?edits[key]:originalVal};
const isEdited=(rowId,colName)=>`${rowId}:${colName}` in edits;

const saveAll=async()=>{if(editCount===0)return;setSaveMsg(null);
// Group edits by row ID
const grouped={};for(const[key,val]of Object.entries(edits)){const[rid,col]=key.split(":");if(!grouped[rid])grouped[rid]={};grouped[rid][col]=val===""?null:val}
try{const r=await api.post(`/db/table/${tab}/update`,{edits:grouped});if(r.status==="error"){setSaveMsg({type:"error",errors:r.errors||[]})}else{setSaveMsg({type:"success",count:r.updated||0});setEdits({});load();setTimeout(()=>setSaveMsg(null),3000)}}catch(e){setSaveMsg({type:"error",errors:[{error:String(e)}]})}};

const addRow=async()=>{try{const r=await api.post(`/db/table/${tab}/add`,{values:newRow});if(r.status==="ok"){setShowAdd(false);setNewRow({});load();setSaveMsg({type:"success",count:0,msg:`Row ${r.id} added`});setTimeout(()=>setSaveMsg(null),3000)}else{setSaveMsg({type:"error",errors:[{error:r.error||"Failed to add row"}]})}}catch(e){setSaveMsg({type:"error",errors:[{error:String(e)}]})}};

const deleteRow=async(rowId)=>{try{await api.del(`/db/table/${tab}/row/${rowId}`);setDeleting(null);load();setSaveMsg({type:"success",count:0,msg:`Row ${rowId} deleted`});setTimeout(()=>setSaveMsg(null),3000)}catch(e){setSaveMsg({type:"error",errors:[{error:String(e)}]})}};

// Format cell value for display
const fmtCell=(val,colName,colType)=>{
if(val===null||val===undefined)return<span style={{color:t.tg,fontStyle:"italic",opacity:0.5}}>null</span>;
// FK resolution: show name alongside ID when toggle is on
if(showNames&&tab==="books"&&val!==null){
if(colName==="author_id"&&fkLookup.authors[val])return<span><span style={{color:t.tg,fontSize:11}}>{val}</span> <span style={{color:t.purt,fontWeight:500}}>{fkLookup.authors[val]}</span></span>;
if(colName==="series_id"&&fkLookup.series[val])return<span><span style={{color:t.tg,fontSize:11}}>{val}</span> <span style={{color:t.cyant,fontWeight:500}}>{fkLookup.series[val]}</span></span>;
}
// Timestamp formatting for REAL columns ending in _at
if(colType==="REAL"&&(colName.endsWith("_at")||colName==="started_at"||colName==="finished_at")){
const ts=Number(val);if(ts>1e9&&ts<2e10){try{return new Date(ts*1000).toLocaleString()}catch{}}
}
// Truncate long text
const s=String(val);
if(s.length>120)return<span title={s}>{s.substring(0,120)}…</span>;
return s;
};

// Column type color
const typeColor=tp=>{const u=(tp||"").toUpperCase();if(u.includes("INTEGER"))return t.cyant;if(u.includes("REAL"))return t.ylwt;if(u.includes("TEXT"))return t.grnt;return t.tg};

return<div style={{display:"flex",flexDirection:"column",gap:16}}>

{/* Header */}
<div style={{display:"flex",alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",gap:12}}>
<div>
<h1 style={{fontSize:22,fontWeight:700,color:t.text,margin:0}}>Database Browser</h1>
<p style={{fontSize:13,color:t.td,marginTop:4}}>Viewing active library database{schema?` · ${schema.row_count.toLocaleString()} rows in ${tab}`:""}</p>
</div>
</div>

{/* Unsaved changes banner */}
{editCount>0?<div style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"10px 16px",background:t.ylw+"18",border:`1px solid ${t.ylw}44`,borderRadius:10,gap:12}}><span style={{fontSize:13,color:t.ylwt,fontWeight:500}}>{editCount} unsaved change{editCount!==1?"s":""}</span><div style={{display:"flex",gap:8}}><Btn size="sm" variant="accent" onClick={saveAll}>Save Changes</Btn><Btn size="sm" onClick={discardAll} style={{color:t.redt}}>Discard</Btn></div></div>:null}
{saveMsg?<div style={{padding:"8px 14px",borderRadius:8,fontSize:13,background:saveMsg.type==="success"?t.grn+"18":t.red+"18",border:`1px solid ${saveMsg.type==="success"?t.grn+"44":t.red+"44"}`,color:saveMsg.type==="success"?t.grnt:t.redt}}>{saveMsg.type==="success"?(saveMsg.msg||`${saveMsg.count} row${saveMsg.count!==1?"s":""} updated`):saveMsg.errors?.map((e,i)=><div key={i}>{e.row?`Row ${e.row}, ${e.column}: `:""}{e.error}</div>)}</div>:null}

{/* Tab bar */}
<div style={{display:"flex",gap:4,overflowX:"auto",paddingBottom:4}}>
{tables.map(tb=><button key={tb} onClick={()=>switchTab(tb)} style={{padding:"8px 16px",borderRadius:8,fontSize:13,fontWeight:500,cursor:"pointer",whiteSpace:"nowrap",background:tab===tb?t.accent+"22":t.bg2,color:tab===tb?t.accent:t.tf,border:`1px solid ${tab===tb?t.accent+"44":t.border}`}}>{tb.replace(/_/g," ")}</button>)}
</div>

{/* Search + info bar */}
<div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:12,flexWrap:"wrap"}}>
<input value={q} onChange={e=>{setQ(e.target.value);setPg(1)}} placeholder={`Search ${tab}...`} style={{padding:"8px 12px",borderRadius:6,border:`1px solid ${t.border}`,background:t.inp,color:t.text2,fontSize:13,width:260}}/>
<div style={{display:"flex",alignItems:"center",gap:12,fontSize:12,color:t.tg}}>
<Btn size="sm" onClick={()=>{setShowAdd(!showAdd);setNewRow({})}} style={{background:showAdd?t.accent+"22":"transparent",border:`1px solid ${showAdd?t.accent+"44":t.border}`}}>{showAdd?"Cancel":"+ Add Row"}</Btn>
{tab==="books"?<button onClick={()=>setShowNames(!showNames)} style={{padding:"4px 10px",borderRadius:6,border:`1px solid ${showNames?t.accent+"44":t.border}`,background:showNames?t.accent+"18":"transparent",color:showNames?t.accent:t.tf,cursor:"pointer",fontSize:11,fontWeight:500}}>{fkLoading?"Loading names...":showNames?"IDs → Names ON":"IDs → Names OFF"}</button>:null}
<span>{total.toLocaleString()} row{total!==1?"s":""}</span>
{schema?<span>{schema.columns.length} columns</span>:null}
</div>
</div>

{/* Add Row Form */}
{showAdd&&schema?<div style={{background:t.bg2,border:`1px solid ${t.accent}44`,borderRadius:10,padding:16,marginBottom:8}}>
<div style={{fontSize:12,fontWeight:600,color:t.accent,marginBottom:8}}>New Row</div>
<div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(200px,1fr))",gap:8}}>
{schema.columns.filter(c=>!c.pk).map(c=><div key={c.name}><div style={{fontSize:10,color:t.tg,marginBottom:2}}>{c.name} <span style={{color:typeColor(c.type),fontSize:9}}>{c.type}{c.notnull?" •req":""}</span></div><input value={newRow[c.name]||""} onChange={e=>setNewRow(r=>({...r,[c.name]:e.target.value}))} placeholder={c.notnull?"required":"optional"} style={{width:"100%",padding:"5px 8px",borderRadius:4,border:`1px solid ${t.border}`,background:t.inp,color:t.text2,fontSize:12}}/></div>)}
</div>
<div style={{display:"flex",gap:8,marginTop:10}}><Btn size="sm" variant="accent" onClick={addRow}>Insert Row</Btn><Btn size="sm" onClick={()=>{setShowAdd(false);setNewRow({})}}>Cancel</Btn></div>
</div>:null}

{/* Data table */}
{ld?<Load/>:rows.length===0?<div style={{padding:40,textAlign:"center",color:t.tg,fontSize:14}}>{q?"No rows match your search":"This table is empty"}</div>:
<div style={{position:"relative"}}><div className="db-table-scroll" style={{overflowX:"auto",border:`1px solid ${t.border}`,borderRadius:10,background:t.bg2}}>
<table style={{width:"100%",borderCollapse:"collapse",fontSize:12,minWidth:schema&&schema.columns.length>6?schema.columns.length*(showNames&&tab==="books"?180:150):undefined}}>
<thead>
<tr>
<th style={{padding:"10px 6px",borderBottom:`2px solid ${t.border}`,background:t.bg4,width:36,position:"sticky",top:0,zIndex:1}}/>
{(schema?.columns||[]).map((c,ci)=><th key={c.name} onClick={()=>toggleSort(c.name)} style={{padding:"10px 12px",paddingRight:ci===(schema?.columns||[]).length-1?24:12,textAlign:"left",borderBottom:`2px solid ${t.border}`,background:t.bg4,cursor:"pointer",whiteSpace:"nowrap",userSelect:"none",position:"sticky",top:0,zIndex:1}}>
<div style={{display:"flex",alignItems:"center",gap:4}}>
<span style={{fontWeight:600,color:sort===c.name?t.accent:t.text2}}>{c.name}</span>
{sort===c.name?<span style={{fontSize:10,color:t.accent}}>{sortDir==="asc"?"▲":"▼"}</span>:null}
<span style={{fontSize:9,color:typeColor(c.type),opacity:0.6,marginLeft:2}}>{(c.type||"TEXT").split(" ")[0]}</span>
{c.pk?<span style={{fontSize:8,fontWeight:700,color:t.ylwt,background:t.ylw+"22",padding:"1px 4px",borderRadius:3,marginLeft:2}}>PK</span>:null}
</div>
</th>)}
</tr>
</thead>
<tbody>
{rows.map((row,ri)=>{const rowId=row.id!=null?row.id:ri;return<tr key={rowId} style={{borderBottom:`1px solid ${t.borderL}`}}>
<td style={{padding:"4px 6px",textAlign:"center"}}>{deleting===rowId?<div style={{display:"flex",gap:2}}><button onClick={()=>deleteRow(rowId)} style={{background:t.red+"22",border:`1px solid ${t.red}44`,borderRadius:4,color:t.redt,cursor:"pointer",fontSize:10,padding:"2px 6px"}}>Yes</button><button onClick={()=>setDeleting(null)} style={{background:t.bg4,border:`1px solid ${t.border}`,borderRadius:4,color:t.tg,cursor:"pointer",fontSize:10,padding:"2px 6px"}}>No</button></div>:<button onClick={()=>setDeleting(rowId)} style={{background:"none",border:"none",cursor:"pointer",color:t.tg,fontSize:13,padding:"2px 4px",opacity:0.4}} title="Delete row">🗑</button>}</td>
{(schema?.columns||[]).map((c,ci)=>{const isEd=isEdited(rowId,c.name);const displayVal=getEditedVal(rowId,c.name,row[c.name]);const isEditing=editCell&&editCell.rowId===rowId&&editCell.col===c.name;
return<td key={c.name} onClick={()=>{if(!isEditing&&!c.pk)startEdit(rowId,c,displayVal)}} style={{padding:"8px 12px",paddingRight:ci===(schema?.columns||[]).length-1?24:12,maxWidth:300,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",color:t.text2,background:isEd?t.accent+"15":"transparent",cursor:c.pk?"default":"pointer",borderLeft:isEd?`2px solid ${t.accent}`:"2px solid transparent"}}>
{isEditing?<input autoFocus value={editVal} onChange={e=>setEditVal(e.target.value)} onKeyDown={e=>{if(e.key==="Enter"){confirmEdit(rowId,c.name,row[c.name])}else if(e.key==="Escape"){cancelEdit()}}} onBlur={()=>confirmEdit(rowId,c.name,row[c.name])} style={{width:"100%",padding:"2px 4px",border:`1px solid ${t.accent}`,borderRadius:3,background:t.inp,color:t.text2,fontSize:12,outline:"none"}}/>:fmtCell(displayVal,c.name,(c.type||"").toUpperCase())}
</td>})}
</tr>})}
</tbody>
</table>
</div>{schema&&schema.columns.length>6?<div style={{position:"absolute",right:0,top:0,bottom:0,width:32,background:`linear-gradient(to right, transparent, ${t.bg}cc)`,pointerEvents:"none",borderRadius:"0 10px 10px 0"}}/>:null}</div>}

{/* Pagination */}
{totalPages>1?<div style={{display:"flex",justifyContent:"center",alignItems:"center",gap:8,padding:8}}>
<Btn size="sm" disabled={pg<=1} onClick={()=>{setPg(pg-1);window.scrollTo(0,0)}}>← Prev</Btn>
<span style={{fontSize:13,color:t.td}}>Page {pg} of {totalPages}</span>
<Btn size="sm" disabled={pg>=totalPages} onClick={()=>{setPg(pg+1);window.scrollTo(0,0)}}>Next →</Btn>
</div>:null}

</div>}

// ─── Settings Helpers (outside SP to prevent re-mount on state change) ───
function SF({label,desc,children,warn}){const t=useTheme();return<div style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"14px 0",borderBottom:`1px solid ${t.borderL}`}}><div style={{flex:1}}><div style={{fontSize:14,fontWeight:500,color:t.text2}}>{label}</div>{desc?<div style={{fontSize:12,color:t.tf,marginTop:2}}>{desc}</div>:null}{warn?<div style={{fontSize:11,color:t.ylwt,marginTop:2}}>⚠ {warn}</div>:null}</div><div>{children}</div></div>}
function STog({on,onToggle,disabled}){const t=useTheme();return<div onClick={disabled?undefined:onToggle} style={{width:44,height:24,borderRadius:12,background:on?t.grn:t.bg4,cursor:disabled?"not-allowed":"pointer",padding:3,transition:"background 0.2s",opacity:disabled?0.5:1}}><div style={{width:18,height:18,borderRadius:"50%",background:"#fff",transform:on?"translateX(20px)":"translateX(0)",transition:"transform 0.2s"}}/></div>}

function SSection({title,defaultOpen=true,children}){const t=useTheme();const[open,setOpen]=useState(defaultOpen);return<div style={{background:t.bg2,border:`1px solid ${t.border}`,borderRadius:12,overflow:"hidden"}}><div onClick={()=>setOpen(!open)} style={{display:"flex",alignItems:"center",gap:8,padding:"14px 20px",cursor:"pointer",userSelect:"none"}}><span style={{transform:open?"rotate(0)":"rotate(-90deg)",transition:"transform 0.2s",fontSize:11,color:t.tg}}>▼</span><span style={{fontSize:13,fontWeight:600,color:t.text,textTransform:"uppercase",letterSpacing:"0.05em"}}>{title}</span></div>{open?<div style={{padding:"0 20px 16px"}}>{children}</div>:null}</div>}

// ─── Settings ───────────────────────────────────────────────
function SP(){const t=useTheme();const[s,setS]=useState(null);const[sv,setSv]=useState(false);const[msg,setMsg]=useState("");
const[mamVld,setMamVld]=useState(false);const[mamRes,setMamRes]=useState(null);const[fsStatus,setFsStatus]=useState(null);const[dragIdx,setDragIdx]=useState(null);const[testRun,setTestRun]=useState(false);const[testRes,setTestRes]=useState(null);const[newSrcPath,setNewSrcPath]=useState("");const[newSrcType,setNewSrcType]=useState("root");const[newSrcApp,setNewSrcApp]=useState("calibre");const[pathVld,setPathVld]=useState(false);const[pathRes,setPathRes]=useState(null);useEffect(()=>{api.get("/settings").then(setS).catch(console.error)},[]);
useEffect(()=>{if(!s?.mam_enabled)return;const poll=()=>api.get("/mam/full-scan/status").then(setFsStatus).catch(()=>{});poll();const iv=setInterval(poll,10000);return()=>clearInterval(iv)},[s?.mam_enabled]);
const save=async()=>{setSv(true);setMsg("");try{const toSave={...s};if(s._editingKey&&s._newKey){toSave.hardcover_api_key=s._newKey}delete toSave._editingKey;delete toSave._newKey;delete toSave._editingMam;delete toSave._newMam;delete toSave._scanClearQ;delete toSave._scanClearResults;delete toSave._scanClearSel;delete toSave.hardcover_api_key_set;delete toSave.language_options;delete toSave._discovered_libraries;delete toSave._extra_mount_paths;delete toSave._newSrcApp;await api.post("/settings",toSave);setMsg("Saved!");upd("_editingKey",false);upd("_newKey","");const fresh=await api.get("/settings");setS(fresh);setTimeout(()=>setMsg(""),2000)}catch(e){setMsg("Error")}setSv(false)};
const doValidate=async()=>{setMamVld(true);setMamRes(null);try{const r=await api.post("/mam/validate");setMamRes(r);if(r.success){const fresh=await api.get("/settings");setS(fresh)}}catch(e){setMamRes({success:false,message:"Network error"})}setMamVld(false)};
const startFullScan=async()=>{try{const r=await api.post("/mam/full-scan");if(r.error){setMsg(r.error);setTimeout(()=>setMsg(""),3000)}else{const st=await api.get("/mam/full-scan/status");setFsStatus(st)}}catch{}};
const cancelFullScan=async()=>{try{await api.post("/mam/full-scan/cancel");const st=await api.get("/mam/full-scan/status");setFsStatus(st)}catch{}};
const resetMam=async()=>{if(!confirm("Reset all MAM scan data? This clears mam_url and mam_status on every book. You will need to re-scan."))return;try{await api.post("/mam/reset");setFsStatus(null);setMsg("MAM data cleared!");setTimeout(()=>setMsg(""),2000)}catch{}};
const reorderFmt=(from,to)=>{if(from===to)return;const arr=[...(s.mam_format_priority||[])];const[item]=arr.splice(from,1);arr.splice(to,0,item);upd("mam_format_priority",arr)};
const doTestScan=async()=>{setTestRun(true);setTestRes(null);try{const r=await api.post("/mam/test-scan");setTestRes(r)}catch(e){setTestRes({error:"Network error"})}setTestRun(false)};
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
{msg&&<span style={{fontSize:13,color:msg==="Saved!"||msg==="Settings reset!"||msg==="MAM data cleared!"||msg==="Token saved!"||msg==="Key saved!"||msg==="Source data cleared!"||msg==="MAM data cleared!"||msg==="All data cleared!"?t.grnt:t.redt}}>{msg}</span>}
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
<SF label="Calibre sync interval (minutes)" desc="Set to 0 to disable auto-sync"><input {...numP("calibre_sync_interval_minutes",60)}/></SF>

</SSection>

{/* ── SOURCES ── */}
<SSection title="Sources">

<SF label="Hardcover API Key" desc="Get from hardcover.app → Account → API">{s.hardcover_api_key_set&&!s._editingKey?<div style={{display:"flex",alignItems:"center",gap:12}}><span style={{fontSize:14,color:t.tm,letterSpacing:"3px"}}>••••••••</span><Btn size="sm" onClick={()=>upd("_editingKey",true)}>Change</Btn></div>:<div style={{display:"flex",flexDirection:"column",gap:6}}><input value={s._editingKey?s._newKey||"":s.hardcover_api_key||""} onChange={e=>s._editingKey?upd("_newKey",e.target.value):upd("hardcover_api_key",e.target.value)} placeholder="Bearer eyJ..." style={{...ist,width:220}}/>{s._editingKey?<div style={{display:"flex",gap:6,marginTop:2}}><Btn size="sm" variant="accent" onClick={async()=>{const nk=s._newKey||"";if(!nk)return;setSv(true);try{await api.post("/settings",{hardcover_api_key:nk});upd("_editingKey",false);upd("_newKey","");const fresh=await api.get("/settings");setS(fresh);setMsg("Key saved!")}catch{setMsg("Error")}setSv(false)}}>Save Key</Btn><Btn size="sm" variant="ghost" onClick={()=>upd("_editingKey",false)}>Cancel</Btn></div>:null}</div>}</SF>

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"10px 0 6px"}}>Sources (in priority order)</div>
<SF label="1. Goodreads (Primary)" desc="Web scraping, most complete for series & dates"><span style={{fontSize:12,color:t.grnt,fontWeight:600}}>Active</span></SF>
<SF label="2. Hardcover" desc="GraphQL API, requires key above"><span style={{fontSize:12,color:s.hardcover_api_key_set?t.grnt:t.tg,fontWeight:600}}>{s.hardcover_api_key_set?"Active":"No key set"}</span></SF>
<SF label="3. Fantastic Fiction" desc="Web scraping for genre fiction" warn={s.fantasticfiction_enabled?"Currently blocked by Cloudflare — may not return results":undefined}><STog on={s.fantasticfiction_enabled} onToggle={()=>upd("fantasticfiction_enabled",!s.fantasticfiction_enabled)}/></SF>
<SF label="4. Kobo" desc="Web scraping for ebooks" warn={s.kobo_enabled?"Results may be incomplete or mixed for some authors":undefined}><STog on={s.kobo_enabled} onToggle={()=>upd("kobo_enabled",!s.kobo_enabled)}/></SF>

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"10px 0 6px"}}>Rate Limits (seconds between requests)</div>
<SF label="Goodreads"><input {...numP("rate_goodreads",2)}/></SF>
<SF label="Hardcover"><input {...numP("rate_hardcover",1)}/></SF>
<SF label="Fantastic Fiction"><input {...numP("rate_fantasticfiction",2)}/></SF>
<SF label="Kobo"><input {...numP("rate_kobo",3)}/></SF>

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

<SF label="Enable MAM features" desc={s.mam_enabled?"MAM integration active across the app":"Validate session first, then enable"}><STog on={!!s.mam_enabled} onToggle={async()=>{try{const r=await api.post("/mam/toggle");upd("mam_enabled",r.enabled)}catch{}}} disabled={!s.mam_session_id}/></SF>

{s.mam_enabled?<>

<SF label="Request delay (seconds)" desc="Pause between MAM API calls during scans. Minimum 1 second."><input {...numP("rate_mam",2,1)}/></SF>

<SF label="Format priority" desc="Drag to reorder. Top format is preferred when multiple are available on MAM."><div style={{display:"flex",flexDirection:"column",gap:4,minWidth:120}}>{(s.mam_format_priority||[]).map((fmt,i)=><div key={fmt} draggable onDragStart={()=>setDragIdx(i)} onDragOver={e=>{e.preventDefault()}} onDrop={()=>{reorderFmt(dragIdx,i);setDragIdx(null)}} onDragEnd={()=>setDragIdx(null)} style={{display:"flex",alignItems:"center",gap:8,padding:"5px 10px",borderRadius:6,background:dragIdx===i?t.accent+"22":t.bg4,border:`1px solid ${dragIdx===i?t.accent:t.border}`,cursor:"grab",fontSize:13,color:t.text2,transition:"background 0.15s"}}><span style={{color:t.tg,fontSize:11,fontWeight:600,width:16}}>{i+1}</span><span style={{fontWeight:500,textTransform:"uppercase",letterSpacing:"0.05em"}}>{fmt}</span><span style={{marginLeft:"auto",color:t.tg,fontSize:10}}>⋮⋮</span></div>)}</div></SF>

<div style={{padding:"10px 0",fontSize:12,color:t.tg,fontStyle:"italic",borderBottom:`1px solid ${t.borderL}`}}>Note: MAM session tokens expire periodically. If scans start failing, generate a new token and re-validate.</div>

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"10px 0 6px"}}>Scan Settings</div>
<SF label="MAM scan interval (minutes)" desc="How often automatic MAM scans run. Default 360 (6 hours). Set to 0 to disable."><input {...numP("mam_scan_interval_minutes",360)}/></SF>
<div style={{padding:"8px 0",fontSize:12,color:t.tg,fontStyle:"italic",borderBottom:`1px solid ${t.borderL}`}}>Scheduled scans check 100 books per cycle. Use the MAM Scan button on the Dashboard to scan all remaining books.</div>
<SF label="Full scan batch delay (minutes)" desc="Wait time between batches during a full library scan"><input {...numP("mam_full_scan_batch_delay_minutes",60,10)}/></SF>

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"10px 0 6px"}}>Test Scan</div>
<div style={{padding:"8px 0 12px"}}>
<div style={{display:"flex",alignItems:"center",gap:10,marginBottom:8}}>
<Btn size="sm" variant="accent" onClick={doTestScan} disabled={testRun}>{testRun?<><Spin/> Scanning 10 books...</>:"Run test scan (10 books)"}</Btn>
</div>
{testRes?<div style={{background:t.bg4,borderRadius:8,padding:"10px 14px",fontSize:13}}>{testRes.error?<span style={{color:t.redt}}>{testRes.error}</span>:<div style={{display:"flex",gap:16,flexWrap:"wrap",color:t.text2}}><span>Scanned: <b>{testRes.scanned||0}</b></span><span style={{color:t.grnt}}>Found: <b>{testRes.found||0}</b></span><span style={{color:t.ylwt}}>Possible: <b>{testRes.possible||0}</b></span><span style={{color:t.redt}}>Not found: <b>{testRes.not_found||0}</b></span>{testRes.errors>0?<span style={{color:t.red}}>Errors: <b>{testRes.errors}</b></span>:null}</div>}</div>:null}
</div>

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"10px 0 6px"}}>Full Library Scan</div>
{fsStatus?.active?<div style={{padding:"12px 0"}}>
<div style={{display:"flex",justifyContent:"space-between",fontSize:12,color:t.td,marginBottom:6}}><span>Scanning... {fsStatus.scanned} of {fsStatus.total_books} books ({fsStatus.progress_pct}%)</span><span>Batch size: {fsStatus.batch_size}</span></div>
<div style={{height:8,borderRadius:4,background:t.bg4,overflow:"hidden"}}><div style={{width:`${fsStatus.progress_pct||0}%`,height:"100%",borderRadius:4,background:t.accent,transition:"width 0.5s"}}/></div>
<div style={{display:"flex",gap:8,marginTop:10}}><Btn size="sm" onClick={cancelFullScan} style={{background:t.red+"22",color:t.redt,border:`1px solid ${t.red}44`}}>Cancel scan</Btn></div>
</div>:null}
{fsStatus&&!fsStatus.active&&fsStatus.status?<div style={{padding:"8px 0",fontSize:12,color:fsStatus.status==="complete"?t.grnt:fsStatus.status==="cancelled"?t.ylwt:t.redt}}>Last scan: {fsStatus.status}{fsStatus.status==="complete"?` — ${fsStatus.scanned} books scanned`:""}</div>:null}
{!fsStatus?.active?<div style={{display:"flex",gap:8,padding:"8px 0"}}>
<Btn size="sm" variant="accent" onClick={startFullScan}>Start full scan</Btn>
<Btn size="sm" onClick={resetMam} style={{color:t.redt}}>Reset scan data</Btn>
</div>:null}

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"10px 0 6px"}}>Manage Scan Data</div>
<SF label="Clear scan data by author" desc="Search for authors, then clear their source or MAM scan data">
<div style={{display:"flex",flexDirection:"column",gap:8,minWidth:220}}>
<div style={{position:"relative"}}>
<input value={s._scanClearQ||""} onChange={e=>{upd("_scanClearQ",e.target.value);if(e.target.value.length>=2)api.get(`/authors?search=${e.target.value}`).then(r=>upd("_scanClearResults",r.authors||[])).catch(()=>{});else upd("_scanClearResults",[])}} placeholder="Search authors..." style={{width:"100%",padding:"6px 8px",background:t.inp,border:`1px solid ${t.border}`,borderRadius:6,color:t.text2,fontSize:13}}/>
{(s._scanClearResults||[]).length>0?<div style={{position:"absolute",top:"100%",left:0,right:0,maxHeight:160,overflowY:"auto",background:t.bg2,border:`1px solid ${t.border}`,borderRadius:"0 0 6px 6px",zIndex:10}}>
{(s._scanClearResults||[]).map(a=><div key={a.id} onClick={()=>{const cur=s._scanClearSel||[];if(!cur.find(x=>x.id===a.id))upd("_scanClearSel",[...cur,{id:a.id,name:a.name}]);upd("_scanClearQ","");upd("_scanClearResults",[])}} style={{padding:"6px 10px",cursor:"pointer",fontSize:12,color:t.text2,borderBottom:`1px solid ${t.borderL}`}}>{a.name} <span style={{color:t.tg}}>({a.total_books||0} books)</span></div>)}
</div>:null}
</div>
{(s._scanClearSel||[]).length>0?<div style={{display:"flex",flexWrap:"wrap",gap:4}}>
{(s._scanClearSel||[]).map(a=><span key={a.id} style={{display:"inline-flex",alignItems:"center",gap:4,padding:"2px 8px",borderRadius:4,fontSize:11,background:t.purb,color:t.purt,border:`1px solid ${t.pur}33`}}>{a.name}<button onClick={()=>upd("_scanClearSel",(s._scanClearSel||[]).filter(x=>x.id!==a.id))} style={{background:"none",border:"none",cursor:"pointer",color:t.purt,padding:0,fontSize:13}}>×</button></span>)}
<button onClick={()=>upd("_scanClearSel",[])} style={{background:"none",border:"none",cursor:"pointer",color:t.tg,fontSize:11,padding:"2px 4px"}}>clear all</button>
</div>:null}
{(s._scanClearSel||[]).length>0?<div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
<Btn size="sm" onClick={async()=>{if(!confirm(`Clear SOURCE scan data for ${(s._scanClearSel||[]).length} author(s)? This will DELETE all discovered books.`))return;setSv(true);try{await api.post("/authors/clear-scan-data",{author_ids:(s._scanClearSel||[]).map(a=>a.id),clear_source:true,clear_mam:false});upd("_scanClearSel",[]);setMsg("Source data cleared!")}catch{setMsg("Error")}setSv(false)}} style={{background:t.ylw+"22",color:t.ylwt,border:`1px solid ${t.ylw}44`}}>Clear Source</Btn>
<Btn size="sm" onClick={async()=>{if(!confirm(`Clear MAM scan data for ${(s._scanClearSel||[]).length} author(s)?`))return;setSv(true);try{await api.post("/authors/clear-scan-data",{author_ids:(s._scanClearSel||[]).map(a=>a.id),clear_source:false,clear_mam:true});upd("_scanClearSel",[]);setMsg("MAM data cleared!")}catch{setMsg("Error")}setSv(false)}} style={{background:t.cyan+"22",color:t.cyant,border:`1px solid ${t.cyan}44`}}>Clear MAM</Btn>
<Btn size="sm" onClick={async()=>{if(!confirm(`Clear ALL scan data for ${(s._scanClearSel||[]).length} author(s)? This will DELETE all discovered books AND reset MAM status.`))return;setSv(true);try{await api.post("/authors/clear-scan-data",{author_ids:(s._scanClearSel||[]).map(a=>a.id),clear_source:true,clear_mam:true});upd("_scanClearSel",[]);setMsg("All data cleared!")}catch{setMsg("Error")}setSv(false)}} style={{background:t.red+"22",color:t.redt,border:`1px solid ${t.red}44`}}>Clear Both</Btn>
</div>:null}
</div>
</SF>

</>:null}

</SSection>

{/* ── APP ── */}
<SSection title="App">

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"6px 0"}}>Scanning Controls</div>
<SF label="Author scanning" desc="Enable source scanning for authors (Goodreads, Hardcover, etc). Disabling cancels any running scan."><STog on={s.author_scanning_enabled!==false} onToggle={async()=>{try{const r=await api.post("/scanning/author/toggle");upd("author_scanning_enabled",r.enabled)}catch{}}}/></SF>
<SF label="MAM scanning" desc={s.mam_scanning_enabled!==false?"MAM scans active — disable to stop all MAM scanning":"MAM scanning disabled — MAM features (badges, pages) still work"}>{s.mam_enabled?<STog on={s.mam_scanning_enabled!==false} onToggle={async()=>{try{const r=await api.post("/scanning/mam/toggle");upd("mam_scanning_enabled",r.enabled)}catch{}}}/>:<span style={{fontSize:12,color:t.tg}}>MAM not enabled</span>}</SF>
<div style={{padding:"8px 0",fontSize:12,color:t.tg,fontStyle:"italic"}}>Disabling a scan type cancels any running scan and prevents future scans. MAM features (badges, pages) remain visible when MAM scanning is off. Set scan intervals to 0 to disable only scheduled scans.</div>

<div style={{fontSize:12,fontWeight:600,color:t.tm,textTransform:"uppercase",letterSpacing:"0.06em",padding:"10px 0 6px"}}>Logging</div>
<SF label="Verbose logging" desc="Show detailed debug output in Docker logs. Logs individual book decisions, page visit details, and merge operations."><STog on={!!s.verbose_logging} onToggle={()=>upd("verbose_logging",!s.verbose_logging)}/></SF>

<div style={{borderTop:`1px solid ${t.borderL}`,marginTop:12,paddingTop:12}}>
<Btn onClick={async()=>{if(!confirm("Reset ALL settings to defaults?\n\nThis will clear your API keys, MAM session, Calibre URLs, source toggles, and all other customizations.\n\nYou will need to re-enter any values — Docker environment variables are only used for initial setup and will not be restored.\n\nThis cannot be undone."))return;setSv(true);try{await api.post("/settings/reset");const fresh=await api.get("/settings");setS(fresh);setMamRes(null);setTestRes(null);setFsStatus(null);setMsg("Settings reset!")}catch{setMsg("Error")}setSv(false)}} style={{color:t.redt}}>Reset all settings</Btn>
</div>

</SSection>

</div>
</div>
</div>}

// ─── MAM Page ───────────────────────────────────────────────
function MP({onNav}){const t=useTheme();
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

// ─── App Shell ──────────────────────────────────────────────

export default function App(){
  const[pg,setPg]=usePersist("page","dashboard");
  const[pa,setPa]=usePersist("page_arg",null);
  const[tn,setTn]=useState(()=>{try{return localStorage.getItem("cl_theme")||"dark"}catch{return"dark"}});
  const[showAdd,setShowAdd]=useState(null);
const[firstRun,setFirstRun]=useState(null);
const[mamWarn,setMamWarn]=useState(false);
const[mamOn,setMamOn]=useState(false);
const[libs,setLibs]=useState([]);
const[activeLib,setActiveLib]=useState(()=>{try{return localStorage.getItem("cl_active_lib")||""}catch{return""}});
useEffect(()=>{api.get("/libraries").then(r=>{const ll=r.libraries||[];setLibs(ll);const act=ll.find(l=>l.active);if(act){setActiveLib(act.slug);try{localStorage.setItem("cl_active_lib",act.slug)}catch{}}}).catch(()=>{})},[]);
// Check if this is a first-run scenario (setup wizard needed)
useEffect(()=>{api.get("/platform").then(r=>setFirstRun(r.first_run===true)).catch(()=>setFirstRun(false))},[]);
useEffect(()=>{api.get("/mam/status").then(r=>{setMamOn(!!r.enabled);if(r.enabled&&r.validation_ok===false)setMamWarn(true);else setMamWarn(false)}).catch(()=>{})},[pg]); // null, "manual", "url", "choose"
  const theme=THEMES[tn]||THEMES.dark;
  const nav=(p,a=null)=>{setPg(p);setPa(a);window.scrollTo(0,0)};
  useEffect(()=>{try{localStorage.setItem("cl_theme",tn)}catch{}},[tn]);
  const nextT=()=>{const n=Object.keys(THEMES);setTn(n[(n.indexOf(tn)+1)%n.length])};
  const switchLib=async(slug)=>{if(slug===activeLib)return;try{await api.post("/libraries/active",{slug});setActiveLib(slug);try{localStorage.setItem("cl_active_lib",slug)}catch{}setPg("dashboard");setPa(null)}catch(e){console.error("Library switch failed:",e)}};

  // Setup wizard completion — refresh libraries and show dashboard
  const onWizardComplete=()=>{setFirstRun(false);api.get("/libraries").then(r=>{const ll=r.libraries||[];setLibs(ll);const act=ll.find(l=>l.active);if(act){setActiveLib(act.slug);try{localStorage.setItem("cl_active_lib",act.slug)}catch{}}}).catch(()=>{});setPg("dashboard")};

return<TC.Provider value={theme}>
<style>{`*{box-sizing:border-box;margin:0}html{height:100%;background:${theme.bg}}body{background:${theme.bg};color:${theme.text2};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;min-height:100%;min-height:100dvh;min-height:-webkit-fill-available}::selection{background:${theme.accent}44}::-webkit-scrollbar{width:8px}::-webkit-scrollbar-track{background:${theme.bg}}::-webkit-scrollbar-thumb{background:${theme.border};border-radius:4px}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}
@keyframes slideOut{from{transform:translateX(0)}to{transform:translateX(100%)}}
@keyframes fadeIn{from{opacity:0;transform:scale(0.97)}to{opacity:1;transform:scale(1)}}
@keyframes fadeOverlay{from{opacity:0}to{opacity:1}}
@keyframes pageIn{from{opacity:0}to{opacity:1}}
button{font-family:inherit;transition:transform 0.1s,opacity 0.15s}button:active{transform:scale(0.96)}
input,select{font-family:inherit}
.sidebar-panel{animation:slideIn 0.25s ease-out}
.sidebar-closing{animation:slideOut 0.2s ease-in forwards}
.page-content{animation:pageIn 0.2s ease-out}
.nav-items{position:relative}
.nav-items::after{content:'';position:absolute;right:0;top:0;bottom:0;width:24px;background:linear-gradient(to right,transparent,${theme.bg}ee);pointer-events:none;opacity:0.8}
@media(max-width:768px){
  .nav-bar{position:relative!important}
  .nav-items{gap:0!important;-webkit-overflow-scrolling:touch}
  .nav-items button{padding:6px 10px!important;font-size:12px!important;white-space:nowrap}
  .nav-items button span:first-child{display:none!important}
  .main-content{padding:12px 12px 60px!important}
  .bp-sticky,.bp-controls[style*="sticky"]{top:0!important}
  .bp-controls{gap:10px!important}
  .bp-right{flex-wrap:wrap!important;justify-content:flex-start!important;width:100%!important}
  .bp-right select,.bp-right button{min-height:40px!important;font-size:13px!important}
  .sidebar-panel,.sidebar-closing{width:100%!important;max-width:100vw!important;padding:20px!important}
  .sb-actions{gap:12px!important}
  .sb-actions button{min-height:44px!important;min-width:44px!important;font-size:15px!important}
  .modal-panel{width:95vw!important;max-width:95vw!important}
  .dash-stats{grid-template-columns:repeat(3,1fr)!important;gap:8px!important}
  .lib-switcher select{max-width:120px!important;font-size:11px!important}
  .author-header{flex-direction:column!important;gap:12px!important}
  .author-controls{width:100%!important;justify-content:flex-start!important;flex-wrap:wrap!important}
  .author-controls button{min-height:40px!important}
  .settings-grid{grid-template-columns:1fr!important}
}`}</style>
<div style={{minHeight:"100vh"}}>

{/* ── First-run loading state ── */}
{firstRun===null?<div style={{display:"flex",justifyContent:"center",alignItems:"center",minHeight:"100vh"}}><Spin/></div>:firstRun?<SetupWizard onComplete={onWizardComplete}/>:<>

{/* ── Sticky Nav ── */}
<nav className="nav-bar" style={{position:"sticky",top:0,zIndex:50,background:theme.bg+"ee",backdropFilter:"blur(12px)",borderBottom:`1px solid ${theme.borderL}`}}>
<div style={{maxWidth:1120,margin:"0 auto",padding:"0 20px",display:"flex",alignItems:"center",justifyContent:"space-between",height:56,gap:8}}>
<button onClick={()=>nav("dashboard")} style={{background:"none",border:"none",cursor:"pointer",flexShrink:0,display:"flex",alignItems:"center",gap:8,position:"relative",paddingBottom:4}}>
<svg viewBox="0 0 512 512" style={{width:28,height:28}}><defs><linearGradient id="ig" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style={{stopColor:"#f0c060"}}/><stop offset="100%" style={{stopColor:"#d4a040"}}/></linearGradient></defs><circle cx="256" cy="256" r="240" fill="#2a1f4e" stroke="#d4a040" strokeWidth="12"/><circle cx="220" cy="200" r="22" fill="none" stroke="url(#ig)" strokeWidth="6"/><circle cx="292" cy="200" r="22" fill="none" stroke="url(#ig)" strokeWidth="6"/><circle cx="220" cy="200" r="8" fill="url(#ig)"/><circle cx="292" cy="200" r="8" fill="url(#ig)"/><path d="M248 220 L256 235 L264 220" fill="none" stroke="url(#ig)" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round"/><path d="M195 155 L212 178 L180 173" fill="url(#ig)" opacity="0.8"/><path d="M317 155 L300 178 L332 173" fill="url(#ig)" opacity="0.8"/><path d="M140 320 L256 290 L372 320 L372 365 C372 365 314 348 256 358 C198 348 140 365 140 365 Z" fill="url(#ig)" opacity="0.85"/></svg>
<span style={{fontSize:18,fontWeight:700,color:theme.accent}}>AthenaScout</span>
{pg==="dashboard"?<div style={{position:"absolute",bottom:0,left:0,right:0,height:2,background:theme.accent,borderRadius:1}}/>:null}
</button>
<div className="nav-items" style={{display:"flex",alignItems:"center",gap:2,overflowX:"auto",flex:1,minWidth:0}}>
{NAV.filter(n=>n.id!=="mam"||mamOn).map(n=><button key={n.id} onClick={()=>nav(n.id)} style={{padding:"8px 14px",borderRadius:8,fontSize:14,fontWeight:500,border:"none",cursor:"pointer",display:"inline-flex",alignItems:"center",gap:6,height:36,whiteSpace:"nowrap",flexShrink:0,background:(pg===n.id||(n.id==="authors"&&pg==="author"))?theme.bg4:"transparent",color:(pg===n.id||(n.id==="authors"&&pg==="author"))?theme.accent:theme.tf}}>
<span style={{fontSize:15,lineHeight:1}}>{n.icon}</span>{n.label}
</button>)}
</div>
<div style={{display:"flex",alignItems:"center",gap:2,flexShrink:0}}>
<div style={{position:"relative"}}>
<button onClick={()=>setShowAdd(showAdd==="choose"?null:"choose")} style={{width:36,height:36,borderRadius:8,fontSize:14,border:"none",cursor:"pointer",background:showAdd==="choose"?theme.bg4:"transparent",color:theme.tf,display:"inline-flex",alignItems:"center",justifyContent:"center"}} title="Add book">{Ic.plus}</button>
{showAdd==="choose"&&<div style={{position:"absolute",top:"100%",right:0,marginTop:4,background:theme.bg2,border:`1px solid ${theme.border}`,borderRadius:8,overflow:"hidden",boxShadow:"0 4px 12px rgba(0,0,0,0.3)",zIndex:60,minWidth:180}}>
<button onClick={e=>{e.stopPropagation();setShowAdd("url")}} style={{display:"flex",alignItems:"center",gap:8,padding:"10px 14px",fontSize:13,color:theme.text2,background:"transparent",border:"none",cursor:"pointer",width:"100%",textAlign:"left"}}>{Ic.search} Add from URL</button>
<button onClick={e=>{e.stopPropagation();setShowAdd("manual")}} style={{display:"flex",alignItems:"center",gap:8,padding:"10px 14px",fontSize:13,color:theme.text2,background:"transparent",border:"none",cursor:"pointer",width:"100%",textAlign:"left",borderTop:`1px solid ${theme.borderL}`}}>{Ic.edit} Add Manually</button>
</div>}
</div>
<button onClick={nextT} style={{width:36,height:36,borderRadius:8,border:"none",cursor:"pointer",background:"transparent",color:theme.tf,display:"inline-flex",alignItems:"center",justifyContent:"center"}} title={`Theme: ${theme.name}`}>{tn==="dark"?Ic.moon:tn==="light"?Ic.sun:Ic.cloudsun}</button>
<button onClick={()=>nav("database")} style={{width:36,height:36,borderRadius:8,border:"none",cursor:"pointer",background:pg==="database"?theme.bg4:"transparent",color:pg==="database"?theme.accent:theme.tf,display:"inline-flex",alignItems:"center",justifyContent:"center"}} title="Database">{Ic.database}</button>
<button onClick={()=>nav("settings")} style={{width:36,height:36,borderRadius:8,border:"none",cursor:"pointer",background:pg==="settings"?theme.bg4:"transparent",color:pg==="settings"?theme.accent:theme.tf,display:"inline-flex",alignItems:"center",justifyContent:"center"}} title="Settings">{Ic.gear}</button>
</div></div></nav>

{/* MAM Validation Warning Banner */}
{mamWarn?<div style={{maxWidth:1120,margin:"12px auto 0",padding:"10px 16px",background:theme.ylw+"18",border:`1px solid ${theme.ylw}44`,borderRadius:10,display:"flex",alignItems:"center",justifyContent:"space-between",gap:12,fontSize:13}}><span style={{color:theme.ylwt}}>⚠ MAM session may have expired — scans are paused. <button onClick={()=>nav("settings")} style={{background:"none",border:"none",color:theme.accent,cursor:"pointer",fontWeight:600,fontSize:13,padding:0,textDecoration:"underline"}}>Go to Settings</button> to update your token and re-validate.</span><button onClick={()=>setMamWarn(false)} style={{background:"none",border:"none",color:theme.tg,cursor:"pointer",fontSize:16,padding:"0 4px",flexShrink:0}}>✕</button></div>:null}

{/* ── Main Content ── */}
<main className="main-content" style={{maxWidth:1120,margin:"0 auto",padding:"28px 20px"}}>
<div className="page-content" key={pg+(pa||"")+activeLib}>
{pg==="dashboard"&&<Dashboard onNav={nav} libs={libs} activeLib={activeLib} switchLib={switchLib}/>}
{pg==="library"&&<BP title="My Library" subtitle="books in your Calibre library" apiPath="/books" extraParams={{owned:true}} exportFilter="library"/>}
{pg==="authors"&&<AuthorsPage onNav={nav}/>}
{pg==="author"&&<ADP authorId={pa} onNav={nav}/>}
{pg==="missing"&&<BP title="Missing Books" subtitle="books to find" apiPath="/books" extraParams={{owned:false}} exportFilter="missing"/>}
{pg==="upcoming"&&<BP title="Upcoming Books" subtitle="unreleased books" apiPath="/upcoming" exportFilter="missing"/>}
{pg==="hidden"&&<HiddenPage onNav={nav}/>}
{pg==="importexport"&&<ImportExportPage/>}
{pg==="mam"&&<MP onNav={nav}/>}
{pg==="database"&&<DBP/>}
{pg==="settings"&&<SP/>}
</div></main>

{showAdd==="manual"&&<AddBookModal onClose={()=>setShowAdd(null)} onAdded={()=>{}}/>}
{showAdd==="url"&&<UrlSearchModal onClose={()=>setShowAdd(null)} onAdded={()=>{}}/>}
</>}
</div>
</TC.Provider>}
