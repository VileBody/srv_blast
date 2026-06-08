/**********************************************************************
 * F4 motion-hook overlay — device "pinch"  (зум)
 * Injectable form of rebuild_pinch.jsx. Layers built on the passed comp from
 * comp-time 0 (clip window pre-reframed so cover-end == hook).
 * Tokens: __F4_BPM__, __F4_DEVICE__. Internal keyframes reflow via TS=refBpm/bpm;
 * layer length is fixed. S_BlurMotion is Sapphire (skipped gracefully if absent).
 **********************************************************************/
(function (comp) {
  if (!comp) { try { $.writeln("[F4][__F4_DEVICE__] no comp"); } catch(_){} return; }

  var CONFIG = {
    textRelease: "ЗУМ!",
    textHold:    "ЗУМЬ\rНАД ЭКРАНОМ\rВ ТАКТ",
    bpm:    __F4_BPM__,
    refBpm: 128,
    dropdownIdx: 1,
    tapHalveAboveBpm: 120,
    blurMochaOpacity: 0.05,
    blurCenterXY:     [-202.757385253906, 746.621337890625],
    blurToShiftY:     100,
    blurBrightness:   1.17,
    fingerStroke:        [1,1,1,1],
    fingerStrokeW:       12,
    fingerStrokeOpacity: 100
  };

  var TS = CONFIG.refBpm / CONFIG.bpm;
  var TAP_DIV = (CONFIG.bpm > CONFIG.tapHalveAboveBpm) ? 2 : 1;

  function t(x){ return x * TS; }
  function beat(n){ return n * 60 / CONFIG.bpm; }

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
          var inE = [], outE = [];
          for (var d = 0; d < dim; d++){ inE.push(new KeyframeEase(0,33.333)); outE.push(new KeyframeEase(0,33.333)); }
          prop.setTemporalEaseAtKey(i, inE, outE);
        } catch(e){}
      }
    }
  }
  function setConst(prop, val){ try { prop.setValue(val); } catch(e){} }
  function setEffParam(eff, mn, val){ try { eff.property(mn).setValue(val); } catch(e){} }
  function setEffKeys(eff, mn, keys, ease){ try { setKeys(eff.property(mn), keys, ease); } catch(e){} }

  function styleText(td, txt){
    td.text = txt;
    try { td.font = "TimesNewRomanPS-ItalicMT"; } catch(e){}
    td.fontSize = 72;
    td.applyFill = true; td.fillColor = [0.92157,0.92157,0.92157]; td.applyStroke = false;
    try { td.justification = ParagraphJustification.CENTER_JUSTIFY; } catch(e){}
    try { td.tracking = -60; } catch(e){}
    try { td.autoLeading = false; td.leading = 131; } catch(e){}
    return td;
  }

  function buildBlackSolid(comp){
    var L = comp.layers.addSolid([0,0,0], "Сплошная заливка Черный 1", comp.width, comp.height, 1);
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(4.2042042042042);
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
    try { var dd = fx.addProperty("ADBE Dropdown Control"); try{ dd.property(1).setValue(CONFIG.dropdownIdx);}catch(e){} } catch(e){}
    try { var sl = fx.addProperty("ADBE Slider Control"); sl.name = "ползунок";
          sl.property("ADBE Slider Control-0001").setValue(CONFIG.bpm); } catch(e){}
    return L;
  }

  function buildHoldText(comp){
    var L = comp.layers.addText(CONFIG.textHold);
    L.name = "ЗУМЬ НАД ЭКРАНОМ В ТАКТ";
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(5.30530530530531);
    var srcProp = L.property("ADBE Text Properties").property("ADBE Text Document");
    var td = srcProp.value; styleText(td, CONFIG.textHold); srcProp.setValue(td);
    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Position"), [540,325.000002235174,0]);
    setKeys(tr.property("ADBE Opacity"), [{time:3.30328, val:100},{time:3.56855, val:0}]);
    var animers = L.property("ADBE Text Properties").property("ADBE Text Animators");
    var anim = animers.addProperty("ADBE Text Animator");
    try { anim.property("ADBE Text Animator Properties").addProperty("ADBE Text Opacity").setValue(0); } catch(e){}
    var sel = anim.property("ADBE Text Selectors").addProperty("ADBE Text Selector");
    try { sel.property("ADBE Text Range Advanced").property("ADBE Text Range Type2").setValue(3); }
    catch(e){ try { sel.property("ADBE Text Range Type2").setValue(3); } catch(e2){} }
    setKeys(sel.property("ADBE Text Percent Start"), [{time:0, val:0},{time:0.86754, val:100}]);
    var fx = L.property("ADBE Effect Parade");
    var mm = fx.addProperty("ADBE Minimax");
    setEffParam(mm, "ADBE Minimax-0001", 2);
    setEffParam(mm, "ADBE Minimax-0003", 2);
    setEffKeys (mm, "ADBE Minimax-0002", [{time:0, val:15},{time:0.17684, val:0}]);
    return L;
  }

  function buildReleaseText(comp){
    var L = comp.layers.addText(CONFIG.textRelease);
    L.name = "ЗУМ!";
    L.startTime = 0; L.inPoint = t(3.53687020353687); L.outPoint = t(5.30530530530531);
    var srcProp = L.property("ADBE Text Properties").property("ADBE Text Document");
    var td = srcProp.value; styleText(td, CONFIG.textRelease); srcProp.setValue(td);
    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Position"), [540,325.000002235174,0]);
    setKeys(tr.property("ADBE Opacity"), [{time:3.97896, val:100},{time:4.24423, val:0}]);
    var fx = L.property("ADBE Effect Parade");
    var mm = fx.addProperty("ADBE Minimax");
    setEffParam(mm, "ADBE Minimax-0001", 2);
    setEffParam(mm, "ADBE Minimax-0003", 2);
    setEffParam(mm, "ADBE Minimax-0004", 1);
    setEffKeys (mm, "ADBE Minimax-0002",
      [{time:0,val:15},{time:0.17684,val:0},{time:3.53685,val:15},{time:3.60358,val:0}]);
    return L;
  }

  function makeShape(verts, inT, outT){
    var s = new Shape(); s.vertices = verts; s.inTangents = inT; s.outTangents = outT; s.closed = true; return s;
  }

  var F3_A = makeShape(
    [[-385.801788330078,185.575988769531],[-208.779510498047,325.039916992188],[173.506408691406,393.556396484375],[-126.558166503906,220.817291259766]],
    [[34.8504638671875,-49.789306640625],[-128.305511474609,-58.9582824707031],[-78.8752136230469,88.1015014648438],[126.392944335938,75.1796875]],
    [[-25.7967834472656,36.8547058105469],[30.08154296875,13.8228149414062],[78.8748779296875,-88.1015014648438],[-83.9717407226562,-49.9471435546875]]);
  var F3_B = makeShape(
    [[-487.90869140625,222.356781005859],[-241.4111328125,347.385009765625],[173.50634765625,393.556396484375],[-194.891418457031,232.456024169922]],
    [[34.8504638671875,-49.7892761230469],[-128.305480957031,-58.9582824707031],[-78.8751220703125,88.1015014648438],[126.392852783203,75.1796569824219]],
    [[-25.7967529296875,36.8547058105469],[30.08154296875,13.8228149414062],[78.8748779296875,-88.1015014648438],[-83.9718627929688,-49.9471435546875]]);
  var F3_drop = makeShape(
    [[-532.449768066406,248.297729492188],[-241.4111328125,347.385009765625],[173.50634765625,393.556396484375],[-194.891418457031,232.455993652344]],
    [[34.8504638671875,-49.789306640625],[-128.305480957031,-58.9582824707031],[-78.8751220703125,88.1015014648438],[126.392852783203,75.1796875]],
    [[-25.796630859375,36.8546142578125],[30.08154296875,13.8228149414062],[78.8748779296875,-88.1015014648438],[-83.9718627929688,-49.9471435546875]]);

  var F2_A = makeShape(
    [[-302.962768554688,-27.4786071777344],[-65.9515991210938,96.6821441650391],[331.648010253906,198.206878662109],[117.108337402344,36.3576507568359]],
    [[57.3650512695312,-66.2257690429688],[-86.875244140625,-30.8331451416016],[71.467041015625,111.01806640625],[70.6065673828125,55.98681640625]],
    [[-54.5562133789062,62.9830932617188],[139.845733642578,49.6330718994141],[-71.467041015625,-111.018035888672],[-68.210205078125,-54.0866394042969]]);
  var F2_B = makeShape(
    [[-283.967895507812,-138.938507080078],[54.1917114257812,126.789047241211],[347.206970214844,212.218811035156],[187.998474121094,43.5829772949219]],
    [[111.240325927734,-51.7263793945312],[-75.9080810546875,-52.30615234375],[71.467529296875,111.017578125],[70.6065673828125,55.9860382080078]],
    [[-78.9871215820312,36.7287902832031],[134.618774414062,92.7630157470703],[-71.4671630859375,-111.018692016602],[-68.2101440429688,-54.0868988037109]]);
  var F2_drop = makeShape(
    [[-164.303588867188,-243.235656738281],[54.1917419433594,126.788940429688],[347.207000732422,212.21875],[187.998626708984,43.5829467773438]],
    [[111.240295410156,-51.7263488769531],[-75.9080963134766,-52.3060913085938],[71.467529296875,111.016754150391],[70.6063537597656,55.9861450195312]],
    [[-78.9872131347656,36.7287902832031],[134.618896484375,92.7628479003906],[-71.4671630859375,-111.018646240234],[-68.2103271484375,-54.0868835449219]]);

  var FINGER_STEP     = 0.23356690023357;
  var FINGER_ALT_KEYS = 17;
  var FINGER_DROP     = 3.93727060393727;

  function buildContour(root, name, A, B, drop, grpPos, grpScale){
    var grp = root.addProperty("ADBE Vector Group"); grp.name = name;
    var cont = grp.property("ADBE Vectors Group");
    var pg = cont.addProperty("ADBE Vector Shape - Group"); pg.name = "Контур 1";
    var path = pg.property("ADBE Vector Shape");

    var step = FINGER_STEP * TAP_DIV;
    var nAlt = Math.round((FINGER_STEP * (FINGER_ALT_KEYS - 1)) / step) + 1;
    for (var k = 0; k < nAlt; k++) path.setValueAtTime(t(k * step), (k % 2 === 0) ? A : B);
    path.setValueAtTime(t(FINGER_DROP), drop);
    for (var i = 1; i <= path.numKeys; i++){
      try { path.setInterpolationTypeAtKey(i,
        KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER); } catch(e){}
    }

    var st = cont.addProperty("ADBE Vector Graphic - Stroke");
    setConst(st.property("ADBE Vector Stroke Color"),   CONFIG.fingerStroke);
    setConst(st.property("ADBE Vector Stroke Width"),   CONFIG.fingerStrokeW);
    setConst(st.property("ADBE Vector Stroke Opacity"), CONFIG.fingerStrokeOpacity);

    var gt = grp.property("ADBE Vector Transform Group");
    if (grpPos)   try { gt.property("ADBE Vector Position").setValue(grpPos); } catch(e){}
    if (grpScale) try { gt.property("ADBE Vector Scale").setValue(grpScale); } catch(e){}
    return grp;
  }

  function buildFinger(comp){
    var L = comp.layers.addShape();
    L.name = "finger";
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(4.3043043043043);

    var root = L.property("ADBE Root Vectors Group");
    buildContour(root, "Фигура 3", F3_A, F3_B, F3_drop,
                 [84.3608559658916, 4.72392891083439], [100, 99.2805755395683]);
    buildContour(root, "Фигура 2", F2_A, F2_B, F2_drop,
                 [-19.3040208033776, 11.636038694339], [100, 99.2805755395683]);

    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Anchor Point"), [0,0,0]);
    setConst(tr.property("ADBE Position"),     [982.870742797852, 1112.4376373291, 0]);
    setConst(tr.property("ADBE Rotate Z"),     -9);
    setKeys(tr.property("ADBE Scale"), [
      {time:3.8038038038038,  val:[110.5,185.38897392969,100]},
      {time:3.93727060393727, val:[157.5,264.242202659966,100]}
    ]);
    setKeys(tr.property("ADBE Opacity"), [
      {time:3.8038038038038,  val:100},
      {time:3.93727060393727, val:0}
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
    var L = comp.layers.addSolid([1,1,1], "Корректирующий слой 2", comp.width, comp.height, 1);
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

  app.beginUndoGroup("F4 pinch overlay");
  try {
    buildBlackSolid(comp);
    buildBpmControl(comp);
    buildHoldText(comp);
    buildReleaseText(comp);
    buildFinger(comp);
    buildFlashAdjustment(comp);
    try { $.writeln("[F4][__F4_DEVICE__] overlay built bpm=" + CONFIG.bpm); } catch(_){}
  } catch(err){
    try { $.writeln("[F4][__F4_DEVICE__] ERROR " + err.toString()); } catch(_){}
  } finally {
    app.endUndoGroup();
  }
})(MAIN_COMP);
