/*** snap_wipe (transition) — на каждой склейке. Авто-каркас ***/
var CONFIG = { targetCompName:null, placeRef:"Текст", dropTime:null, startTime:null, duration:0.067, place:"below:Текст", cuts:null };
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
  var dur=CONFIG.duration, cx=comp.width/2, cy=comp.height/2;
  var L=comp.layers.addSolid([1,1,1],"snap wipe",comp.width,comp.height,1); L.adjustmentLayer=true; L.startTime=0; L.inPoint=t0; L.outPoint=t0+dur;
  var fx=L.property("ADBE Effect Parade");
  var geo=fx.addProperty("ADBE Geometry2"); var ap=geo.property("ADBE Geometry2-0001"); // Anchor (горизонт. свайп)
  ap.setValueAtTime(t0,[cx,cy]); ap.setValueAtTime(t0+dur*0.5,[cx+140,cy]); ap.setValueAtTime(t0+dur,[cx+340,cy]);
  for(var k=1;k<=ap.numKeys;k++){ try{ap.setInterpolationTypeAtKey(k,BZ,BZ);}catch(e){} }
  var db=fx.addProperty("ADBE Motion Blur"); setP(db,"ADBE Motion Blur-0001",90); setP(db,"ADBE Motion Blur-0002",100);
  var mm=fx.addProperty("ADBE Minimax"); setP(mm,"ADBE Minimax-0002",165); setP(mm,"ADBE Minimax-0004",2);
  var op=fx.addProperty("ADBE Optics Compensation"); setP(op,"ADBE Optics Compensation-0001",120); setP(op,"ADBE Optics Compensation-0002",1);
  if(ref)try{L.moveAfter(ref);}catch(e){}
  return L;
}
(function(){ if(!app.project){log("нет проекта");return;} var comp=findComp(); if(!comp){log("нет компа");return;}
  app.beginUndoGroup("snap wipe"); try{ var ts=times(),ref=refLayer(comp); for(var i=0;i<ts.length;i++)one(comp,ts[i],ref); log("snap wipe x"+ts.length+" -> "+comp.name);}catch(e){log("err "+e+" "+(e.line||""));} finally{app.endUndoGroup();} })();
