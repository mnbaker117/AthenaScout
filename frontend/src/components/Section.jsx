import { useState, useEffect } from "react";
import { useTheme } from "../theme";
import { PB } from "./PB";

export function Section({title,count,children,defaultOpen=true,ownedCount,totalCount}){const t=useTheme();const[open,setOpen]=useState(defaultOpen);
useEffect(()=>{setOpen(defaultOpen)},[defaultOpen]);
return<div style={{marginBottom:12}}><div onClick={()=>setOpen(!open)} style={{display:"flex",alignItems:"center",gap:10,cursor:"pointer",padding:"8px 0"}}><span style={{color:t.tg,transform:open?"rotate(0)":"rotate(-90deg)",transition:"transform 0.2s",fontSize:12}}>▼</span><span style={{fontSize:14,fontWeight:600,color:t.tm,textTransform:"uppercase"}}>{title}</span><span style={{fontSize:11,color:t.tg}}>{count}</span>{totalCount!=null?<><span style={{fontSize:11,color:t.grnt}}>{ownedCount||0}/{totalCount}</span><div style={{width:60}}><PB owned={ownedCount||0} total={totalCount}/></div></>:null}</div>{open?children:null}</div>}
