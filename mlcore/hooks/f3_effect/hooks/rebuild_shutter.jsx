/*** shutter (hook) — нарезка по 0.1с от dropTime. Авто-каркас ***/
var CONFIG = { targetCompName:null, placeRef:"Текст", dropTime:null, startTime:null, place:"below:Текст", chunks:6 };
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){ var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; } }
var CHUNK=0.1001001001001, MB_RES=0.06673340006673;
function log(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }
function setP(e,m,v){ try{e.property(m).setValue(v);}catch(x){} }
function findLayer(c,n){ for(var i=1;i<=c.numLayers;i++) if(c.layer(i).name===n) return c.layer(i); return null; }
function findComp(){ var a=app.project.activeItem,i,it;
  if(CONFIG.targetCompName){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&it.name===CONFIG.targetCompName)return it;}}
  if(CONFIG.placeRef){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&findLayer(it,CONFIG.placeRef))return it;}}
  if(a&&a instanceof CompItem)return a; var b=null;for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&(!b||it.numLayers>b.numLayers))b=it;} return b; }
function adj(comp,name,inP,outP){ var L=comp.layers.addSolid([1,1,1],name,comp.width,comp.height,1); L.adjustmentLayer=true; L.startTime=0; L.inPoint=inP; L.outPoint=outP; return L; }
(function(){ if(!app.project){log("нет проекта");return;} var comp=findComp(); if(!comp){log("нет компа");return;}
  var t0=(CONFIG.startTime!=null)?CONFIG.startTime:((CONFIG.dropTime!=null)?CONFIG.dropTime:0);
  app.beginUndoGroup("shutter"); var built=[];
  try{
    for(var i=0;i<CONFIG.chunks;i++){
      var a=t0+i*CHUNK, b=t0+(i+1)*CHUNK;
      if(i%2===0){ var inv=adj(comp,"shutter invert "+i,a,b); setP(inv.property("ADBE Effect Parade").addProperty("ADBE Invert"),"ADBE Invert-0001",8); built.push(inv); }
      if(i<5){ var L=adj(comp,"shutter blur "+i,a,b); var mb=L.property("ADBE Effect Parade").addProperty("ADBE Motion Blur");
        var d=mb.property("ADBE Motion Blur-0001"); d.setValueAtTime(a,90); d.setValueAtTime(a+MB_RES,0);
        var bl=mb.property("ADBE Motion Blur-0002"); bl.setValueAtTime(a,15); bl.setValueAtTime(a+MB_RES,0); built.push(L); }
      // нечётные чанки: в оригинале PNG-оверлей — добавляется отдельным шагом (лого/бренд)
    }
    var txt=findLayer(comp,(CONFIG.place&&CONFIG.place.indexOf(":")>-1)?CONFIG.place.split(":")[1]:CONFIG.placeRef);
    if(txt){ for(var k=0;k<built.length;k++){ try{ built[k].moveAfter(txt); }catch(e){} } }
    log("shutter: слоёв "+built.length+" @ "+t0+" -> "+comp.name);
  }catch(e){log("err "+e+" "+(e.line||""));} finally{app.endUndoGroup();}
})();
