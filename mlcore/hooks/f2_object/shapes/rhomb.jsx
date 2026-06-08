/*** rebuild_shape_rhomb.jsx — пара "rhomb" + "rhomblayer" с эффектами minimax flash + snap wipe.
 * Авто-каркас (headless): без alert, без кликов. Слои:
 *   1. minimax flash (adjustment, поверх)         — вспышка/scatter/exposure
 *   2. snap wipe     (adjustment, ниже flash)     — горизонтальный смаз-вайп
 *   3. rhomblayer    (увеличенная копия фигуры)   — появляется через 0.267с
 *   4. rhomb         (базовая фигура)             — стартует на t0
 * Тайминги фиксированы из дампа (0/0.267/0.434/0.500). Якорь:
 *   - CONFIG.startTime  → tBase = startTime
 *   - CONFIG.dropTime   → tBase = dropTime - 0.434 (вспышки на дропе)
 *   - иначе              → tBase = 0
 ***/
var CONFIG = {
  targetCompName:null, placeRef:null,
  dropTime:null, startTime:null,
  shapeCenter:null,           // [x,y,z] поз. фигуры в компе; null => дамповая позиция
  opacity:null, blend:null
};
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){
  var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; }
}

// ───────────────── SHAPE DATA (только эта секция отличается между 5 скриптами) ─────────────────
var SHAPE = {
  name: "rhomb",
  pathType: "polygon",                   // "star" | "polygon" | "ellipse" | "rect"
  starPoints: 4, starInner: 150.450845096076, starOuter: 300.901690192152,
  ellipseSize: null, rectSize: null,
  fill: [1, 1, 1, 1],
  strokeWidth: null,                     // без обводки
  vectorPos: [-271.555555555555, -291.252644856771],
  vectorScaleBase: [70, 70],
  vectorScaleLayer: [140, 140],
  layerPosDefault: [811.555555555555, 1279.98620351156, 0]
};
// ──────────────────────────────────────────────────────────────────────────────────────────────

// тайминги анимации (фикс. из дампа)
var T_LAYER_OFFSET = 0.26693360026693;
var T_FX_OFFSET    = 0.43376710043377;
var T_END_OFFSET   = 0.5005005005005;
var T_FX_DUR       = T_END_OFFSET - T_FX_OFFSET;   // ~0.067с
var INVERT_KEYS = [[0,100],[0.13346680013347,0],[0.3003003003003,100],[0.46713380046713,0]];
var BZ = KeyframeInterpolationType.BEZIER;

function log(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }
function setP(e,m,v){ try{e.property(m).setValue(v);}catch(x){} }
function findLayer(c,n){ for(var i=1;i<=c.numLayers;i++) if(c.layer(i).name===n) return c.layer(i); return null; }
function findComp(){
  var a=app.project.activeItem,i,it;
  if(CONFIG.targetCompName){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&it.name===CONFIG.targetCompName)return it;}}
  if(CONFIG.placeRef){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&findLayer(it,CONFIG.placeRef))return it;}}
  if(a&&a instanceof CompItem)return a;
  var b=null; for(i=1;i<=app.project.numItems;i++){it=app.project.item(i); if(it instanceof CompItem&&(!b||it.numLayers>b.numLayers))b=it;} return b;
}
function blendEnum(s){ s=String(s||"").toLowerCase(); return s==="add"?BlendingMode.ADD:s==="screen"?BlendingMode.SCREEN:s==="multiply"?BlendingMode.MULTIPLY:s==="overlay"?BlendingMode.OVERLAY:s==="normal"?BlendingMode.NORMAL:null; }
function bezAll(p){ for(var k=1;k<=p.numKeys;k++){ try{p.setInterpolationTypeAtKey(k,BZ,BZ);}catch(e){} } }

function addShapeContent(contents){
  var t=SHAPE.pathType, p;
  if (t==="star" || t==="polygon"){
    p = contents.addProperty("ADBE Vector Shape - Star");
    if (t==="polygon") setP(p,"ADBE Vector Star Type",2);
    if (SHAPE.starPoints!=null) setP(p,"ADBE Vector Star Points",SHAPE.starPoints);
    if (SHAPE.starInner!=null) setP(p,"ADBE Vector Star Inner Radius",SHAPE.starInner);
    if (SHAPE.starOuter!=null) setP(p,"ADBE Vector Star Outer Radius",SHAPE.starOuter);
  } else if (t==="ellipse"){
    p = contents.addProperty("ADBE Vector Shape - Ellipse");
    if (SHAPE.ellipseSize) setP(p,"ADBE Vector Ellipse Size",SHAPE.ellipseSize);
  } else if (t==="rect"){
    p = contents.addProperty("ADBE Vector Shape - Rect");
    if (SHAPE.rectSize) setP(p,"ADBE Vector Rect Size",SHAPE.rectSize);
  }
  if (SHAPE.strokeWidth!=null){
    var s = contents.addProperty("ADBE Vector Graphic - Stroke");
    setP(s,"ADBE Vector Stroke Width",SHAPE.strokeWidth);
    try{ s.property("ADBE Vector Stroke Color").setValue([SHAPE.fill[0],SHAPE.fill[1],SHAPE.fill[2],1]); }catch(e){}
  }
  var f = contents.addProperty("ADBE Vector Graphic - Fill");
  setP(f,"ADBE Vector Fill Color",SHAPE.fill);
}

function addInvertExtract(L, tBase){
  var fx = L.property("ADBE Effect Parade");
  var inv = fx.addProperty("ADBE Invert");
  setP(inv,"ADBE Invert-0001",12);
  var mix = inv.property("ADBE Invert-0002");
  for (var k=0;k<INVERT_KEYS.length;k++) mix.setValueAtTime(tBase+INVERT_KEYS[k][0], INVERT_KEYS[k][1]);
  bezAll(mix);
  var ext = fx.addProperty("ADBE Extract");
  setP(ext,"ADBE Extract-0003",45);
  setP(ext,"ADBE Extract-0004",125);
  setP(ext,"ADBE Extract-0006",100);
}

function buildShape(comp, tBase, name, scaleVec, t_in){
  var L = comp.layers.addShape();
  L.name = name;
  var grp = L.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
  var contents = grp.property("ADBE Vectors Group");
  addShapeContent(contents);
  var vTr = grp.property("ADBE Vector Transform Group");
  if (SHAPE.vectorPos) setP(vTr,"ADBE Vector Position",SHAPE.vectorPos);
  if (scaleVec) setP(vTr,"ADBE Vector Scale",scaleVec);
  var pos = CONFIG.shapeCenter || SHAPE.layerPosDefault || [comp.width/2,comp.height/2,0];
  L.property("ADBE Transform Group").property("ADBE Position").setValue(pos);
  L.startTime=0; L.inPoint=t_in; L.outPoint=tBase+T_END_OFFSET;
  addInvertExtract(L, tBase);
  if (CONFIG.opacity!=null){ try{ L.property("ADBE Transform Group").property("ADBE Opacity").setValue(CONFIG.opacity); }catch(e){} }
  var bm = blendEnum(CONFIG.blend); if (bm!=null){ try{ L.blendingMode=bm; }catch(e){} }
  return L;
}

function buildMinimaxFlash(comp, t0, dur){
  var L = comp.layers.addSolid([1,1,1],"minimax flash",comp.width,comp.height,1);
  L.adjustmentLayer=true; L.startTime=0; L.inPoint=t0; L.outPoint=t0+dur;
  var fx = L.property("ADBE Effect Parade");
  var mm = fx.addProperty("ADBE Minimax"); setP(mm,"ADBE Minimax-0002",50); setP(mm,"ADBE Minimax-0004",2);
  var lk = fx.addProperty("ADBE Luma Key"); setP(lk,"ADBE Luma Key-0002",100);
  var sc = fx.addProperty("ADBE Scatter"); setP(sc,"ADBE Scatter-0001",20);
  var ex = fx.addProperty("ADBE Exposure2"); setP(ex,"ADBE Exposure2-0003",0.8);
  return L;
}

function buildSnapWipe(comp, t0, dur){
  var L = comp.layers.addSolid([1,1,1],"snap wipe",comp.width,comp.height,1);
  L.adjustmentLayer=true; L.startTime=0; L.inPoint=t0; L.outPoint=t0+dur;
  var fx = L.property("ADBE Effect Parade");
  var cx = comp.width/2, cy = comp.height/2;
  var geo = fx.addProperty("ADBE Geometry2");
  var ap = geo.property("ADBE Geometry2-0001");
  ap.setValueAtTime(t0,           [cx,     cy]);
  ap.setValueAtTime(t0+dur*0.5,   [cx+140, cy]);
  ap.setValueAtTime(t0+dur,       [cx+340, cy]);
  bezAll(ap);
  var db = fx.addProperty("ADBE Motion Blur"); setP(db,"ADBE Motion Blur-0001",90); setP(db,"ADBE Motion Blur-0002",100);
  var mm = fx.addProperty("ADBE Minimax"); setP(mm,"ADBE Minimax-0002",165); setP(mm,"ADBE Minimax-0004",2);
  var op = fx.addProperty("ADBE Optics Compensation"); setP(op,"ADBE Optics Compensation-0001",120); setP(op,"ADBE Optics Compensation-0002",1);
  return L;
}

(function(){
  if (!app.project){ log("нет проекта"); return; }
  var comp = findComp(); if (!comp){ log("нет компа"); return; }
  var tBase;
  if (CONFIG.startTime!=null) tBase = CONFIG.startTime;
  else if (CONFIG.dropTime!=null) tBase = CONFIG.dropTime - T_FX_OFFSET;
  else tBase = 0;
  var tLayer = tBase + T_LAYER_OFFSET;
  var tFx    = tBase + T_FX_OFFSET;
  app.beginUndoGroup("rebuild shape " + SHAPE.name);
  try {
    var Lb = buildShape(comp, tBase, SHAPE.name,            SHAPE.vectorScaleBase,  tBase);
    var Ll = buildShape(comp, tBase, SHAPE.name + "layer",  SHAPE.vectorScaleLayer, tLayer);
    var Lw = buildSnapWipe(comp, tFx, T_FX_DUR);
    var Lm = buildMinimaxFlash(comp, tFx, T_FX_DUR);
    // порядок: minimax flash (top) → snap wipe → layer → base (bottom)
    Lb.moveToEnd(); Ll.moveBefore(Lb); Lw.moveBefore(Ll); Lm.moveBefore(Lw);
    if (CONFIG.placeRef){
      var ref = findLayer(comp, CONFIG.placeRef);
      if (ref){ Lm.moveAfter(ref); Lw.moveAfter(Lm); Ll.moveAfter(Lw); Lb.moveAfter(Ll); }
    }
    log("rebuild_shape_" + SHAPE.name + ": ok → " + comp.name + " @ tBase=" + tBase.toFixed(3));
  } catch (e){ log("err: " + e + " (стр " + (e.line||"?") + ")"); }
  finally { app.endUndoGroup(); }
})();
