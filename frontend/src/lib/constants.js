// Navigation menu structure for AthenaScout top nav.
// Each entry: {id, label, icon}
// id is matched against the current page state in App.jsx.
//
// Phase 3c: "suggestions" was added; auto-hidden when its count is 0
// (gating done in App.jsx). To make room without crowding the navbar,
// "importexport" was moved to a right-side icon button alongside the
// theme/database/settings cluster — it's a power-user backup feature
// accessed rarely, so it fits better as an icon than a text item.

export const NAV=[
  {id:"library",label:"Library",icon:"📖"},
  {id:"authors",label:"Authors",icon:"◉"},
  {id:"missing",label:"Missing",icon:"◌"},
  {id:"upcoming",label:"Upcoming",icon:"📅"},
  {id:"mam",label:"MAM",icon:"🔍"},
  {id:"suggestions",label:"Suggestions",icon:"💡"},
];
