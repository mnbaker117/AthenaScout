import { useState, useEffect, useCallback } from "react";
import { useTheme } from "../theme";
import { api } from "../api";
import { usePersist } from "../hooks/usePersist";
import { Btn } from "../components/Btn";
import { Load } from "../components/Load";

// ─── Database Browser ───────────────────────────────────────
export default function DatabasePage(){const t=useTheme();
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
