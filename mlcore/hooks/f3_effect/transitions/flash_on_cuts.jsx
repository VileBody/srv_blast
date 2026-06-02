/*** flash_on_cuts (transition, cut_overlay) — белая вспышка на склейках. Авто-каркас ***/
var CONFIG = { targetCompName:null, placeRef:"Текст", dropTime:null, startTime:null, duration:0.633, place:"below:Текст", cuts:null,
               opStart:25, fadeTime:0.6 };
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){ var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; } }
function log(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }
function findLayer(c,n){ for(var i=1;i<=c.numLayers;i++) if(c.layer(i).name===n) return c.layer(i); return null; }
function findComp(){ var a=app.project.activeItem,i,it;
  if(CONFIG.targetCompName){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&it.name===CONFIG.targetCompName)return it;}}
  if(CONFIG.placeRef){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&findLayer(it,CONFIG.placeRef))return it;}}
  if(a&&a instanceof CompItem)return a; var b=null;for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&(!b||it.numLayers>b.numLayers))b=it;} return b; }
function refLayer(comp){ return findLayer(comp,(CONFIG.place&&CONFIG.place.indexOf(":")>-1)?CONFIG.place.split(":")[1]:CONFIG.placeRef); }
function times(){ if(CONFIG.cuts&&CONFIG.cuts.length)return CONFIG.cuts; return [(CONFIG.startTime!=null)?CONFIG.startTime:((CONFIG.dropTime!=null)?CONFIG.dropTime:0)]; }
function whiteSolid(comp){ var t=comp.layers.addSolid([1,1,1],"White Solid 1",comp.width,comp.height,comp.pixelAspect); var s=t.source; t.remove(); return s; }
function one(comp,src,t0,ref){
  var L=comp.layers.add(src,CONFIG.duration); L.name="Вспышка"; L.inPoint=t0; L.outPoint=Math.min(comp.duration,t0+CONFIG.duration);
  try{L.blendingMode=BlendingMode.ADD;}catch(e){} L.motionBlur=false;
  var op=L.property("ADBE Transform Group").property("ADBE Opacity");
  var ft=Math.min(t0+CONFIG.fadeTime,L.outPoint);
  op.setValueAtTime(t0,CONFIG.opStart); op.setValueAtTime(ft,0);
  if(ref)try{L.moveAfter(ref);}catch(e){}
  return L;
}
(function(){ if(!app.project){log("нет проекта");return;} var comp=findComp(); if(!comp){log("нет компа");return;}
  app.beginUndoGroup("flash on cuts"); try{ var src=whiteSolid(comp),ts=times(),ref=refLayer(comp); for(var i=0;i<ts.length;i++)one(comp,src,ts[i],ref); log("flash on cuts x"+ts.length+" -> "+comp.name);}catch(e){log("err "+e+" "+(e.line||""));} finally{app.endUndoGroup();} })();
