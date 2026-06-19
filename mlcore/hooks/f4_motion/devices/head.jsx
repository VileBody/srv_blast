/**********************************************************************
 * F4 motion-hook overlay — device "head"  ("качай головой")
 * Injectable form of rebuild_head.jsx. Layers built on the passed comp from
 * comp-time 0 (clip window pre-reframed so cover-end == hook).
 * Tokens: __F4_BPM__, __F4_DEVICE__. Internal keyframes/wiggle reflow via bpm;
 * layer length fixed. S_Blur/S_BlurMotion are Sapphire (skipped if absent).
 **********************************************************************/
(function (comp) {
  if (!comp) { try { $.writeln("[F4][__F4_DEVICE__] no comp"); } catch(_){} return; }

  var CONFIG = {
    textRelease: "КАЧАЙ!",
    textHold:    "КАЧАЙ ГОЛОВОЙ\rВ ТАКТ",
    bpm:    __F4_BPM__,
    refBpm: 128,
    dropdownIdx: 1,
    fig1Stroke:          [1,1,1,1],
    fig2Stroke:          [1,0,0,1],
    fingerStrokeW:       20,
    fingerStrokeOpacity: 100
  };

  var TS = CONFIG.refBpm / CONFIG.bpm;

  var HEAD_STEP     = 0.23356690023357;
  var HEAD_ALT_KEYS = 18;
  var HEAD_ZOOM_T0  = 3.73707040373707;
  var HEAD_ZOOM_T1  = 3.97063730397064;

  var TOFF = __F4_TOFF__;  // drop-anchor offset (overlay.py)
  function t(x){ return x * TS + TOFF; }
  function beat(n){ return n * 60 / CONFIG.bpm; }
  function setConst(prop, val){ try { prop.setValue(val); } catch(e){} }
  function setExpr(prop, e){ try { prop.expression = e; } catch(err){} }
  function setEffParam(eff, mn, val){ try { eff.property(mn).setValue(val); } catch(e){} }

  function setKeys(prop, keys, ease){
    if (ease === undefined) ease = true;
    var i;
    for (i = 0; i < keys.length; i++) prop.setValueAtTime(t(keys[i].time), keys[i].val);
    if (!prop.numKeys) return;
    var dim = (keys[0].val instanceof Array) ? keys[0].val.length : 1;
    for (i = 1; i <= prop.numKeys; i++){
      try { prop.setInterpolationTypeAtKey(i,
        KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER); } catch(e){}
      if (ease){
        try {
          var inE=[], outE=[];
          for (var d=0; d<dim; d++){ inE.push(new KeyframeEase(0,33.333)); outE.push(new KeyframeEase(0,33.333)); }
          prop.setTemporalEaseAtKey(i, inE, outE);
        } catch(e){}
      }
    }
  }
  function setEffKeys(eff, mn, keys, ease){ try { setKeys(eff.property(mn), keys, ease); } catch(e){} }

  function makeShape(verts, inT, outT, closed){
    var s = new Shape(); s.vertices = verts; s.inTangents = inT; s.outTangents = outT; s.closed = closed; return s;
  }
  function addContour(root, name, A, B, grpPos, grpScale, strokeColor){
    var grp = root.addProperty("ADBE Vector Group"); grp.name = name;
    var cont = grp.property("ADBE Vectors Group");
    var pg = cont.addProperty("ADBE Vector Shape - Group"); pg.name = "Контур 1";
    var path = pg.property("ADBE Vector Shape");

    for (var k = 0; k < HEAD_ALT_KEYS; k++) path.setValueAtTime(t(k * HEAD_STEP), (k % 2 === 0) ? A : B);
    for (var i = 1; i <= path.numKeys; i++){
      try { path.setInterpolationTypeAtKey(i,
        KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER); } catch(e){}
    }

    var st = cont.addProperty("ADBE Vector Graphic - Stroke");
    setConst(st.property("ADBE Vector Stroke Color"),   strokeColor);
    setConst(st.property("ADBE Vector Stroke Width"),   CONFIG.fingerStrokeW);
    setConst(st.property("ADBE Vector Stroke Opacity"), CONFIG.fingerStrokeOpacity);

    var gt = grp.property("ADBE Vector Transform Group");
    if (grpPos)   try { gt.property("ADBE Vector Position").setValue(grpPos); } catch(e){}
    if (grpScale) try { gt.property("ADBE Vector Scale").setValue(grpScale); } catch(e){}
    return grp;
  }
  function addBlur(L, blurKeys, mochaOpacity, fromZ){
    var fx = L.property("ADBE Effect Parade");
    try { var b = fx.addProperty("S_Blur"); setEffKeys(b, "S_Blur-0050", blurKeys, true); } catch(e){}
    try { var bm = fx.addProperty("S_BlurMotion");
          setEffParam(bm, "S_BlurMotion-0522", mochaOpacity);
          setEffParam(bm, "S_BlurMotion-0051", fromZ); } catch(e){}
  }

  var F1_inT = [[145.796905517578,0.78971862792969],[0,0],[0,0],[0,0],[0,0],[-19.5925903320312,-13.2409362792969],[0,0],[0,0],[-7.22955322265625,33.2771911621094],[0,0],[0,0]];
  var F1_outT = [[-137.320129394531,-0.74380493164062],[0,0],[0,0],[0,0],[0,0],[16.6090393066406,11.224609375],[0,0],[0,0],[7.22955322265625,-33.2771911621094],[0,0],[0,0]];
  var F1_A = makeShape(
    [[-103.027374267578,-23.9138946533203],[-266.111114501953,89.2375946044922],[-270.288146972656,161.799072265625],[-317.111083984375,244.431030273438],[-278.592620849609,242.100433349609],[-257.111114501953,340.828094482422],[-158.111145019531,347.646728515625],[-131.111145019531,442.828155517578],[83.6593322753906,374.110260009766],[62.4444885253906,264.246826171875],[84.888916015625,97.0895690917969]],
    F1_inT, F1_outT, true);
  var F1_B = makeShape(
    [[-146.413269042969,-23.9138946533203],[-315.694885253906,91.9137573242188],[-319.871948242188,153.770568847656],[-346.538391113281,244.161193847656],[-300.285522460938,242.100433349609],[-275.705017089844,340.828094482422],[-186.002105712891,347.646728515625],[-131.111175537109,442.828155517578],[83.6593322753906,374.110260009766],[28.3556823730469,266.923004150391],[38.4041442871094,99.7657470703125]],
    F1_inT, F1_outT, true);

  var F2_inT = [[0,0],[-109.714324951172,9.14285278320312]];
  var F2_outT = [[0,0],[109.714279174805,-9.14285278320312]];
  var F2_A = makeShape([[-160.571411132812,173.714294433594],[-146.857147216797,251.428558349609]], F2_inT, F2_outT, false);
  var F2_B = makeShape([[-91.9999847412109,178.285705566406],[-78.2857055664062,256]], F2_inT, F2_outT, false);

  // Верт.масштаб через text-animator Scale — надёжно в headless aerender
  // (как reveal-аниматор opacity ниже); пивот per-glyph от baseline =
  // визуально Character-панель «верт.масштаб», без сдвига слоя.
  function f4VScale(L){
    try {
      var __an = L.property("ADBE Text Properties").property("ADBE Text Animators").addProperty("ADBE Text Animator");
      try { __an.name = "vscale240"; } catch(e){}
      var __sc = __an.property("ADBE Text Animator Properties").addProperty("ADBE Text Scale 3D");
      __sc.setValue([100, 240, 100]);
    } catch(e){}
  }
  function styleText(td, txt){
    try { td.resetCharStyle(); } catch(e){}  // сброс наследования Character-панели ноды (иначе sticky-дефолт)
    td.text = txt;
    try { td.font = "TimesNewRomanPS-ItalicMT"; } catch(e){}
    td.fontSize = 72; td.applyFill = true; td.fillColor = [0.92157,0.92157,0.92157]; td.applyStroke = false;
    try { td.justification = ParagraphJustification.CENTER_JUSTIFY; } catch(e){}
    try { td.tracking = -60; } catch(e){}
    try { td.autoLeading = false; td.leading = 140; } catch(e){}
    // верт.масштаб 240% ставится через text-animator Scale (f4VScale,
    // вызывается после создания текст-слоя): ни doc-level td.verticalScale,
    // ни characterRange.verticalScale не пишутся в headless aerender
    // (наблюдалось 400%, затем 100%).
    return td;
  }

  function buildBlackSolid(comp){
    var L = comp.layers.addSolid([0,0,0], "Сплошная заливка Черный 1", comp.width, comp.height, 1);
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(4.004004004004);
    var tr = L.property("ADBE Transform Group");
    // Cover must span the WHOLE comp ("Comp 1" is 1080x1960, not 1920). Center on
    // the actual comp dims + 10% over-scale so footage never pokes out at bottom.
    var __cx = comp.width/2, __cy = comp.height/2;
    setConst(tr.property("ADBE Anchor Point"), [__cx,__cy,0]);
    setConst(tr.property("ADBE Position"),      [__cx,__cy,0]);
    setConst(tr.property("ADBE Scale"),         [110,110,100]);
    setConst(tr.property("ADBE Opacity"),  96);
    return L;
  }
  function buildBpmControl(comp){
    var L = comp.layers.addNull(comp.duration);
    L.name = "bpm control";
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(5.10510510510511);
    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Anchor Point"), [0,0,0]);
    setConst(tr.property("ADBE Position"),     [213.33332824707,1460,0]);
    setConst(tr.property("ADBE Opacity"),      0);
    var fx = L.property("ADBE Effect Parade");
    try { var dd = fx.addProperty("ADBE Dropdown Control"); try{ dd.property(1).setValue(CONFIG.dropdownIdx);}catch(e){} } catch(e){}
    try { var sl = fx.addProperty("ADBE Slider Control"); sl.name = "ползунок";
          sl.property("ADBE Slider Control-0001").setValue(CONFIG.bpm); } catch(e){}
    return L;
  }
  function buildHoldText(comp){
    var L = comp.layers.addText(CONFIG.textHold);
    L.name = "КАЧАЙ ГОЛОВОЙ В ТАКТ";
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(5.10510510510511);
    var srcProp = L.property("ADBE Text Properties").property("ADBE Text Document");
    var td = srcProp.value; styleText(td, CONFIG.textHold); srcProp.setValue(td); f4VScale(L);
    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Position"), [540,325.000002235174,0]);
    setKeys(tr.property("ADBE Opacity"), [{time:3.30328,val:100},{time:3.56855,val:0}]);
    var animers = L.property("ADBE Text Properties").property("ADBE Text Animators");
    var anim = animers.addProperty("ADBE Text Animator");
    try { anim.property("ADBE Text Animator Properties").addProperty("ADBE Text Opacity").setValue(0); } catch(e){}
    var sel = anim.property("ADBE Text Selectors").addProperty("ADBE Text Selector");
    try { sel.property("ADBE Text Range Advanced").property("ADBE Text Range Type2").setValue(3); }
    catch(e){ try { sel.property("ADBE Text Range Type2").setValue(3); } catch(e2){} }
    setKeys(sel.property("ADBE Text Percent Start"), [{time:0,val:0},{time:0.86754,val:100}]);
    var fx = L.property("ADBE Effect Parade");
    var mm = fx.addProperty("ADBE Minimax");
    setEffParam(mm,"ADBE Minimax-0001",2); setEffParam(mm,"ADBE Minimax-0003",2);
    setEffKeys(mm,"ADBE Minimax-0002",[{time:0,val:15},{time:0.17684,val:0}]);
    return L;
  }
  function buildReleaseText(comp){
    var L = comp.layers.addText(CONFIG.textRelease);
    L.name = "КАЧАЙ!";
    L.startTime = 0; L.inPoint = t(3.53687020353687); L.outPoint = t(5.10510510510511);
    var srcProp = L.property("ADBE Text Properties").property("ADBE Text Document");
    var td = srcProp.value; styleText(td, CONFIG.textRelease); srcProp.setValue(td); f4VScale(L);
    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Position"), [540,325.000002235174,0]);
    setKeys(tr.property("ADBE Opacity"), [{time:3.97896,val:100},{time:4.24423,val:0}]);
    var fx = L.property("ADBE Effect Parade");
    var mm = fx.addProperty("ADBE Minimax");
    setEffParam(mm,"ADBE Minimax-0001",2); setEffParam(mm,"ADBE Minimax-0003",2); setEffParam(mm,"ADBE Minimax-0004",1);
    setEffKeys(mm,"ADBE Minimax-0002",[{time:0,val:15},{time:0.17684,val:0},{time:3.53685,val:15},{time:3.60358,val:0}]);
    return L;
  }
  function buildFlashAdjustment(comp){
    var L = comp.layers.addSolid([1,1,1], "Корректирующий слой 2", comp.width, comp.height, 1);
    L.adjustmentLayer = true;
    L.startTime = 0; L.inPoint = t(4.004004004004); L.outPoint = t(4.17083750417084);
    var fx = L.property("ADBE Effect Parade");
    var mm = fx.addProperty("ADBE Minimax");
    setEffParam(mm,"ADBE Minimax-0001",2); setEffParam(mm,"ADBE Minimax-0002",50);
    setEffParam(mm,"ADBE Minimax-0003",1); setEffParam(mm,"ADBE Minimax-0004",3);
    var lk = fx.addProperty("ADBE Luma Key");
    setEffParam(lk,"ADBE Luma Key-0001",2); setEffParam(lk,"ADBE Luma Key-0002",31);
    return L;
  }

  function buildFigure1(comp){
    var L = comp.layers.addShape();
    L.name = "Слой-фигура 1";
    L.startTime = 0; L.inPoint = t(0); L.outPoint = t(4.004004004004);
    var root = L.property("ADBE Root Vectors Group");
    addContour(root, "Фигура 1", F1_A, F1_B, null, [100, 105.263157894737], CONFIG.fig1Stroke);
    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Anchor Point"), [0,0,0]);
    setConst(tr.property("ADBE Position"),     [277.108840942383, 927.477739741489, 0]);
    setKeys(tr.property("ADBE Scale"), [
      {time:HEAD_ZOOM_T0, val:[-134,147.413291020099,100]},
      {time:HEAD_ZOOM_T1, val:[-289,317.928664961257,100]}
    ]);
    setConst(tr.property("ADBE Rotate Z"), -1.07629316392788);
    // Head bob (Y) + rotation wiggle BAKED to keyframes. The original expressions
    // read bpm from a Cyrillic slider on "bpm control" (thisComp.layer(...).
    // effect("ползунок")) — that lookup fails in headless aerender, so the head
    // had NO movement. Bake a deterministic in-tempo sine (freq = bpm/60 Hz,
    // bob ±15px, rot ±3°) from CONFIG.bpm; sampled ~12×/beat over the window.
    (function(){
      var pos = tr.property("ADBE Position");
      var rot = tr.property("ADBE Rotate Z");
      var baseX = 277.108840942383, baseY = 927.477739741489, baseRot = -1.07629316392788;
      var beatDur = 60.0 / CONFIG.bpm;          // comp-time seconds per beat
      var w0 = 0.0, w1 = 4.004004004004;        // authored window (pre-TS)
      var step = (beatDur / 12.0) / TS;          // authored step ≈ 1/12 beat
      if (!(step > 0)) step = 0.05;
      var PI2 = Math.PI * 2.0, x = w0, n = 0;
      while (x <= w1 + 1e-6 && n < 600){
        var ct = t(x), ph = PI2 * ct / beatDur;
        pos.setValueAtTime(ct, [baseX, baseY + 15.0 * Math.sin(ph), 0]);
        rot.setValueAtTime(ct, baseRot + 3.0 * Math.sin(ph + 1.3));
        x += step; n++;
      }
      var props = [[pos, 3], [rot, 1]];
      for (var p = 0; p < props.length; p++){
        var pr = props[p][0], dim = props[p][1];
        for (var i = 1; i <= pr.numKeys; i++){
          try { pr.setInterpolationTypeAtKey(i,
            KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER); } catch(e){}
          try {
            var ie = [], oe = [];
            for (var d = 0; d < dim; d++){ ie.push(new KeyframeEase(0,33.333)); oe.push(new KeyframeEase(0,33.333)); }
            pr.setTemporalEaseAtKey(i, ie, oe);
          } catch(e){}
        }
      }
    })();
    addBlur(L, [{time:HEAD_ZOOM_T0,val:0},{time:HEAD_ZOOM_T1,val:120}], 0.11, 1.16);
    return L;
  }
  function buildFigure2(comp){
    var L = comp.layers.addShape();
    L.name = "Слой-фигура 2";
    L.startTime = 0; L.inPoint = t(0); L.outPoint = t(4.004004004004);
    var root = L.property("ADBE Root Vectors Group");
    addContour(root, "Фигура 1", F2_A, F2_B, [0, -432], null, CONFIG.fig2Stroke);
    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Anchor Point"), [0,0,0]);
    setConst(tr.property("ADBE Position"),     [540,960,0]);
    setKeys(tr.property("ADBE Scale"), [
      {time:HEAD_ZOOM_T0, val:[100,-100,100]},
      {time:HEAD_ZOOM_T1, val:[140,-140,100]}
    ]);
    addBlur(L, [{time:HEAD_ZOOM_T0,val:4},{time:HEAD_ZOOM_T1,val:68}], 0.12, 1.16);
    return L;
  }

  app.beginUndoGroup("F4 head overlay");
  try {
    buildBlackSolid(comp);
    buildBpmControl(comp);
    buildHoldText(comp);
    buildReleaseText(comp);
    // buildFlashAdjustment removed — drop flash = F3 hook_light (added by overlay.py)
    buildFigure1(comp);
    buildFigure2(comp);
    try { $.writeln("[F4][__F4_DEVICE__] overlay built bpm=" + CONFIG.bpm); } catch(_){}
  } catch(err){
    try { $.writeln("[F4][__F4_DEVICE__] ERROR " + err.toString()); } catch(_){}
  } finally {
    app.endUndoGroup();
  }
})(MAIN_COMP);
