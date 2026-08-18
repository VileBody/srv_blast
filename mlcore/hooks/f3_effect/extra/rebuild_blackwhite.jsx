/*** blackwhite — грейд Sapphire + глитч-оверлеи (дамп ш3 20260817) ***/
/* Грейд: S_HueSatBright(Sat 0, Bright .5, Offset Darks -.5) — тот же приём, что
   в ш3 на слое «ЦК на видос»: обесцвечивает и вжигает чёрные, вместо плоского
   ADBE Black&White. Sapphire на ноде есть (его же тянет hook_light).
   Глитчи: клипы из CONFIG.clips тайлятся по окну эффекта в seeded-random
   порядке, blend 5220 + тот же грейд (Bright .75). Нет клипов => только грейд. */
var CONFIG = { targetCompName:null, placeRef:"Текст", startTime:null, duration:null, place:"below:Текст", clips:null, seed:"blackwhite" };
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){ var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; } }
function log(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }
function findLayer(c,n){ for(var i=1;i<=c.numLayers;i++) if(c.layer(i).name===n) return c.layer(i); return null; }
function findComp(){ var a=app.project.activeItem,i,it; if(CONFIG.targetCompName){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&it.name===CONFIG.targetCompName)return it;}} if(CONFIG.placeRef){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&findLayer(it,CONFIG.placeRef))return it;}} if(a&&a instanceof CompItem)return a; return null; }
function place(comp,L){ var ref=findLayer(comp,CONFIG.placeRef); if(ref)try{L.moveAfter(ref);}catch(e){} var t=(CONFIG.startTime!=null)?CONFIG.startTime:0; L.startTime=0; L.inPoint=t; L.outPoint=(CONFIG.duration!=null)?Math.min(comp.duration,t+CONFIG.duration):comp.duration; }
function setP(e,n,v){ try{var p=e.property(n);if(p)p.setValue(v);}catch(x){} }

/* Грейд ш3: Saturation / Brightness / Offset Darks у S_HueSatBright. */
function addGrade(L, bright){
  var fx=L.property("ADBE Effect Parade");
  var g=fx.addProperty("S_HueSatBright");
  if(!g) throw new Error("blackwhite: Sapphire S_HueSatBright unavailable");
  setP(g,"S_HueSatBright-0052",0);        // Saturation -> ЧБ
  setP(g,"S_HueSatBright-0053",bright);   // Brightness
  setP(g,"S_HueSatBright-0055",-0.5);     // Offset Darks -> вжечь чёрные
  return g;
}

/* mulberry32 от строкового сида — детерминированный порядок глитчей.
   ExtendScript = ES3: Math.imul нет, иначе весь блок падает в headless aerender. */
function imul32(a,b){ a=a>>>0; b=b>>>0;
  var ah=(a>>>16)&0xffff, al=a&0xffff, bh=(b>>>16)&0xffff, bl=b&0xffff;
  return ((al*bl) + ((((ah*bl + al*bh) & 0xffff) << 16) >>> 0)) | 0; }
function rngFrom(seed){
  var s=0, str=String(seed);
  for (var i=0;i<str.length;i++){ s=((s*31 + str.charCodeAt(i))>>>0); }
  s=(s>>>0)||1;
  return function(){ s=(s+0x6D2B79F5)>>>0; var t=s; t=imul32(t^(t>>>15), t|1); t^=t+imul32(t^(t>>>7), t|61); return ((t^(t>>>14))>>>0)/4294967296; };
}
function shuffled(arr, rnd){ var a=arr.slice(0), i, j, t; for(i=a.length-1;i>0;i--){ j=Math.floor(rnd()*(i+1)); t=a[i]; a[i]=a[j]; a[j]=t; } return a; }

/* Импорт с дедупом: один и тот же файл переиспользуем как FootageItem. */
function importOnce(path){
  var f=new File(path); if(!f.exists) return null;
  var i, it;
  for(i=1;i<=app.project.numItems;i++){ it=app.project.item(i);
    if(it instanceof FootageItem && it.mainSource && it.mainSource.file){
      try{ if(String(it.mainSource.file.fsName)===String(f.fsName)) return it; }catch(e){}
    } }
  try{ var io=new ImportOptions(f); return app.project.importFile(io); }catch(e2){ return null; }
}

/* Глитч-клип 1920x1080 в вертикальный комп: скейл по бОльшей стороне (cover). */
function coverScale(comp, item){
  var w=item.width||comp.width, h=item.height||comp.height;
  var k=Math.max(comp.width/w, comp.height/h)*100;
  return [k,k,100];
}

function addGlitches(comp, anchorLayer, t0, t1){
  var paths=CONFIG.clips; if(!paths || !paths.length) return 0;
  var items=[], i;
  for(i=0;i<paths.length;i++){ var it=importOnce(paths[i]); if(it && it.duration>0) items.push(it); }
  if(!items.length){ log("blackwhite: no glitch clips resolved"); return 0; }
  var rnd=rngFrom(CONFIG.seed), order=shuffled(items,rnd), oi=0, cursor=t0, made=0;
  var guard=0;
  while(cursor < t1-comp.frameDuration && guard++ < 400){
    if(oi>=order.length){ order=shuffled(items,rnd); oi=0; }
    var src=order[oi++];
    var L=comp.layers.add(src);
    L.startTime=cursor;
    L.inPoint=cursor;
    L.outPoint=Math.min(t1, cursor+src.duration);
    try{ L.audioEnabled=false; }catch(eA){}
    setP(L.property("ADBE Transform Group"),"ADBE Scale",coverScale(comp,src));
    addGrade(L,0.75);
    try{ L.blendingMode=5220; }catch(eB){}   // сырой код енама из дампа ш3
    try{ L.moveBefore(anchorLayer); }catch(eM){}
    cursor=L.outPoint; made++;
  }
  return made;
}

(function(){ if(!app.project){return;} var comp=findComp(); if(!comp){throw new Error("blackwhite: target comp not found");}
  app.beginUndoGroup("blackwhite");
  try{
    var L=comp.layers.addSolid([1,1,1],"blackwhite",comp.width,comp.height,1); L.adjustmentLayer=true;
    addGrade(L,0.5);
    place(comp,L);
    var n=addGlitches(comp, L, L.inPoint, L.outPoint);
    log("blackwhite -> "+comp.name+" (glitch tiles: "+n+")");
  }finally{app.endUndoGroup();}
})();
