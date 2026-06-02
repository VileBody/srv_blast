/*** minimax (transition) — вертикальная вспышка на склейках. Авто-каркас ***/
var CONFIG = { targetCompName:null, placeRef:"Текст", dropTime:null, startTime:null, duration:0.05, place:"below:Текст", cuts:null };
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){ var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; } }
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
  var L=comp.layers.addSolid([1,1,1],"minimax flash",comp.width,comp.height,1); L.adjustmentLayer=true; L.startTime=0; L.inPoint=t0; L.outPoint=t0+CONFIG.duration;
  var fx=L.property("ADBE Effect Parade");
  var mm=fx.addProperty("ADBE Minimax"); setP(mm,"ADBE Minimax-0002",50); setP(mm,"ADBE Minimax-0004",2);
  var lk=fx.addProperty("ADBE Luma Key"); setP(lk,"ADBE Luma Key-0002",100);
  var sc=fx.addProperty("ADBE Scatter"); setP(sc,"ADBE Scatter-0001",20);
  var ex=fx.addProperty("ADBE Exposure2"); setP(ex,"ADBE Exposure2-0003",0.8);
  if(ref)try{L.moveAfter(ref);}catch(e){}
  return L;
}
(function(){ if(!app.project){log("нет проекта");return;} var comp=findComp(); if(!comp){log("нет компа");return;}
  app.beginUndoGroup("minimax"); try{ var ts=times(),ref=refLayer(comp); for(var i=0;i<ts.length;i++)one(comp,ts[i],ref); log("minimax x"+ts.length+" -> "+comp.name);}catch(e){log("err "+e+" "+(e.line||""));} finally{app.endUndoGroup();} })();
