/*** blackwhite — generated from total_dump_v5 layer JSON ***/
var CONFIG = { targetCompName:null, placeRef:"Текст", startTime:null, duration:null, place:"below:Текст" };
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){ var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; } }
function log(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }
function findLayer(c,n){ for(var i=1;i<=c.numLayers;i++) if(c.layer(i).name===n) return c.layer(i); return null; }
function findComp(){ var a=app.project.activeItem,i,it; if(CONFIG.targetCompName){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&it.name===CONFIG.targetCompName)return it;}} if(CONFIG.placeRef){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&findLayer(it,CONFIG.placeRef))return it;}} if(a&&a instanceof CompItem)return a; return null; }
function place(comp,L){ var ref=findLayer(comp,CONFIG.placeRef); if(ref)try{L.moveAfter(ref);}catch(e){} var t=(CONFIG.startTime!=null)?CONFIG.startTime:0; L.startTime=0; L.inPoint=t; L.outPoint=(CONFIG.duration!=null)?Math.min(comp.duration,t+CONFIG.duration):comp.duration; }
function setP(e,n,v){ try{var p=e.property(n);if(p)p.setValue(v);}catch(x){} }
(function(){ if(!app.project){return;} var comp=findComp(); if(!comp){throw new Error("blackwhite: target comp not found");} app.beginUndoGroup("blackwhite"); try{ var L=comp.layers.addSolid([1,1,1],"blackwhite",comp.width,comp.height,1); L.adjustmentLayer=true; var fx=L.property("ADBE Effect Parade"); var bw=fx.addProperty("ADBE Black&White"); if(!bw)throw new Error("blackwhite: ADBE Black&White unavailable"); setP(bw,"ADBE Black&White-0006",-100); setP(bw,"ADBE Black&White-0007",1); setP(bw,"ADBE Black&White-0008",[0.0078160008,0.006920415,0.019607844,1]); place(comp,L); log("blackwhite -> "+comp.name); }finally{app.endUndoGroup();} })();
