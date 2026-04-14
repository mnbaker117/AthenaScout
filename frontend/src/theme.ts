// Theme definitions, context, and hook for AthenaScout.
//
// THEMES is the master list of color palettes (dark/dim/light).
// TC is the React context that provides the active theme.
// useTheme() is a thin wrapper around useContext(TC) for use in components.
//
// To use in a component:
//   import { useTheme } from "../theme";
//   function MyComponent() {
//     const t = useTheme();
//     return <div style={{color: t.text}}>Hello</div>;
//   }
import { createContext, useContext } from "react";
import type { Theme, ThemeName } from "./types";

export const THEMES: Record<ThemeName, Theme> = {
  dark:{name:"Dark",bg:"#0a0a1a",bg2:"#12122a",bg3:"#0e0e22",bg4:"#1a1a2e",border:"#2a2a4a",borderH:"#4a4a7a",borderL:"#1a1a2e",text:"#e0e0f0",text2:"#c0c0e0",tm:"#a0a0c0",td:"#808098",tf:"#707090",tg:"#505070",ti:"#404060",accent:"#d4a357",abg:"rgba(212,163,87,0.15)",abr:"rgba(212,163,87,0.3)",grn:"#3d9970",grnt:"#5bc49a",grnb:"rgba(61,153,112,0.15)",red:"#c75c5c",redt:"#e87070",redb:"rgba(199,92,92,0.15)",ylw:"#d4a357",ylwt:"#e8c080",ylwb:"rgba(212,163,87,0.15)",pur:"#6a5acd",purt:"#8888cc",purb:"rgba(74,74,138,0.15)",cyan:"#2d8a8a",cyant:"#5ecece",cyanb:"rgba(45,138,138,0.15)",inp:"#12122a"},
  dim:{name:"Dim",bg:"#2a2a30",bg2:"#333338",bg3:"#2e2e34",bg4:"#3a3a40",border:"#4a4a52",borderH:"#66666e",borderL:"#404048",text:"#eaeaea",text2:"#d8d8d8",tm:"#b8b8c0",td:"#989898",tf:"#808088",tg:"#686870",ti:"#585860",accent:"#e0a850",abg:"rgba(224,168,80,0.18)",abr:"rgba(224,168,80,0.35)",grn:"#4aaa80",grnt:"#66c8a0",grnb:"rgba(74,170,128,0.18)",red:"#d06868",redt:"#f08080",redb:"rgba(208,104,104,0.18)",ylw:"#d4a357",ylwt:"#e8c080",ylwb:"rgba(212,163,87,0.18)",pur:"#7a6ad8",purt:"#a0a0d8",purb:"rgba(122,106,216,0.18)",cyan:"#3a9a9a",cyant:"#6ed8d8",cyanb:"rgba(58,154,154,0.18)",inp:"#333338"},
  light:{name:"Light",bg:"#f5f5f0",bg2:"#ffffff",bg3:"#fafaf8",bg4:"#eeeee8",border:"#d8d8d0",borderH:"#b0b0a8",borderL:"#e8e8e0",text:"#1a1a2a",text2:"#2a2a3a",tm:"#505068",td:"#686880",tf:"#888898",tg:"#a0a0b0",ti:"#c0c0c8",accent:"#b8862d",abg:"rgba(184,134,45,0.12)",abr:"rgba(184,134,45,0.3)",grn:"#2d8060",grnt:"#1a6848",grnb:"rgba(45,128,96,0.1)",red:"#b84444",redt:"#a03030",redb:"rgba(184,68,68,0.1)",ylw:"#a07828",ylwt:"#886020",ylwb:"rgba(160,120,40,0.1)",pur:"#5848b0",purt:"#4838a0",purb:"rgba(88,72,176,0.1)",cyan:"#1a7070",cyant:"#0a5858",cyanb:"rgba(26,112,112,0.1)",inp:"#ffffff"},
};

export const TC = createContext<Theme>(THEMES.dark);
export const useTheme = (): Theme => useContext(TC);
