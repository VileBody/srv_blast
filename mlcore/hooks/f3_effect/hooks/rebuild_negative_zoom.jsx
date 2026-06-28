/*** negative zoom (hook/drop) — adjustment, анкер на dropTime. Авто-каркас ***/
var CONFIG = { targetCompName:null, placeRef:"Текст", dropTime:null, startTime:null, duration:0.25025025025025, place:"below:Текст", opacity:null, blend:null };
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){ var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; } }
var BZ = KeyframeInterpolationType.BEZIER;
function log(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }
function setP(e,m,v){ try{e.property(m).setValue(v);}catch(x){} }
// кейфреймы по ДОЛЯМ длительности (recalc под длину леера) + bezier-ease
function setKF(e,m,t0,dur,pairs){ try{ var p=e.property(m); var i;
  for(i=0;i<pairs.length;i++) p.setValueAtTime(t0+pairs[i][0]*dur, pairs[i][1]);
  for(i=1;i<=p.numKeys;i++){ try{ p.setInterpolationTypeAtKey(i,BZ,BZ);
    p.setTemporalEaseAtKey(i,[new KeyframeEase(0,33.333333)],[new KeyframeEase(0,33.333333)]); }catch(x){} } }catch(z){} }
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
  var dur=(CONFIG.duration!=null)?CONFIG.duration:0.25025025025025;
  app.beginUndoGroup("negative zoom");
  try{
    var L=comp.layers.addSolid([1,1,1],"negative zoom",comp.width,comp.height,1); L.adjustmentLayer=true; L.startTime=0; L.inPoint=t0; L.outPoint=t0+dur;
    var fx=L.property("ADBE Effect Parade");
    // Lumetri: только Tint/Saturation читаемы (остальное — CUSTOM/NO_VALUE)
    try{ var lum=fx.addProperty("ADBE Lumetri"); setP(lum,"ADBE Lumetri-0008",300); setP(lum,"ADBE Lumetri-0020",0); }catch(e){}
    var no=fx.addProperty("ADBE Noise2"); setP(no,"ADBE Noise2-0001",20); setP(no,"ADBE Noise2-0002",0);
    var tr=fx.addProperty("ADBE Geometry2"); setP(tr,"ADBE Geometry2-0003",115);      // Transform Scale = зум
    var bc=fx.addProperty("ADBE Brightness & Contrast 2");                            // пульс 0->60->0 (0 / 0.5 / 1.0 длит.)
    setKF(bc,"ADBE Brightness & Contrast 2-0001",t0,dur,[[0,0],[0.5,60],[1.0,0]]);
    setKF(bc,"ADBE Brightness & Contrast 2-0002",t0,dur,[[0,0],[0.5,60],[1.0,0]]);
    var iv=fx.addProperty("ADBE Invert");                                             // негатив-вспышка blend 0->100->0
    setKF(iv,"ADBE Invert-0002",t0,dur,[[0,0],[0.3333,100],[0.5,0]]);
    placeZ(comp,L); log("negative zoom -> "+comp.name+" @ "+t0+" dur "+dur);
  }catch(e){log("err "+e+" "+(e.line||""));} finally{app.endUndoGroup();}
})();
