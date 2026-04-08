import { useState, useEffect } from "react";
import { THEMES, TC, useTheme } from "./theme";
import { api } from "./api";
import { Ic } from "./icons";
import { usePersist } from "./hooks/usePersist";
import { NAV } from "./lib/constants";
import { Btn } from "./components/Btn";
import { Spin } from "./components/Spin";
import { AddBookModal } from "./components/AddBookModal";
import { UrlSearchModal } from "./components/UrlSearchModal";
import { SetupWizard } from "./components/SetupWizard";
import LoginPage from "./pages/LoginPage";
import Dashboard from "./pages/Dashboard";
import ImportExportPage from "./pages/ImportExportPage";
import HiddenPage from "./pages/HiddenPage";
import AuthorsPage from "./pages/AuthorsPage";
import BooksPage from "./pages/BooksPage";
import AuthorDetailPage from "./pages/AuthorDetailPage";
import MAMPage from "./pages/MAMPage";
import DatabasePage from "./pages/DatabasePage";
import SettingsPage from "./pages/SettingsPage";
import SuggestionsPage from "./pages/SuggestionsPage";

// ─── App Shell ──────────────────────────────────────────────

export default function App(){
  const[pg,setPg]=usePersist("page","dashboard");
  const[pa,setPa]=usePersist("page_arg",null);
  const[tn,setTn]=useState(()=>{try{return localStorage.getItem("cl_theme")||"dark"}catch{return"dark"}});
  const[showAdd,setShowAdd]=useState(null);
// Phase 22B.3 Stage 2A — auth state. Three meaningful values:
//   {loading:true}                  → render loading spinner (initial check in flight)
//   {authenticated:false,firstRun:true|false} → render LoginPage (setup or sign-in)
//   {authenticated:true,...}        → render the rest of the app
const[authState,setAuthState]=useState({loading:true,authenticated:false,firstRun:false});
const checkAuth=async()=>{try{const r=await api.get("/auth/check");setAuthState({loading:false,authenticated:!!r.authenticated,firstRun:!!r.first_run})}catch{setAuthState({loading:false,authenticated:false,firstRun:false})}};
useEffect(()=>{checkAuth();const onAuthRequired=()=>setAuthState(s=>s.authenticated?{loading:false,authenticated:false,firstRun:false}:s);window.addEventListener("athenascout:auth-required",onAuthRequired);return()=>window.removeEventListener("athenascout:auth-required",onAuthRequired)},[]);
const onLoginSuccess=()=>{setAuthState({loading:false,authenticated:true,firstRun:false})};
const[firstRun,setFirstRun]=useState(null);
const[mamWarn,setMamWarn]=useState(false);
const[mamOn,setMamOn]=useState(false);
const[libs,setLibs]=useState([]);
const[activeLib,setActiveLib]=useState(()=>{try{return localStorage.getItem("cl_active_lib")||""}catch{return""}});
useEffect(()=>{if(!authState.authenticated)return;api.get("/libraries").then(r=>{const ll=r.libraries||[];setLibs(ll);const act=ll.find(l=>l.active);if(act){setActiveLib(act.slug);try{localStorage.setItem("cl_active_lib",act.slug)}catch{}}}).catch(()=>{})},[authState.authenticated]);
// Check if this is a first-run scenario (setup wizard needed). Skipped until authenticated.
useEffect(()=>{if(!authState.authenticated)return;api.get("/platform").then(r=>setFirstRun(r.first_run===true)).catch(()=>setFirstRun(false))},[authState.authenticated]);
// MAM status is refetched on login and on explicit "athenascout:mam-state-changed"
// events dispatched by SettingsPage when the user toggles MAM. It used to
// refetch on every `pg` change (page nav) as a lazy refresh trigger, which
// cost 1 API call per nav click. Event-driven is surgical and free.
useEffect(()=>{if(!authState.authenticated)return;const refresh=()=>api.get("/mam/status").then(r=>{setMamOn(!!r.enabled);if(r.enabled&&r.validation_ok===false)setMamWarn(true);else setMamWarn(false)}).catch(()=>{});refresh();window.addEventListener("athenascout:mam-state-changed",refresh);return()=>window.removeEventListener("athenascout:mam-state-changed",refresh)},[authState.authenticated]);
// Phase 3c: pending series-suggestion count drives whether the
// "Suggestions" nav item appears at all (hidden when 0 to keep the
// navbar clean) and the badge number shown next to it. Refetched on
// page changes via the explicit "athenascout:suggestions-changed"
// event that SuggestionsPage dispatches after Apply/Ignore/Delete,
// plus a one-shot fetch on initial auth.
const[sugCount,setSugCount]=useState(0);
useEffect(()=>{if(!authState.authenticated)return;const refresh=()=>api.get("/series-suggestions/count").then(r=>setSugCount(r.pending||0)).catch(()=>{});refresh();window.addEventListener("athenascout:suggestions-changed",refresh);return()=>window.removeEventListener("athenascout:suggestions-changed",refresh)},[authState.authenticated]);
  const theme=THEMES[tn]||THEMES.dark;
  const nav=(p,a=null)=>{setPg(p);setPa(a);window.scrollTo(0,0)};
  useEffect(()=>{try{localStorage.setItem("cl_theme",tn)}catch{}},[tn]);
  const nextT=()=>{const n=Object.keys(THEMES);setTn(n[(n.indexOf(tn)+1)%n.length])};
  const switchLib=async(slug)=>{if(slug===activeLib)return;try{await api.post("/libraries/active",{slug});setActiveLib(slug);try{localStorage.setItem("cl_active_lib",slug)}catch{}setPg("dashboard");setPa(null)}catch(e){console.error("Library switch failed:",e)}};

  // Setup wizard completion — refresh libraries and show dashboard
  const onWizardComplete=()=>{setFirstRun(false);api.get("/libraries").then(r=>{const ll=r.libraries||[];setLibs(ll);const act=ll.find(l=>l.active);if(act){setActiveLib(act.slug);try{localStorage.setItem("cl_active_lib",act.slug)}catch{}}}).catch(()=>{});setPg("dashboard")};

// Auth gate — render login or loading before the main shell.
if(authState.loading)return<TC.Provider value={theme}><div style={{display:"flex",justifyContent:"center",alignItems:"center",minHeight:"100vh",background:theme.bg}}><Spin/></div></TC.Provider>;
if(!authState.authenticated)return<TC.Provider value={theme}><LoginPage onLoginSuccess={onLoginSuccess} isFirstRun={authState.firstRun}/></TC.Provider>;

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
{NAV.filter(n=>(n.id!=="mam"||mamOn)&&(n.id!=="suggestions"||sugCount>0)).map(n=><button key={n.id} onClick={()=>nav(n.id)} style={{padding:"8px 14px",borderRadius:8,fontSize:14,fontWeight:500,border:"none",cursor:"pointer",display:"inline-flex",alignItems:"center",gap:6,height:36,whiteSpace:"nowrap",flexShrink:0,background:(pg===n.id||(n.id==="authors"&&pg==="author"))?theme.bg4:"transparent",color:(pg===n.id||(n.id==="authors"&&pg==="author"))?theme.accent:theme.tf}}>
<span style={{fontSize:15,lineHeight:1}}>{n.icon}</span>{n.label}
{n.id==="suggestions"&&sugCount>0?<span style={{display:"inline-flex",alignItems:"center",justifyContent:"center",minWidth:18,height:18,padding:"0 5px",borderRadius:9,fontSize:11,fontWeight:700,background:theme.accent,color:theme.bg,marginLeft:2}}>{sugCount}</span>:null}
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
<button onClick={()=>nav("importexport")} style={{width:36,height:36,borderRadius:8,border:"none",cursor:"pointer",background:pg==="importexport"?theme.bg4:"transparent",color:pg==="importexport"?theme.accent:theme.tf,display:"inline-flex",alignItems:"center",justifyContent:"center"}} title="Import / Export">{Ic.arrows}</button>
<button onClick={()=>nav("database")} style={{width:36,height:36,borderRadius:8,border:"none",cursor:"pointer",background:pg==="database"?theme.bg4:"transparent",color:pg==="database"?theme.accent:theme.tf,display:"inline-flex",alignItems:"center",justifyContent:"center"}} title="Database">{Ic.database}</button>
<button onClick={()=>nav("settings")} style={{width:36,height:36,borderRadius:8,border:"none",cursor:"pointer",background:pg==="settings"?theme.bg4:"transparent",color:pg==="settings"?theme.accent:theme.tf,display:"inline-flex",alignItems:"center",justifyContent:"center"}} title="Settings">{Ic.gear}</button>
</div></div></nav>

{/* MAM Validation Warning Banner */}
{mamWarn?<div style={{maxWidth:1120,margin:"12px auto 0",padding:"10px 16px",background:theme.ylw+"18",border:`1px solid ${theme.ylw}44`,borderRadius:10,display:"flex",alignItems:"center",justifyContent:"space-between",gap:12,fontSize:13}}><span style={{color:theme.ylwt}}>⚠ MAM session may have expired — scans are paused. <button onClick={()=>nav("settings")} style={{background:"none",border:"none",color:theme.accent,cursor:"pointer",fontWeight:600,fontSize:13,padding:0,textDecoration:"underline"}}>Go to Settings</button> to update your token and re-validate.</span><button onClick={()=>setMamWarn(false)} style={{background:"none",border:"none",color:theme.tg,cursor:"pointer",fontSize:16,padding:"0 4px",flexShrink:0}}>✕</button></div>:null}

{/* ── Main Content ── */}
<main className="main-content" style={{maxWidth:1120,margin:"0 auto",padding:"28px 20px"}}>
<div className="page-content" key={pg+(pa||"")+activeLib}>
{pg==="dashboard"&&<Dashboard onNav={nav} libs={libs} activeLib={activeLib} switchLib={switchLib}/>}
{pg==="library"&&<BooksPage title="My Library" subtitle="books in your Calibre library" apiPath="/books" extraParams={{owned:true}} exportFilter="library"/>}
{pg==="authors"&&<AuthorsPage onNav={nav}/>}
{pg==="author"&&<AuthorDetailPage authorId={pa} onNav={nav}/>}
{pg==="missing"&&<BooksPage title="Missing Books" subtitle="books to find" apiPath="/books" extraParams={{owned:false}} exportFilter="missing"/>}
{pg==="upcoming"&&<BooksPage title="Upcoming Books" subtitle="unreleased books" apiPath="/upcoming" exportFilter="missing"/>}
{pg==="hidden"&&<HiddenPage onNav={nav}/>}
{pg==="importexport"&&<ImportExportPage/>}
{pg==="mam"&&<MAMPage onNav={nav}/>}
{pg==="suggestions"&&<SuggestionsPage onNav={nav}/>}
{pg==="database"&&<DatabasePage/>}
{pg==="settings"&&<SettingsPage/>}
</div></main>

{showAdd==="manual"&&<AddBookModal onClose={()=>setShowAdd(null)} onAdded={()=>{}}/>}
{showAdd==="url"&&<UrlSearchModal onClose={()=>setShowAdd(null)} onAdded={()=>{}}/>}
</>}
</div>
</TC.Provider>}
