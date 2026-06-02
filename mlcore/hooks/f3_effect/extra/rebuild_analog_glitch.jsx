/*** analog glitch — adjustment, авто-каркас (оркестратор/headless) ***/
var CONFIG = { targetCompName:null, placeRef:"Текст", startTime:null, duration:null, place:"below:Текст", opacity:null, blend:null };
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){ var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; } }
function log(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }
function setP(e,m,v){ try{e.property(m).setValue(v);}catch(x){} }
function findLayer(c,n){ for(var i=1;i<=c.numLayers;i++) if(c.layer(i).name===n) return c.layer(i); return null; }
function findComp(){ var a=app.project.activeItem,i,it;
  if(CONFIG.targetCompName){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&it.name===CONFIG.targetCompName)return it;}}
  if(CONFIG.placeRef){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&findLayer(it,CONFIG.placeRef))return it;}}
  if(a&&a instanceof CompItem)return a; var b=null;for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&(!b||it.numLayers>b.numLayers))b=it;} return b; }
function blendEnum(s){ s=String(s||"").toLowerCase(); return s==="add"?BlendingMode.ADD:s==="screen"?BlendingMode.SCREEN:s==="multiply"?BlendingMode.MULTIPLY:s==="overlay"?BlendingMode.OVERLAY:s==="normal"?BlendingMode.NORMAL:null; }
function place(comp,L){
  if(CONFIG.place){var m=CONFIG.place,ref=null;if(m.indexOf(":")>-1){ref=findLayer(comp,m.split(":")[1]);m=m.split(":")[0];}
    if(m==="above"&&ref)L.moveBefore(ref);else if(m==="below"&&ref)L.moveAfter(ref);else if(m==="top")L.moveToBeginning();else if(m==="bottom")L.moveToEnd();}
  if(CONFIG.startTime!=null){ L.startTime=CONFIG.startTime; L.inPoint=CONFIG.startTime; if(CONFIG.duration!=null)L.outPoint=CONFIG.startTime+CONFIG.duration; }
  else if(CONFIG.duration!=null){ L.outPoint=L.inPoint+CONFIG.duration; }
  if(CONFIG.opacity!=null){try{L.property("ADBE Transform Group").property("ADBE Opacity").setValue(CONFIG.opacity);}catch(e){}}
  var bm=blendEnum(CONFIG.blend); if(bm!=null){try{L.blendingMode=bm;}catch(e){}}
}
function build(comp){
  var L=comp.layers.addSolid([1,1,1],"analog glitch",comp.width,comp.height,1); L.adjustmentLayer=true; L.startTime=0; L.inPoint=0; L.outPoint=comp.duration;
  var fx=L.property("ADBE Effect Parade");
  var bc=fx.addProperty("ADBE Brightness & Contrast 2"); setP(bc,"ADBE Brightness & Contrast 2-0002",100);
  var v1=fx.addProperty("ADBE Venetian Blinds"); setP(v1,"ADBE Venetian Blinds-0001",77); setP(v1,"ADBE Venetian Blinds-0003",8);
  var v2=fx.addProperty("ADBE Venetian Blinds"); setP(v2,"ADBE Venetian Blinds-0001",77); setP(v2,"ADBE Venetian Blinds-0002",90); setP(v2,"ADBE Venetian Blinds-0003",4);
  var ch=fx.addProperty("ADBE Simple Choker"); setP(ch,"ADBE Simple Choker-0002",0.4);
  var ww=fx.addProperty("ADBE Wave Warp"); setP(ww,"ADBE Wave Warp-0001",8); setP(ww,"ADBE Wave Warp-0002",6); setP(ww,"ADBE Wave Warp-0003",1200); setP(ww,"ADBE Wave Warp-0004",0);
  var g1=fx.addProperty("ADBE Glo2"); setP(g1,"ADBE Glo2-0002",12.75); setP(g1,"ADBE Glo2-0003",17); setP(g1,"ADBE Glo2-0004",1.95); setP(g1,"ADBE Glo2-0007",2); setP(g1,"ADBE Glo2-0008",2); setP(g1,"ADBE Glo2-0012",[1,0,0,1]);
  var g2=fx.addProperty("ADBE Glo2"); setP(g2,"ADBE Glo2-0002",125); setP(g2,"ADBE Glo2-0003",17); setP(g2,"ADBE Glo2-0004",1.95); setP(g2,"ADBE Glo2-0007",2); setP(g2,"ADBE Glo2-0008",2); setP(g2,"ADBE Glo2-0012",[1,0,0,1]);
  var pt=fx.addProperty("ADBE Posterize Time"); setP(pt,"ADBE Posterize Time-0001",12);
  return L;
}
(function(){ if(!app.project){log("нет проекта");return;} var comp=findComp(); if(!comp){log("нет компа");return;}
  app.beginUndoGroup("analog glitch"); try{ place(comp,build(comp)); log("analog glitch -> "+comp.name);}catch(e){log("err "+e+" "+(e.line||""));} finally{app.endUndoGroup();} })();
