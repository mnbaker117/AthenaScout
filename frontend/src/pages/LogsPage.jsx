import { useState, useEffect, useRef } from "react";
import { useTheme } from "../theme";
import { api } from "../api";
import { Btn } from "../components/Btn";
import { Spin } from "../components/Spin";

export default function LogsPage(){
  const t=useTheme();
  const[lines,setLines]=useState([]);
  const[loading,setLoading]=useState(true);
  const[auto,setAuto]=useState(true);
  const[filter,setFilter]=useState("");
  const bottomRef=useRef(null);

  const load=async()=>{
    try{
      const r=await api.get("/logs?lines=1000");
      setLines(r.lines||[]);
    }catch{}
    setLoading(false);
  };

  useEffect(()=>{load();const iv=setInterval(()=>{if(!document.hidden)load()},5000);const onVis=()=>{if(!document.hidden)load()};document.addEventListener("visibilitychange",onVis);return()=>{clearInterval(iv);document.removeEventListener("visibilitychange",onVis)}},[]);

  useEffect(()=>{if(auto&&bottomRef.current)bottomRef.current.scrollIntoView({behavior:"smooth"})},[lines,auto]);

  const filtered=filter?lines.filter(l=>l.toLowerCase().includes(filter.toLowerCase())):lines;

  const levelColor=(line)=>{
    if(line.includes(" ERROR:"))return t.redt;
    if(line.includes(" WARNING:"))return t.ylwt;
    if(line.includes(" DEBUG:"))return t.tg;
    return t.text2;
  };

  return<div style={{display:"flex",flexDirection:"column",gap:16}}>
    <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",gap:12}}>
      <h1 style={{fontSize:22,fontWeight:700,color:t.text,margin:0}}>Application Logs</h1>
      <div style={{display:"flex",alignItems:"center",gap:8}}>
        <input value={filter} onChange={e=>setFilter(e.target.value)} placeholder="Filter logs..." style={{padding:"6px 10px",background:t.inp,border:`1px solid ${t.border}`,borderRadius:6,color:t.text2,fontSize:12,width:200}}/>
        <Btn size="sm" variant={auto?"accent":"ghost"} onClick={()=>setAuto(!auto)}>{auto?"Auto-scroll ON":"Auto-scroll OFF"}</Btn>
        <Btn size="sm" onClick={load}>{loading?<Spin/>:"Refresh"}</Btn>
      </div>
    </div>
    <div style={{background:t.bg2,border:`1px solid ${t.border}`,borderRadius:10,padding:0,maxHeight:"calc(100vh - 200px)",overflowY:"auto",overflowX:"auto"}}>
      <pre style={{margin:0,padding:16,fontSize:11,lineHeight:1.6,fontFamily:"'JetBrains Mono','Fira Code','SF Mono',Consolas,monospace",whiteSpace:"pre-wrap",wordBreak:"break-all"}}>
        {filtered.length===0&&!loading?<span style={{color:t.tg,fontStyle:"italic"}}>No log entries{filter?" matching filter":""}</span>:
        filtered.map((line,i)=><span key={i} style={{color:levelColor(line)}}>{line}{"\n"}</span>)}
        <span ref={bottomRef}/>
      </pre>
    </div>
    <div style={{fontSize:11,color:t.tg}}>{filtered.length} of {lines.length} lines · Polls every 5s · Last 1000 entries</div>
  </div>
}
