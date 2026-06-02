/*** flash slow shutter (hook) — adjustment, анкер на dropTime. Авто-каркас ***/
var CONFIG = { targetCompName:null, placeRef:"Текст", dropTime:null, startTime:null, duration:0.5, place:"below:Текст", opacity:null, blend:null };
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){ var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; } }
function log(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }
function setP(e,m,v){ try{e.property(m).setValue(v);}catch(x){} }
function setK(e,m,a){ try{var p=e.property(m);for(var i=0;i<a.length;i++)p.setValueAtTime(a[i][0],a[i][1]);}catch(x){} }
function findLayer(c,n){ for(var i=1;i<=c.numLayers;i++) if(c.layer(i).name===n) return c.layer(i); return null; }
function findComp(){ var a=app.project.activeItem,i,it;
  if(CONFIG.targetCompName){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&it.name===CONFIG.targetCompName)return it;}}
  if(CONFIG.placeRef){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&findLayer(it,CONFIG.placeRef))return it;}}
  if(a&&a instanceof CompItem)return a; var b=null;for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&(!b||it.numLayers>b.numLayers))b=it;} return b; }
function blendEnum(s){ s=String(s||"").toLowerCase(); return s==="add"?BlendingMode.ADD:s==="screen"?BlendingMode.SCREEN:s==="multiply"?BlendingMode.MULTIPLY:s==="overlay"?BlendingMode.OVERLAY:s==="normal"?BlendingMode.NORMAL:null; }
function placeZ(comp,L){ if(CONFIG.place){var m=CONFIG.place,ref=null;if(m.indexOf(":")>-1){ref=findLayer(comp,m.split(":")[1]);m=m.split(":")[0];}
    if(m==="above"&&ref)L.moveBefore(ref);else if(m==="below"&&ref)L.moveAfter(ref);else if(m==="top")L.moveToBeginning();else if(m==="bottom")L.moveToEnd();}
  if(CONFIG.opacity!=null){try{L.property("ADBE Transform Group").property("ADBE Opacity").setValue(CONFIG.opacity);}catch(e){}}
  var bm=blendEnum(CONFIG.blend);if(bm!=null){try{L.blendingMode=bm;}catch(e){}} }
(function(){ if(!app.project){log("нет проекта");return;} var comp=findComp(); if(!comp){log("нет компа");return;}
  var t0=(CONFIG.startTime!=null)?CONFIG.startTime:((CONFIG.dropTime!=null)?CONFIG.dropTime:0);
  var dur=(CONFIG.duration!=null)?CONFIG.duration:0.5;
  app.beginUndoGroup("flash slow shutter");
  try{
    var L=comp.layers.addSolid([1,1,1],"flash slow shutter",comp.width,comp.height,1); L.adjustmentLayer=true; L.startTime=0; L.inPoint=t0; L.outPoint=t0+dur;
    var fx=L.property("ADBE Effect Parade");
    var ec=fx.addProperty("ADBE Echo"); setP(ec,"ADBE Echo-0001",-0.2); setP(ec,"ADBE Echo-0002",5); setP(ec,"ADBE Echo-0004",0.8); setP(ec,"ADBE Echo-0005",2);
    var pt=fx.addProperty("ADBE Posterize Time"); setP(pt,"ADBE Posterize Time-0001",8);
    try{ var lu=fx.addProperty("ADBE Lumetri"); setK(lu,"ADBE Lumetri-0011",[[t0,3],[t0+0.1,0]]); }catch(e){} // экспозиция-вспышка на дропе
    placeZ(comp,L); log("flash slow shutter -> "+comp.name+" @ "+t0);
  }catch(e){log("err "+e+" "+(e.line||""));} finally{app.endUndoGroup();}
})();
