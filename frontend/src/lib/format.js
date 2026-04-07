// Display formatting helpers for AthenaScout.

// Calculate a percentage as a single-decimal number.
// pct(50, 200) → 25
// pct(0, 0)    → 100 (defensive default)
export const pct=(o,t)=>t===0?100:Math.floor(o/t*1000)/10;

// Format a Unix timestamp as a relative time string.
// "Just now" / "5m ago" / "3h ago" / "2d ago" / "Never"
export const timeAgo=ts=>{if(!ts)return"Never";const d=Math.floor((Date.now()/1000-ts)/60);if(d<1)return"Just now";if(d<60)return`${d}m ago`;if(d<1440)return`${Math.floor(d/60)}h ago`;return`${Math.floor(d/1440)}d ago`};

// Format a date string to YYYY-MM-DD (truncating any time component).
// Returns empty string for null/undefined input.
export const fmtDate=d=>d&&d.length>=10?d.substring(0,10):d||"";
