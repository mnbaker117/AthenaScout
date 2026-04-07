import { useState, useEffect } from "react";
import { useTheme } from "../theme";
import { api } from "../api";
import { Btn } from "../components/Btn";
import { Load } from "../components/Load";
import { BList } from "../components/BookViews";

export default function HiddenPage({onNav}){const t=useTheme();const[bks,setBks]=useState([]);const[ld,setLd]=useState(true);const load=()=>{setLd(true);api.get("/books/hidden").then(d=>{setBks(d.books);setLd(false)}).catch(console.error)};useEffect(()=>{load()},[]);const unhide=async id=>{await api.post(`/books/${id}/unhide`);load()};
return<div style={{display:"flex",flexDirection:"column",gap:20}}>
<div style={{display:"flex",alignItems:"center",gap:12}}><Btn variant="ghost" onClick={()=>onNav("dashboard")}>← Dashboard</Btn><h1 style={{fontSize:22,fontWeight:700,color:t.text,margin:0}}>Hidden Books</h1><span style={{fontSize:13,color:t.tg}}>({bks.length})</span></div>
{ld?<Load/>:bks.length===0?<div style={{textAlign:"center",padding:60,color:t.tg}}>No hidden books</div>:<BList books={bks} showAuthor onAction={(a,id)=>a==="unhide"&&unhide(id)}/>}
</div>}
