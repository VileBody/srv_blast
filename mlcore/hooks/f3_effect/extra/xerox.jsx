/*** xerox — adjustment, авто-каркас. Roto Brush выкинут. VISINF Grain = плагин ***/
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
  var L=comp.layers.addSolid([1,1,1],"xerox",comp.width,comp.height,1); L.adjustmentLayer=true; L.startTime=0; L.inPoint=0; L.outPoint=comp.duration;
  var fx=L.property("ADBE Effect Parade");
  try{ var gi=fx.addProperty("VISINF Grain Implant"); setP(gi,"VISINF Grain Implant-0021",3); }catch(e){} // Add Grain (плагин)
  try{ fx.addProperty("ADBE CurvesCustom"); }catch(e){}                                                  // Curves (дефолт)
  var th=fx.addProperty("ADBE Threshold2"); setP(th,"ADBE Threshold2-0001",0.23529411764706);
  var bb=fx.addProperty("ADBE Box Blur2"); setP(bb,"ADBE Box Blur2-0001",0.1);
  var gl=fx.addProperty("ADBE Glo2"); setP(gl,"ADBE Glo2-0002",178.5); setP(gl,"ADBE Glo2-0003",195);
  var no=fx.addProperty("ADBE Noise2"); setP(no,"ADBE Noise2-0001",15); setP(no,"ADBE Noise2-0002",0);
  return L;
}
(function(){ if(!app.project){log("нет проекта");return;} var comp=findComp(); if(!comp){log("нет компа");return;}
  app.beginUndoGroup("xerox"); try{ place(comp,build(comp)); log("xerox -> "+comp.name);}catch(e){log("err "+e+" "+(e.line||""));} finally{app.endUndoGroup();} })();
