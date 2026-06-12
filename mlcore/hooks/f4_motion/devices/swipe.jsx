/**********************************************************************
 * F4 motion-hook overlay — device "swipe"
 * --------------------------------------------------------------------
 * Injectable form of rebuild_swipe.jsx. Builds the swipe overlay layers
 * on top of the passed comp (MAIN_COMP). Authored from comp-time 0; the
 * clip window is pre-reframed so the cover-layer end lands on the hook.
 *
 * Tokens substituted by mlcore.hooks.f4_motion.overlay.build_overlay_jsx:
 *   __F4_BPM__     numeric BPM literal (drives in-tempo keyframes)
 *   __F4_DEVICE__  device id string (logging only)
 *
 * NOTE: layer LENGTH is fixed (not bpm-scaled). Only the keyframes inside
 * shapes reflow to the beat via TS = refBpm/bpm. S_BlurMotion is Sapphire;
 * the render node carries Sapphire (no native fallback by design).
 **********************************************************************/
(function (comp) {
  if (!comp) { try { $.writeln("[F4][__F4_DEVICE__] no comp"); } catch(_){} return; }

  var CONFIG = {
    textRelease: "СВАЙП!",
    textHold:    "СВАЙПАЙ \rНАД ЭКРАНОМ\rВ ТАКТ",

    bpm:    __F4_BPM__,   // measured BPM (injected)
    refBpm: 128,          // BPM the dump keyframes were authored under
    dropdownIdx: 1,
    tapHalveAboveBpm: 120,

    blurMochaOpacity: 0.1,
    blurCenterXY:     [701.242630004883, 1374.62132263184],
    blurToShiftY:     100,
    blurBrightness:   1.17,

    fingerStroke:        [1,1,1,1],
    fingerStrokeW:       12,
    fingerStrokeOpacity: 82
  };

  var TS = CONFIG.refBpm / CONFIG.bpm;
  var TAP_DIV = (CONFIG.bpm > CONFIG.tapHalveAboveBpm) ? 2 : 1;

  var TOFF = __F4_TOFF__;  // drop-anchor offset (overlay.py)
  function t(x){ return x * TS + TOFF; }
  function beat(n){ return n * 60 / CONFIG.bpm; }

  function setKeys(prop, keys, ease){
    if (ease === undefined) ease = true;
    var i;
    for (i = 0; i < keys.length; i++) prop.setValueAtTime(t(keys[i].time), keys[i].val);
    if (!prop.numKeys) return;
    var dim = 1;
    var v = keys[0].val;
    if (v instanceof Array) dim = v.length;
    for (i = 1; i <= prop.numKeys; i++){
      try {
        prop.setInterpolationTypeAtKey(i,
          KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);
      } catch(e){}
      if (ease){
        try {
          var inE = [], outE = [];
          for (var d = 0; d < dim; d++){ inE.push(new KeyframeEase(0, 33.333)); outE.push(new KeyframeEase(0, 33.333)); }
          prop.setTemporalEaseAtKey(i, inE, outE);
        } catch(e){}
      }
    }
  }

  function setConst(prop, val){ try { prop.setValue(val); } catch(e){} }
  function setEffParam(eff, mn, val){ try { eff.property(mn).setValue(val); } catch(e){} }
  function setEffKeys(eff, mn, keys, ease){ try { setKeys(eff.property(mn), keys, ease); } catch(e){} }

  function styleText(td, txt){
    try { td.resetCharStyle(); } catch(e){}  // сброс наследования Character-панели ноды (иначе sticky-дефолт)
    td.text = txt;
    try { td.font = "TimesNewRomanPS-ItalicMT"; } catch(e){}
    td.fontSize = 72;
    td.applyFill = true;
    td.fillColor = [0.92157,0.92157,0.92157];
    td.applyStroke = false;
    try { td.justification = ParagraphJustification.CENTER_JUSTIFY; } catch(e){}
    try { td.tracking = -60; } catch(e){}
    try { td.autoLeading = false; td.leading = 140; } catch(e){}
    try { td.verticalScale = 240; } catch(e){}  // doc-level fallback (flaky headless)
    // Надёжный канал верт.масштаба = characterRange (как fontSize у трека):
    // doc-level td.verticalScale в headless aerender дропается на setValue,
    // глиф наследует sticky verticalScale Character-панели ноды (наблюдалось 400%).
    try { var __vr = td.characterRange(0, (txt && txt.length) ? txt.length : 1); __vr.verticalScale = 240; } catch(e){}
    return td;
  }

  // (низ) Сплошная заливка Черный 1 — "накрывающий" слой, его outPoint = hook
  function buildBlackSolid(comp){
    var L = comp.layers.addSolid([0,0,0], "Сплошная заливка Черный 1",
                                 comp.width, comp.height, 1);
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(4.3043043043043);
    var tr = L.property("ADBE Transform Group");
    // Cover must span the WHOLE comp ("Comp 1" is 1080x1960, not 1920 — the
    // hardcoded [540,960] left a ~20px gap at the bottom). Center on the actual
    // comp dims + 10% over-scale so reframe/rounding never exposes footage.
    var __cx = comp.width/2, __cy = comp.height/2;
    setConst(tr.property("ADBE Anchor Point"), [__cx,__cy,0]);
    setConst(tr.property("ADBE Position"),     [__cx,__cy,0]);
    setConst(tr.property("ADBE Scale"),        [110,110,100]);
    setConst(tr.property("ADBE Opacity"),      96);
    return L;
  }

  function buildBpmControl(comp){
    var L = comp.layers.addNull(comp.duration);
    L.name = "bpm control";
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(5.30530530530531);
    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Anchor Point"), [0,0,0]);
    setConst(tr.property("ADBE Position"),     [213.33332824707,1460,0]);
    setConst(tr.property("ADBE Opacity"),      0);
    var fx = L.property("ADBE Effect Parade");
    try {
      var dd = fx.addProperty("ADBE Dropdown Control");
      try { dd.property(1).setValue(CONFIG.dropdownIdx); } catch(e){}
    } catch(e){}
    try {
      var sl = fx.addProperty("ADBE Slider Control");
      sl.name = "ползунок";
      sl.property("ADBE Slider Control-0001").setValue(CONFIG.bpm);
    } catch(e){}
    return L;
  }

  function buildHoldText(comp){
    var L = comp.layers.addText(CONFIG.textHold);
    L.name = "СВАЙПАЙ  НАД ЭКРАНОМ В ТАКТ";
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(5.30530530530531);

    var srcProp = L.property("ADBE Text Properties").property("ADBE Text Document");
    var td = srcProp.value; styleText(td, CONFIG.textHold); srcProp.setValue(td);

    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Position"), [540,325.000002235174,0]);
    setKeys(tr.property("ADBE Opacity"), [
      {time:3.30328, val:100},
      {time:3.56855, val:0}
    ]);

    var animers = L.property("ADBE Text Properties").property("ADBE Text Animators");
    var anim = animers.addProperty("ADBE Text Animator");
    try { anim.property("ADBE Text Animator Properties")
              .addProperty("ADBE Text Opacity").setValue(0); } catch(e){}
    var sel = anim.property("ADBE Text Selectors").addProperty("ADBE Text Selector");
    try {
      sel.property("ADBE Text Range Advanced")
         .property("ADBE Text Range Type2").setValue(3); // Слова
    } catch(e){
      try { sel.property("ADBE Text Range Type2").setValue(3); } catch(e2){}
    }
    setKeys(sel.property("ADBE Text Percent Start"), [
      {time:0,       val:0},
      {time:0.86754, val:100}
    ]);

    var fx = L.property("ADBE Effect Parade");
    var mm = fx.addProperty("ADBE Minimax");
    setEffParam(mm, "ADBE Minimax-0001", 2);
    setEffParam(mm, "ADBE Minimax-0003", 2);
    setEffKeys (mm, "ADBE Minimax-0002", [
      {time:0,       val:15},
      {time:0.17684, val:0}
    ]);
    return L;
  }

  function buildReleaseText(comp){
    var L = comp.layers.addText(CONFIG.textRelease);
    L.name = "СВАЙП!";
    L.startTime = 0; L.inPoint = t(3.53687020353687); L.outPoint = t(5.30530530530531);

    var srcProp = L.property("ADBE Text Properties").property("ADBE Text Document");
    var td = srcProp.value; styleText(td, CONFIG.textRelease); srcProp.setValue(td);

    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Position"), [540,325.000002235174,0]);
    setKeys(tr.property("ADBE Opacity"), [
      {time:3.97896, val:100},
      {time:4.24423, val:0}
    ]);

    var fx = L.property("ADBE Effect Parade");
    var mm = fx.addProperty("ADBE Minimax");
    setEffParam(mm, "ADBE Minimax-0001", 2);
    setEffParam(mm, "ADBE Minimax-0003", 2);
    setEffParam(mm, "ADBE Minimax-0004", 1);
    setEffKeys (mm, "ADBE Minimax-0002", [
      {time:0,       val:15},
      {time:0.17684, val:0},
      {time:3.53685, val:15},
      {time:3.60358, val:0}
    ]);
    return L;
  }

  function makeShape(verts, inT, outT){
    var s = new Shape();
    s.vertices    = verts;
    s.inTangents  = inT;
    s.outTangents = outT;
    s.closed = true;
    return s;
  }

  // Контур "Фигура 4" (2 точки)
  var F4_A = makeShape(
    [[-249.5478515625,37.6560821533203],[-167.418090820312,120.499481201172]],
    [[56.8590087890625,-16.9452362060547],[-69.4891052246094,5.71292114257812]],
    [[-53.449462890625,15.9291229248047],[91.6060943603516,-7.53123474121094]]);
  var F4_B = makeShape(
    [[-110.559188842773,-58.3668975830078],[-28.4294281005859,24.4765014648438]],
    [[56.8590087890625,-16.9452362060547],[-69.4891052246094,5.71292114257812]],
    [[-53.4495086669922,15.9291229248047],[91.6060943603516,-7.53123474121094]]);

  // Контур "Фигура 2" (5 точек)
  var F2_A = makeShape(
    [[-296.857666015625,2.86114501953125],[-224.581573486328,169.513671875],
     [5.98086547851562,302.553100585938],[247.045379638672,283.298706054688],
     [-8.30148315429688,111.611434936523]],
    [[90.6622924804688,-18.9584045410156],[-58.8351135253906,-70.9679412841797],
     [0,0],[71.467529296875,111.017700195312],[70.606689453125,55.9865264892578]],
    [[-77.8576965332031,16.2808380126953],[81.0018005371094,97.7057495117188],
     [0,0],[-71.4671630859375,-111.018005371094],[-68.210205078125,-54.0866851806641]]);
  var F2_B = makeShape(
    [[-157.688049316406,-101.999786376953],[-126.910034179688,57.1970672607422],
     [-3.65692138671875,278.458984375],[237.407928466797,259.204528808594],
     [108.75634765625,-6.26445007324219]],
    [[90.6623077392578,-18.9583740234375],[-58.8351440429688,-70.9679107666016],
     [0,0],[71.467529296875,111.017761230469],[70.6068725585938,55.9865875244141]],
    [[-77.8577575683594,16.2808227539062],[81.0019836425781,97.7055511474609],
     [0,0],[-71.4671630859375,-111.017883300781],[-68.2102661132812,-54.0867004394531]]);

  var F4_drop = makeShape(
    [[-53.3389739990234,-98.7483673095703],[-27.9621124267578,-13.4865112304688]],
    [[56.8590087890625,-16.9452362060547],[-69.4891204833984,5.71292114257812]],
    [[-53.4494934082031,15.9291229248047],[91.6060791015625,-7.53123474121094]]);
  var F2_drop = makeShape(
    [[-51.2278442382812,-166.191955566406],[-126.910049438477,57.1970367431641],
     [-3.65692138671875,278.458679199219],[237.408020019531,259.20458984375],
     [108.756591796875,-6.26443481445312]],
    [[90.6622619628906,-18.9583740234375],[-58.8351287841797,-70.9678344726562],
     [0,0],[71.4672241210938,111.018249511719],[70.606689453125,55.9865570068359]],
    [[-77.8577575683594,16.2808227539062],[81.0019836425781,97.7059173583984],
     [0,0],[-71.4671630859375,-111.017883300781],[-68.2105560302734,-54.0867004394531]]);

  var FINGER_STEP     = 0.23356690023357;
  var FINGER_ALT_KEYS = 17;
  var FINGER_DROP     = 4.27093760427094;

  function buildContour(root, name, A, B, drop, grpScale){
    var grp = root.addProperty("ADBE Vector Group"); grp.name = name;
    var cont = grp.property("ADBE Vectors Group");
    var pg = cont.addProperty("ADBE Vector Shape - Group"); pg.name = "Контур 1";
    var path = pg.property("ADBE Vector Shape");

    var step  = FINGER_STEP * TAP_DIV;
    var nAlt  = Math.round((FINGER_STEP * (FINGER_ALT_KEYS - 1)) / step) + 1;
    for (var k = 0; k < nAlt; k++){
      path.setValueAtTime(t(k * step), (k % 2 === 0) ? A : B);
    }
    path.setValueAtTime(t(FINGER_DROP), drop);
    for (var i = 1; i <= path.numKeys; i++){
      try { path.setInterpolationTypeAtKey(i,
        KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER); } catch(e){}
    }

    var st = cont.addProperty("ADBE Vector Graphic - Stroke");
    setConst(st.property("ADBE Vector Stroke Color"),   CONFIG.fingerStroke);
    setConst(st.property("ADBE Vector Stroke Width"),   CONFIG.fingerStrokeW);
    setConst(st.property("ADBE Vector Stroke Opacity"), CONFIG.fingerStrokeOpacity);

    if (grpScale){
      try { grp.property("ADBE Vector Transform Group")
               .property("ADBE Vector Scale").setValue(grpScale); } catch(e){}
    }
    return grp;
  }

  function buildFinger(comp){
    var L = comp.layers.addShape();
    L.name = "finger";
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(4.3043043043043);

    var root = L.property("ADBE Root Vectors Group");
    buildContour(root, "Фигура 4", F4_A, F4_B, F4_drop, null);
    buildContour(root, "Фигура 2", F2_A, F2_B, F2_drop, [100, 99.2805755395683]);

    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Anchor Point"), [0,0,0]);
    setConst(tr.property("ADBE Position"),     [917,1359,0]);
    setConst(tr.property("ADBE Scale"),        [117.524187306575,197.173651553725,100]);
    setKeys(tr.property("ADBE Opacity"), [
      {time:4.03737, val:100},
      {time:4.27094, val:0}
    ]);

    var fx = L.property("ADBE Effect Parade");
    try {
      var b = fx.addProperty("S_BlurMotion");
      setEffParam(b, "S_BlurMotion-0522", CONFIG.blurMochaOpacity);
      setEffParam(b, "S_BlurMotion-0050", CONFIG.blurCenterXY);
      setEffParam(b, "S_BlurMotion-0058", CONFIG.blurToShiftY);
      setEffParam(b, "S_BlurMotion-0060", CONFIG.blurBrightness);
    } catch(e){}
    return L;
  }

  function buildFlashAdjustment(comp){
    var L = comp.layers.addSolid([1,1,1], "Корректирующий слой 2",
                                 comp.width, comp.height, 1);
    L.adjustmentLayer = true;
    L.startTime = 0; L.inPoint = t(4.2042042042042); L.outPoint = t(4.37103770437104);

    var fx = L.property("ADBE Effect Parade");
    var mm = fx.addProperty("ADBE Minimax");
    setEffParam(mm, "ADBE Minimax-0001", 2);
    setEffParam(mm, "ADBE Minimax-0002", 50);
    setEffParam(mm, "ADBE Minimax-0003", 1);
    setEffParam(mm, "ADBE Minimax-0004", 3);
    var lk = fx.addProperty("ADBE Luma Key");
    setEffParam(lk, "ADBE Luma Key-0001", 2);
    setEffParam(lk, "ADBE Luma Key-0002", 31);
    return L;
  }

  app.beginUndoGroup("F4 swipe overlay");
  try {
    // bottom-up: each new layer goes to index 1, so the whole F4 stack ends up
    // on top of the existing footage/text layers.
    buildBlackSolid(comp);
    buildBpmControl(comp);
    buildHoldText(comp);
    buildReleaseText(comp);
    buildFinger(comp);
    // buildFlashAdjustment removed — drop flash = F3 hook_light (added by overlay.py)
    try { $.writeln("[F4][__F4_DEVICE__] overlay built bpm=" + CONFIG.bpm); } catch(_){}
  } catch(err){
    try { $.writeln("[F4][__F4_DEVICE__] ERROR " + err.toString()); } catch(_){}
  } finally {
    app.endUndoGroup();
  }
})(MAIN_COMP);
