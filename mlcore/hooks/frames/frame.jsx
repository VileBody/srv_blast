/*** frame — PNG-рамка поверх ВСЕХ слоёв компа ***/
/* Рамка = чёрная маска 1080x1920 с прозрачным окном: сверху над субтитрами и
   футажом, на всю длину компа. Скейлим по большей стороне (cover), потому что
   рендер-комп не обязан совпадать с 1080x1920 (сейчас 1080x1960). */
var CONFIG = { targetCompName:null, framePath:null, opacity:100 };
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){ var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; } }
function log(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }
function findComp(){ var a=app.project.activeItem,i,it; if(CONFIG.targetCompName){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&it.name===CONFIG.targetCompName)return it;}} if(a&&a instanceof CompItem)return a; return null; }
function setP(e,n,v){ try{var p=e.property(n);if(p)p.setValue(v);}catch(x){} }
function importOnce(path){
  var f=new File(path); if(!f.exists) return null;
  var i, it;
  for(i=1;i<=app.project.numItems;i++){ it=app.project.item(i);
    if(it instanceof FootageItem && it.mainSource && it.mainSource.file){
      try{ if(String(it.mainSource.file.fsName)===String(f.fsName)) return it; }catch(e){}
    } }
  try{ return app.project.importFile(new ImportOptions(f)); }catch(e2){ return null; }
}
(function(){
  if(!app.project){return;}
  if(!CONFIG.framePath){ log("frame: no framePath, skip"); return; }
  var comp=findComp(); if(!comp){ throw new Error("frame: target comp not found"); }
  app.beginUndoGroup("frame");
  try{
    var item=importOnce(CONFIG.framePath);
    if(!item){ log("frame: asset missing -> "+CONFIG.framePath); return; }
    var L=comp.layers.add(item);
    L.name="frame_overlay";
    try{ L.moveToBeginning(); }catch(eM){}
    L.startTime=0; L.inPoint=0; L.outPoint=comp.duration;
    var w=item.width||comp.width, h=item.height||comp.height;
    var k=Math.max(comp.width/w, comp.height/h)*100;
    var tr=L.property("ADBE Transform Group");
    setP(tr,"ADBE Scale",[k,k,100]);
    setP(tr,"ADBE Position",[comp.width/2, comp.height/2]);
    if(CONFIG.opacity!=null && CONFIG.opacity!==100) setP(tr,"ADBE Opacity",CONFIG.opacity);
    log("frame -> "+comp.name+" ("+CONFIG.framePath+")");
  }finally{ app.endUndoGroup(); }
})();
