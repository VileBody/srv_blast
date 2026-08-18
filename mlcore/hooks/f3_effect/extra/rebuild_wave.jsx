/*** wave — generated from total_dump_v5 layer JSON ***/
var CONFIG = { targetCompName:null, placeRef:"Текст", startTime:null, duration:null, place:"below:Текст" };
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){ var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; } }
function log(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }
function findLayer(c,n){ for(var i=1;i<=c.numLayers;i++) if(c.layer(i).name===n) return c.layer(i); return null; }
function findComp(){ var a=app.project.activeItem,i,it; if(CONFIG.targetCompName){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&it.name===CONFIG.targetCompName)return it;}} if(CONFIG.placeRef){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&findLayer(it,CONFIG.placeRef))return it;}} if(a&&a instanceof CompItem)return a; return null; }
function place(comp,L){ var ref=findLayer(comp,CONFIG.placeRef); if(ref)try{L.moveAfter(ref);}catch(e){} var t=(CONFIG.startTime!=null)?CONFIG.startTime:0; L.startTime=0; L.inPoint=t; L.outPoint=(CONFIG.duration!=null)?Math.min(comp.duration,t+CONFIG.duration):comp.duration; }
function setP(e,n,v){ try{var p=e.property(n);if(p)p.setValue(v);}catch(x){} }
function fxS(){ try{ return (CONFIG.fxScale && CONFIG.fxScale.i>0) ? CONFIG.fxScale : null; }catch(e){ return null; } }
function sx(v){ var s=fxS(); return s? v*s.x : v; }
function sy(v){ var s=fxS(); return s? v*s.y : v; }
function si(v){ var s=fxS(); return s? v*s.i : v; }
/* Синий tint-шейп поверх варпа (дамп wave 20260817, комп-источник 576x1024).
   Размеры/смещение держим В ДОЛЯХ комп-размера — рендер-комп 1080x1960, не 576x1024.
   Blend 5222 = сырой код енама из дампа (AE принимает число); NORMAL=5212 для сверки. */
var TINT = { kw:590.222222/576, kh:1045.333333/1024, ox:-5.333333/576, oy:-12.444444/1024,
             color:[0,0.09411748250326,1,1], opacity:48, blend:5222 };
function addWaveTint(comp){
  var S=comp.layers.addShape(); S.name="wave_tint";
  var grp=S.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group"); grp.name="Rectangle 1";
  var cont=grp.property("ADBE Vectors Group");
  var rect=cont.addProperty("ADBE Vector Shape - Rect");
  setP(rect,"ADBE Vector Rect Size",[comp.width*TINT.kw, comp.height*TINT.kh]);
  var stroke=cont.addProperty("ADBE Vector Graphic - Stroke"); setP(stroke,"ADBE Vector Stroke Width",0);
  var fill=cont.addProperty("ADBE Vector Graphic - Fill"); setP(fill,"ADBE Vector Fill Color",TINT.color);
  var gt=grp.property("ADBE Vector Transform Group");
  setP(gt,"ADBE Vector Position",[comp.width*TINT.ox, comp.height*TINT.oy]);
  setP(gt,"ADBE Vector Group Opacity",TINT.opacity);
  var tr=S.property("ADBE Transform Group");
  setP(tr,"ADBE Anchor Point",[0,0]);
  setP(tr,"ADBE Position",[comp.width/2, comp.height/2]);
  try{ S.collapseTransformation=true; }catch(e1){}
  try{ S.blendingMode=TINT.blend; }catch(e2){}
  return S;
}
(function(){ if(!app.project){return;} var comp=findComp(); if(!comp){throw new Error("wave: target comp not found");} app.beginUndoGroup("wave"); try{ var L=comp.layers.addSolid([1,1,1],"wave",comp.width,comp.height,1); L.adjustmentLayer=true; var fx=L.property("ADBE Effect Parade"); var w=fx.addProperty("ADBE Wave Warp"); setP(w,"ADBE Wave Warp-0001",si(5)); setP(w,"ADBE Wave Warp-0002",si(2)); setP(w,"ADBE Wave Warp-0003",125.4); setP(w,"ADBE Wave Warp-0005",-0.62); setP(w,"ADBE Wave Warp-0006",3); var td=fx.addProperty("ADBE Turbulent Displace"); setP(td,"ADBE Turbulent Displace-0002",4); setP(td,"ADBE Turbulent Displace-0003",1000); setP(td,"ADBE Turbulent Displace-0005",1.97); var tr=fx.addProperty("ADBE Geometry2"); setP(tr,"ADBE Geometry2-0003",103); place(comp,L); var S=addWaveTint(comp); place(comp,S); try{S.moveBefore(L);}catch(eM){} log("wave -> "+comp.name); }finally{app.endUndoGroup();} })();
