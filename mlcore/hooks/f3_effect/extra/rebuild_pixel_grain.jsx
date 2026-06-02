/*** pixel grain — импорт .aep + copyToComp (пресет зерна нечитаем). Авто-каркас ***/
var CONFIG = { targetCompName:null, placeRef:"Текст", startTime:null, duration:null, place:"below:Текст", opacity:null, blend:null };
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){ var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; } }
var AEP_PATH = (function(){ return new File($.fileName).parent.fsName + "/pixel graim.aep"; })();
var SRC_FOLDER = "[src] pixel grain";

function log(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }
function findLayer(c,n){ for(var i=1;i<=c.numLayers;i++) if(c.layer(i).name===n) return c.layer(i); return null; }
function findComp(){ var a=app.project.activeItem,i,it;
  if(CONFIG.targetCompName){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&it.name===CONFIG.targetCompName)return it;}}
  if(CONFIG.placeRef){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&findLayer(it,CONFIG.placeRef))return it;}}
  if(a&&a instanceof CompItem)return a; var b=null;for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&(!b||it.numLayers>b.numLayers))b=it;} return b; }
function blendEnum(s){ s=String(s||"").toLowerCase(); return s==="add"?BlendingMode.ADD:s==="screen"?BlendingMode.SCREEN:s==="multiply"?BlendingMode.MULTIPLY:s==="overlay"?BlendingMode.OVERLAY:s==="normal"?BlendingMode.NORMAL:null; }
function customize(comp,layers){ var i,L;
  if(CONFIG.place){var m=CONFIG.place,ref=null;if(m.indexOf(":")>-1){ref=findLayer(comp,m.split(":")[1]);m=m.split(":")[0];}
    for(i=0;i<layers.length;i++){L=layers[i];if(m==="above"&&ref)L.moveBefore(ref);else if(m==="below"&&ref)L.moveAfter(ref);else if(m==="top")L.moveToBeginning();else if(m==="bottom")L.moveToEnd();}}
  if(CONFIG.startTime!=null){ var earliest=null;for(i=0;i<layers.length;i++)earliest=(earliest==null)?layers[i].inPoint:Math.min(earliest,layers[i].inPoint);
    var delta=CONFIG.startTime-earliest; for(i=0;i<layers.length;i++)layers[i].startTime=layers[i].startTime+delta; } // только startTime — in/out едут сами
  if(CONFIG.duration!=null){ var base=(CONFIG.startTime!=null?CONFIG.startTime:layers[0].inPoint),we=base+CONFIG.duration;
    for(i=0;i<layers.length;i++){L=layers[i];if(L.inPoint<base)L.inPoint=base;if(L.outPoint>we)L.outPoint=we;} }
  for(i=0;i<layers.length;i++){L=layers[i];
    if(CONFIG.opacity!=null){try{L.property("ADBE Transform Group").property("ADBE Opacity").setValue(CONFIG.opacity);}catch(e){}}
    var bm=blendEnum(CONFIG.blend);if(bm!=null){try{L.blendingMode=bm;}catch(e){}}}
}
(function(){
  if(!app.project){log("нет проекта");return;} var comp=findComp(); if(!comp){log("нет компа");return;}
  var aep=new File(AEP_PATH); if(!aep.exists){log("нет .aep: "+AEP_PATH);return;}
  app.beginUndoGroup("pixel grain");
  try{
    var before={},i,it; for(i=1;i<=app.project.numItems;i++)before[app.project.item(i).id]=true;
    app.project.importFile(new ImportOptions(aep));
    var newItems=[],src=null;
    for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(before[it.id])continue;newItems.push(it);
      if(!src&&(it instanceof CompItem)){for(var l=1;l<=it.numLayers;l++)if(it.layer(l).adjustmentLayer){src=it;break;}}}
    if(!src){log("в .aep нет слоя с эффектом");app.endUndoGroup();return;}
    var copied=[]; for(var li=src.numLayers;li>=1;li--){var L=src.layer(li);if(L.adjustmentLayer){L.copyToComp(comp);copied.push(comp.layer(1));}}
    if(copied.length)customize(comp,copied);
    try{var fld=app.project.items.addFolder(SRC_FOLDER);for(var k=0;k<newItems.length;k++)try{newItems[k].parentFolder=fld;}catch(e){}}catch(e){}
    log("pixel grain: скопировано "+copied.length+" -> "+comp.name);
  }catch(err){log("err "+err+" "+(err.line||""));} finally{app.endUndoGroup();}
})();
