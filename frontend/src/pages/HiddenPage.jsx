import { useState, useEffect } from "react";
import { useTheme } from "../theme";
import { api } from "../api";
import { Btn } from "../components/Btn";
import { Load } from "../components/Load";
import { BList } from "../components/BookViews";
import { BookSidebar } from "../components/BookSidebar";

export default function HiddenPage({onNav}){const t=useTheme();const[bks,setBks]=useState([]);const[ld,setLd]=useState(true);const[sb,setSb]=useState(null);const[sbClosing,setSbClosing]=useState(false);
const load=()=>{setLd(true);api.get("/books/hidden").then(d=>{setBks(d.books);setLd(false)}).catch(console.error)};
useEffect(()=>{load()},[]);
const closeSb=()=>{if(!sb)return;setSbClosing(true);setTimeout(()=>{setSb(null);setSbClosing(false)},200)};
const toggleSb=b=>{if(sb&&sb.id===b.id)closeSb();else{setSbClosing(false);setSb(b)}};
const onAction=async(act,id)=>{if(act==="unhide")await api.post(`/books/${id}/unhide`);load()};
return<div style={{display:"flex",flexDirection:"column",gap:20}}>
<div style={{display:"flex",alignItems:"center",gap:12}}><Btn variant="ghost" onClick={()=>onNav("dashboard")}>← Dashboard</Btn><h1 style={{fontSize:22,fontWeight:700,color:t.text,margin:0}}>Hidden Books</h1><span style={{fontSize:13,color:t.tg}}>({bks.length})</span></div>
{ld?<Load/>:bks.length===0?<div style={{textAlign:"center",padding:60,color:t.tg}}>No hidden books</div>:<BList books={bks} showAuthor onAction={onAction} onBookClick={toggleSb}/>}
{sb?<BookSidebar book={sb} closing={sbClosing} onClose={closeSb} onAction={onAction} onEdit={load}/>:null}
</div>}
