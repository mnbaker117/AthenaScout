// Login + first-run admin setup screen for AthenaScout.
//
// Phase 22B.3 Stage 2A. Renders in two modes depending on the
// `isFirstRun` prop:
//   - true:  "Welcome — create your admin account" with a confirm-password field
//   - false: "Sign in to AthenaScout"
//
// On successful submit, calls onLoginSuccess() which is wired in App.jsx
// to flip the auth state and re-render the main app.
import { useState } from "react";
import { useTheme } from "../theme";
import { api } from "../api";
import { Btn } from "../components/Btn";
import { Spin } from "../components/Spin";

export default function LoginPage({onLoginSuccess,isFirstRun}){const t=useTheme();
const[username,setUsername]=useState("");const[password,setPassword]=useState("");const[confirm,setConfirm]=useState("");const[err,setErr]=useState("");const[busy,setBusy]=useState(false);
const isSetup=!!isFirstRun;
const submit=async()=>{setErr("");
if(isSetup){if(username.length<3){setErr("Username must be at least 3 characters");return}if(password.length<8){setErr("Password must be at least 8 characters");return}if(password!==confirm){setErr("Passwords don't match");return}}
else{if(!username||!password){setErr("Username and password required");return}}
setBusy(true);try{await api.post(isSetup?"/auth/setup":"/auth/login",{username,password});onLoginSuccess&&onLoginSuccess()}catch(e){setErr(e.message||"Login failed");setBusy(false)}};
const onKey=e=>{if(e.key==="Enter")submit()};
const ist={width:"100%",padding:"10px 12px",background:t.inp,border:`1px solid ${t.border}`,borderRadius:6,color:t.text2,fontSize:14,boxSizing:"border-box",fontFamily:"inherit"};
const lbl={display:"block",marginBottom:4,marginTop:12,fontSize:12,fontWeight:600,color:t.tg,textTransform:"uppercase",letterSpacing:"0.05em"};
return<div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"100vh",background:t.bg,color:t.text2,padding:20}}>
<div style={{background:t.bg2,border:`1px solid ${t.border}`,borderRadius:12,padding:32,maxWidth:420,width:"100%",boxShadow:"0 4px 24px rgba(0,0,0,0.2)"}}>
<div style={{display:"flex",alignItems:"center",gap:10,marginBottom:6}}>
<svg viewBox="0 0 512 512" style={{width:32,height:32}}><defs><linearGradient id="ig2" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style={{stopColor:"#f0c060"}}/><stop offset="100%" style={{stopColor:"#d4a040"}}/></linearGradient></defs><circle cx="256" cy="256" r="240" fill="#2a1f4e" stroke="#d4a040" strokeWidth="12"/><circle cx="220" cy="200" r="22" fill="none" stroke="url(#ig2)" strokeWidth="6"/><circle cx="292" cy="200" r="22" fill="none" stroke="url(#ig2)" strokeWidth="6"/><circle cx="220" cy="200" r="8" fill="url(#ig2)"/><circle cx="292" cy="200" r="8" fill="url(#ig2)"/><path d="M248 220 L256 235 L264 220" fill="none" stroke="url(#ig2)" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round"/><path d="M195 155 L212 178 L180 173" fill="url(#ig2)" opacity="0.8"/><path d="M317 155 L300 178 L332 173" fill="url(#ig2)" opacity="0.8"/><path d="M140 320 L256 290 L372 320 L372 365 C372 365 314 348 256 358 C198 348 140 365 140 365 Z" fill="url(#ig2)" opacity="0.85"/></svg>
<h1 style={{fontSize:22,fontWeight:700,color:t.accent,margin:0}}>AthenaScout</h1>
</div>
<p style={{margin:"0 0 20px 0",color:t.tg,fontSize:13}}>{isSetup?"Welcome! Create your admin account to get started.":"Sign in to your account."}</p>
<label style={lbl}>Username</label>
<input type="text" value={username} onChange={e=>setUsername(e.target.value)} onKeyDown={onKey} autoComplete="username" autoFocus disabled={busy} style={ist}/>
<label style={lbl}>Password</label>
<input type="password" value={password} onChange={e=>setPassword(e.target.value)} onKeyDown={onKey} autoComplete={isSetup?"new-password":"current-password"} disabled={busy} style={ist}/>
{isSetup?<>
<label style={lbl}>Confirm Password</label>
<input type="password" value={confirm} onChange={e=>setConfirm(e.target.value)} onKeyDown={onKey} autoComplete="new-password" disabled={busy} style={ist}/>
</>:null}
{err?<div style={{marginTop:14,padding:"8px 12px",background:t.redb||"rgba(199,92,92,0.15)",border:`1px solid ${t.red}55`,color:t.redt,borderRadius:6,fontSize:13}}>{err}</div>:null}
<div style={{marginTop:20,display:"flex",justifyContent:"flex-end"}}>
<Btn variant="accent" onClick={submit} disabled={busy}>{busy?<Spin/>:(isSetup?"Create Account":"Sign In")}</Btn>
</div>
{isSetup?<div style={{marginTop:18,padding:"10px 12px",background:t.bg4,border:`1px solid ${t.borderL}`,borderRadius:6,fontSize:11,color:t.tg,lineHeight:1.5}}>
<strong style={{color:t.text2}}>Important:</strong> AthenaScout has a single admin account. Choose a strong password — there is no automated password recovery. If you forget it, you can reset by editing <code style={{color:t.accent}}>athenascout_auth.db</code> on the server directly.
</div>:null}
</div>
</div>}
