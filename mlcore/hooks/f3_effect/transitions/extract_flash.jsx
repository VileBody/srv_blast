/*** extract_flash (transition) — агрессивный инверт-импульс + Extract. Авто-каркас ***/
var CONFIG = { targetCompName:null, placeRef:"Текст", dropTime:null, startTime:null, duration:0.2835, place:"below:Текст", cuts:null };
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){ var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; } }
var BZ=KeyframeInterpolationType.BEZIER;
function log(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }
function setP(e,m,v){ try{e.property(m).setValue(v);}catch(x){} }
function findLayer(c,n){ for(var i=1;i<=c.numLayers;i++) if(c.layer(i).name===n) return c.layer(i); return null; }
function findComp(){ var a=app.project.activeItem,i,it;
  if(CONFIG.targetCompName){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&it.name===CONFIG.targetCompName)return it;}}
  if(CONFIG.placeRef){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&findLayer(it,CONFIG.placeRef))return it;}}
  if(a&&a instanceof CompItem)return a; var b=null;for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&(!b||it.numLayers>b.numLayers))b=it;} return b; }
function refLayer(comp){ return findLayer(comp,(CONFIG.place&&CONFIG.place.indexOf(":")>-1)?CONFIG.place.split(":")[1]:CONFIG.placeRef); }
function times(){ if(CONFIG.cuts&&CONFIG.cuts.length)return CONFIG.cuts; return [(CONFIG.startTime!=null)?CONFIG.startTime:((CONFIG.dropTime!=null)?CONFIG.dropTime:0)]; }
function one(comp,t0,ref){
  var L=comp.layers.addSolid([1,1,1],"extract flash",comp.width,comp.height,1); L.adjustmentLayer=true; L.startTime=0; L.inPoint=t0; L.outPoint=t0+CONFIG.duration;
  var fx=L.property("ADBE Effect Parade");
  var inv=fx.addProperty("ADBE Invert"); setP(inv,"ADBE Invert-0001",12); // Alpha
  var bl=inv.property("ADBE Invert-0002"); // Blend With Original — пульс 100/0/100/0
  bl.setValueAtTime(t0,100); bl.setValueAtTime(t0+0.0834,0); bl.setValueAtTime(t0+0.2001,100); bl.setValueAtTime(t0+0.2835,0);
  for(var k=1;k<=bl.numKeys;k++){ try{bl.setInterpolationTypeAtKey(k,BZ,BZ);}catch(e){} }
  var ex=fx.addProperty("ADBE Extract"); setP(ex,"ADBE Extract-0003",45); setP(ex,"ADBE Extract-0004",125); setP(ex,"ADBE Extract-0006",100);
  if(ref)try{L.moveAfter(ref);}catch(e){}
  return L;
}
(function(){ if(!app.project){log("нет проекта");return;} var comp=findComp(); if(!comp){log("нет компа");return;}
  app.beginUndoGroup("extract flash"); try{ var ts=times(),ref=refLayer(comp); for(var i=0;i<ts.length;i++)one(comp,ts[i],ref); log("extract flash x"+ts.length+" -> "+comp.name);}catch(e){log("err "+e+" "+(e.line||""));} finally{app.endUndoGroup();} })();
