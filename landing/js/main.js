/* Blast landing — main.js. Живые фрагменты веб-приложения, стена роликов, шаги, язык через js/i18n.js. */
(function(){

  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  /* ── Атрибуция кампаний в веб-приложение (как раньше) ── */
  (() => { const keys = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'], q = new URLSearchParams(location.search), c = new URLSearchParams();
    keys.forEach(k => { const v = q.get(k); if(v) c.set(k, v.slice(0, 160)); });
    if(c.size) document.querySelectorAll('a[href^="https://app.blast808.com/"]').forEach(a => { const u = new URL(a.href); c.forEach((v, k) => u.searchParams.set(k, v)); a.href = u.toString(); }); })();

  /* ── Язык: ведёт общий js/i18n.js (BLAST_I18N, ?lang, blast_language), здесь — то, что он не знает ── */
  const I18N = window.BLAST_I18N;
  let LP_LANG = I18N ? I18N.getLanguage() : 'ru';
  function trTree(root){ if(I18N) I18N.translateSubtree(root); }
  window.__lpTr = trTree;
  function syncLang(){
    document.querySelectorAll('.lp-lang [role=radio], .lp-mfoot [role=radio]').forEach(b => { const on = b.dataset.language === LP_LANG;
      b.setAttribute('aria-checked', on); b.classList.toggle('bg-field-hover', on); b.classList.toggle('text-text', on); b.classList.toggle('shadow-[inset_0_0_0_1px_var(--line-strong)]', on); b.classList.toggle('text-text-60', !on); });
    /* в полосе этапов «Background» не влезает — короткое BG */
    document.querySelectorAll('.w12-tab .w12-l').forEach(l => { if(l.textContent === 'Фон' || l.textContent === 'Background' || l.textContent === 'BG') l.textContent = LP_LANG === 'en' ? 'BG' : 'Фон'; });
    document.querySelectorAll('textarea').forEach(ta => { if(!ta.dataset.ru) ta.dataset.ru = ta.value; ta.value = LP_LANG === 'en' ? '#nightcity #music #newtrack' : ta.dataset.ru; });
    if(window.__lpBuildLyric) window.__lpBuildLyric(LP_LANG); (window.__lpRepaint || []).forEach(f => f());
  }
  document.addEventListener('blast:languagechange', e => { LP_LANG = e.detail.language; syncLang(); });

  /* ── Ролики: base64 → blob, постер сразу, играют только в зоне видимости ── */
  const MEDIA = 'media/';
  function clipUrl(n){ return MEDIA + n + '.mp4'; }
  function poster(n){ return MEDIA + n + '.jpg'; }
  const vio = new IntersectionObserver(es => es.forEach(e => { const v = e.target;
    if(e.isIntersecting){ if(!v.src) v.src = clipUrl(v.dataset.clip); if(!reduce || !v.muted) v.play().catch(()=>{}); } else v.pause(); }), {rootMargin:'150px 0px'});
  function wire(v){ v.poster = poster(v.dataset.clip); vio.observe(v); }

  /* ── Стена: три колонки, бесконечная лента; чипы — настоящие psb-chip из плеера Пула ── */
  const chip = id => document.getElementById(id).innerHTML;
  const W = [
    [['w_h1','9/16','tpl-chip-bg'],['w_j2','1/1',''],['w_b1','4/3','tpl-chip-t'],['w_a1','1/1',''],['w_u1','4/5','']],
    [['w_t1','3/4',''],['w_i1','1/1','tpl-chip-fx'],['w_b4','4/3',''],['w_h2','9/16','tpl-chip-t'],['w_a2','1/1','']],
    [['w_j1','1/1','tpl-chip-t'],['w_u1','9/16',''],['w_b3','4/3','tpl-chip-bg'],['w_i2','1/1',''],['w_t2','3/4','tpl-chip-fx']]
  ];
  const wall = document.getElementById('wall'), cols = [];
  W.forEach((list, ci) => {
    const col = document.createElement('div'); col.className = 'lp-col'; const inner = document.createElement('div'); inner.className = 'lp-col-in'; col.appendChild(inner); wall.appendChild(col);
    list.forEach(([n, r, c]) => { const t = document.createElement('div'); t.className = 'lp-tile'; t.style.aspectRatio = r;
      const v = document.createElement('video'); v.muted = true; v.loop = true; v.playsInline = true; v.dataset.clip = n; t.appendChild(v);
      if(c){ const ch = document.createElement('div'); ch.className = 'psb-chips'; ch.innerHTML = '<div class="rail">' + chip(c) + '</div>'; t.appendChild(ch); }
      inner.appendChild(t); wire(v); });
    cols.push({inner, y: [0,-120,-60][ci], speed: [22,-16,28][ci]});
  });
  function stepWall(dt){ cols.forEach(c => { c.y -= c.speed*dt; const f = c.inner.firstElementChild, l = c.inner.lastElementChild, g = 12;
    if(c.speed > 0 && -c.y > f.offsetHeight + g){ c.y += f.offsetHeight + g; c.inner.appendChild(f); }
    if(c.speed < 0 && c.y > 0){ c.inner.insertBefore(l, f); c.y -= l.offsetHeight + g; }
    c.inner.style.transform = 'translate3d(0,' + c.y + 'px,0)'; }); }
  stepWall(0); let wOn = false, wl = 0;
  function wLoop(ts){ if(!wOn) return; const dt = wl ? Math.min(.05,(ts-wl)/1000) : 0; wl = ts; stepWall(dt); requestAnimationFrame(wLoop); }
  if(!reduce) new IntersectionObserver(([e]) => { wOn = e.isIntersecting; wl = 0; if(wOn) requestAnimationFrame(wLoop); }).observe(wall);
  document.querySelectorAll('video[data-clip]').forEach(v => { if(!v.closest('.lp-wall')) wire(v); });

  document.querySelectorAll('[role=radiogroup]:not(.lp-lang [role=radiogroup])').forEach(g => g.querySelectorAll('[role=radio]').forEach(b => b.addEventListener('click', () => {
    g.querySelectorAll('[role=radio]').forEach(x => { const on = x === b; x.setAttribute('aria-checked', on); x.classList.toggle('bg-field-hover', on); x.classList.toggle('text-text', on); x.classList.toggle('shadow-[inset_0_0_0_1px_var(--line-strong)]', on); x.classList.toggle('text-text-60', !on); }); })));

  /* ── Соцдоказательство: слова и кружки загораются как субтитр ── */
  const lyric = document.getElementById('lyric'); let lyricWords = [], lyricShown = false;
  function buildLyric(lang){
    lyric.innerHTML = lyric.dataset[lang];
    const out = [];
    [...lyric.childNodes].forEach(n => {
      if(n.nodeType === 3){ n.textContent.split(/(\s+)/).forEach(w => { if(!w) return; if(/^\s+$/.test(w)){ out.push(document.createTextNode(w)); return; }
        const s = document.createElement('span'); s.className = 'lp-w'; s.textContent = w; out.push(s); }); }
      else { const s = document.createElement('span'); s.className = 'lp-w' + (n.classList && n.classList.contains('lp-stack') ? ' lp-pics' : ''); s.appendChild(n.cloneNode(true)); out.push(s); }
    });
    lyric.replaceChildren(...out); lyricWords = [...lyric.querySelectorAll('.lp-w')];
    lyric.classList.toggle('armed', !reduce);
    if(lyricShown || reduce) lyricWords.forEach(w => w.classList.add('on'));
  }
  window.__lpBuildLyric = buildLyric;
  if(!reduce) new IntersectionObserver((es, ob) => es.forEach(e => { if(!e.isIntersecting) return; ob.disconnect(); lyricShown = true;
    lyricWords.forEach((w,i) => setTimeout(() => w.classList.add('on'), i*230)); }), {threshold:.6}).observe(lyric);

  /* ── Флоу: фрагменты въезжают при появлении ── */
  const fcs = [...document.querySelectorAll('.lp-fc')];
  const fio = new IntersectionObserver(es => es.forEach(e => { if(e.isIntersecting) e.target.classList.add('in'); }), {threshold:.25});
  fcs.forEach(f => fio.observe(f)); if(reduce) fcs.forEach(f => f.classList.add('in'));

  /* ── Звук в примерах ── */
  function setSound(btn){ const v = btn.closest('.lp-exclip').querySelector('video'), on = btn.getAttribute('aria-pressed') !== 'true';
    document.querySelectorAll('video').forEach(o => o.muted = true);
    document.querySelectorAll('.lp-snd').forEach(b => { b.setAttribute('aria-pressed','false'); b.classList.remove('bg-accent-strong'); });
    if(on){ v.muted = false; v.currentTime = 0; v.play().catch(()=>{}); btn.setAttribute('aria-pressed','true'); btn.classList.add('bg-accent-strong'); } }
  document.querySelectorAll('.lp-snd').forEach(b => b.addEventListener('click', e => { e.stopPropagation(); setSound(b); }));
  document.querySelectorAll('.lp-exclip').forEach(c => c.addEventListener('click', () => setSound(c.querySelector('.lp-snd'))));

  /* ── Цифры из частиц ── */
  function rng(s){return function(){s|=0;s=s+0x6D2B79F5|0;let t=Math.imul(s^s>>>15,1|s);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296}}
  function Particles(cv){
    const ctx = cv.getContext('2d'); let pts = [], w = 0, h = 0, dpr = 1, on = false, mx = -9999, my = -9999;
    function build(){ const r = cv.getBoundingClientRect(); dpr = Math.min(2, devicePixelRatio||1); w = Math.floor(r.width); h = Math.floor(r.height); if(!w) return;
      cv.width = w*dpr; cv.height = h*dpr; const off = document.createElement('canvas'); off.width = w; off.height = h; const o = off.getContext('2d', {willReadFrequently:true});
      const txt = cv.dataset.text, fs = Math.min(h*.62, w*(txt.length > 2 ? .3 : .44));
      o.font = '400 ' + fs + 'px Point'; o.textAlign = 'right'; o.fillStyle = '#fff'; o.fillText(txt, w*.93, h*.5 + fs*.36);
      const d = o.getImageData(0,0,w,h).data, rr = rng(9), old = pts; pts = [];
      for(let y=0;y<h;y+=4) for(let x=0;x<w;x+=4){ if(d[(y*w+x)*4+3] > 128 && rr() > .18){ const p = old[pts.length] || {x: x+(rr()-.5)*w*.6, y: y+(rr()-.5)*h*.8, vx:0, vy:0};
        p.hx = x+(rr()-.5)*2.5; p.hy = y+(rr()-.5)*2.5; p.s = rr() < .2 ? 2.4 : 1.6; p.a = .35 + rr()*.65; pts.push(p); } } }
    function draw(){ ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,w,h); for(const p of pts){ ctx.fillStyle = 'rgba(232,226,255,' + p.a + ')'; ctx.fillRect(p.x,p.y,p.s,p.s); } }
    function frame(){ if(!on) return;
      for(const p of pts){ const dx = p.x-mx, dy = p.y-my, d2 = dx*dx+dy*dy; if(d2 < RR){ const f = (RR-d2)/RR*2.4, dd = Math.sqrt(d2)||1; p.vx += dx/dd*f; p.vy += dy/dd*f; }
        p.vx += (p.hx-p.x)*.045; p.vy += (p.hy-p.y)*.045; p.vx *= .82; p.vy *= .82; p.x += p.vx; p.y += p.vy; }
      draw(); requestAnimationFrame(frame); }
    const host = cv.parentElement;
    /* мышь — отталкивает при наведении; палец — касание разгоняет частицы, потом они собираются обратно */
    let RR = 6400, rt0;
    const at = e => { const r = cv.getBoundingClientRect(); mx = e.clientX - r.left; my = e.clientY - r.top; RR = e.pointerType === 'mouse' ? 6400 : 16900; };
    host.addEventListener('pointermove', at);
    host.addEventListener('pointerdown', e => { at(e); clearTimeout(rt0); if(e.pointerType !== 'mouse') rt0 = setTimeout(() => { mx = my = -9999; }, 650); });
    host.addEventListener('pointerleave', () => { mx = my = -9999; });
    host.addEventListener('pointercancel', () => { mx = my = -9999; });
    Promise.race([document.fonts.load('400 100px Point'), new Promise(r => setTimeout(r, 1500))]).then(() => {
      build(); if(reduce){ pts.forEach(p => { p.x = p.hx; p.y = p.hy; }); draw(); return; }
      new IntersectionObserver(([e]) => { const was = on; on = e.isIntersecting; if(on && !was) requestAnimationFrame(frame); }, {threshold:.15}).observe(cv); });
    let rt; addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { build(); if(reduce){ pts.forEach(p => { p.x = p.hx; p.y = p.hy; }); draw(); } }, 150); });
  }
  document.querySelectorAll('canvas.lp-particles').forEach(Particles);

  /* ── Орбита: настоящие круги, пилюли идут по ним (внутренний и внешний в разные стороны) ── */
  const orbit = document.getElementById('orbit'), ops = [...orbit.querySelectorAll('.lp-op')], rings = orbit.querySelectorAll('.lp-ring');
  let oa = 0, oOn = false, ol = 0;
  function radii(){ const m = Math.min(orbit.clientWidth, orbit.clientHeight); return { inner: m*.25, outer: m*.44 }; }
  function placeOrbit(){ const R = radii(); rings[0].style.width = rings[0].style.height = R.inner*2 + 'px'; rings[1].style.width = rings[1].style.height = R.outer*2 + 'px';
    const inner = ops.filter(p => p.dataset.ring === 'inner'), outer = ops.filter(p => p.dataset.ring === 'outer');
    inner.forEach((p,i) => { const a = oa + i*Math.PI; p.style.transform = 'translate3d(calc(-50% + ' + (Math.cos(a)*R.inner).toFixed(2) + 'px), calc(-50% + ' + (Math.sin(a)*R.inner).toFixed(2) + 'px), 0)'; });
    outer.forEach((p,i) => { const a = -oa*.7 + i*Math.PI*2/3 + .5; p.style.transform = 'translate3d(calc(-50% + ' + (Math.cos(a)*R.outer).toFixed(2) + 'px), calc(-50% + ' + (Math.sin(a)*R.outer).toFixed(2) + 'px), 0)'; }); }
  function oLoop(ts){ if(!oOn) return; oa += ol ? (ts-ol)/1000*.22 : 0; ol = ts; placeOrbit(); requestAnimationFrame(oLoop); }
  placeOrbit(); addEventListener('resize', placeOrbit);
  if(!reduce) new IntersectionObserver(([e]) => { oOn = e.isIntersecting; ol = 0; if(oOn) requestAnimationFrame(oLoop); }).observe(orbit);

  /* ── Шаги: наведение / клик меняет инфографику справа; без наведения листаются сами ── */
  const steps = [...document.querySelectorAll('.lp-step')], panes = [...document.querySelectorAll('.lp-info-pane')]; let si = 0, sTimer, held = false;
  function pickStep(i){ si = i; steps.forEach((s,j) => s.setAttribute('aria-selected', j === i)); panes.forEach((p,j) => p.classList.toggle('on', j === i)); }
  steps.forEach((s,i) => { s.addEventListener('click', () => { held = true; pickStep(i); }); s.addEventListener('mouseenter', () => { held = true; pickStep(i); }); s.addEventListener('focus', () => { held = true; pickStep(i); }); });
  const infoEl = document.getElementById('info');
  infoEl.addEventListener('pointerenter', () => { held = true; }); infoEl.addEventListener('pointerdown', () => { held = true; });
  document.querySelector('.lp-steps-grid').addEventListener('mouseleave', () => { held = false; });
  function sNext(){ sTimer = setTimeout(() => { if(!held) pickStep((si+1) % 3); sNext(); }, si === 1 ? 9200 : 3200); }
  if(!reduce) new IntersectionObserver(([e]) => { clearTimeout(sTimer); if(e.isIntersecting) sNext(); }).observe(document.getElementById('info'));

  /* ── Живые фрагменты: всё на aria-атрибутах, как в приложении ── */
  document.addEventListener('click', e => {
    const tab = e.target.closest('.w12-tab, .w12-mode, .w12-fx-step');
    if(tab && tab.closest('.lp-frag, .lp-mw')){ const list = tab.parentElement; list.querySelectorAll('[role=tab]').forEach(x => x.setAttribute('aria-selected', x === tab));
      if(list.closest('.lp-mw-tabs')){ const i = [...list.children].indexOf(tab); document.querySelectorAll('#mw .lp-mw-p').forEach((p,j) => p.classList.toggle('on', j === i)); } return; }
    const fxh = e.target.closest('.w12-fx-head'); if(fxh){ fxh.setAttribute('aria-expanded', fxh.getAttribute('aria-expanded') !== 'true'); return; }
    const drop = e.target.closest('.w12-drop-opt'); if(drop){ drop.parentElement.querySelectorAll('.w12-drop-opt').forEach(x => x.setAttribute('aria-pressed', x === drop)); return; }
    const pill = e.target.closest('.w12-chiprow .w12-pill'); if(pill){ pill.parentElement.querySelectorAll('.w12-pill').forEach(x => x.setAttribute('aria-pressed', x === pill)); return; }
    const card = e.target.closest('.w12-mcard'); if(card){ card.setAttribute('aria-pressed', card.getAttribute('aria-pressed') !== 'true');
      let n = 0; card.parentElement.querySelectorAll('.w12-mcard').forEach(c => { const b = c.querySelector('.w12-badge'); if(b) b.textContent = c.getAttribute('aria-pressed') === 'true' ? ++n : ''; }); return; }
    const sbtn = e.target.closest('.w12-step button'); if(sbtn){ const st = sbtn.parentElement, num = st.querySelector('.w12-l'), btns = st.querySelectorAll('button');
      const v = Math.max(0, (parseInt(num.textContent)||0) + (sbtn === btns[btns.length-1] ? 1 : -1)); num.textContent = v; return; }
    const radio = e.target.closest('.ttp-seg [role=radio]'); if(radio && !radio.disabled){ const seg = radio.parentElement, rs = [...seg.querySelectorAll('[role=radio]')];
      rs.forEach(x => x.setAttribute('aria-checked', x === radio)); const th = seg.querySelector('.ttp-seg-thumb'); if(th) th.style.setProperty('--i', rs.indexOf(radio)); seg.dataset.picked = radio.dataset.value || ''; return; }
    const sw = e.target.closest('.ttp-switch, .ttp-box'); if(sw){ const on = sw.getAttribute('aria-checked') !== 'true'; sw.setAttribute('aria-checked', on);
      if(sw.classList.contains('ttp-box')) document.querySelectorAll('.ttp-act').forEach(b => b.classList.toggle('pending', !on)); return; }
    const tag = e.target.closest('.ttp-tag'); if(tag){ const ta = tag.closest('.ttp-field').querySelector('textarea'); if(ta) ta.value = (ta.value + ' ' + tag.textContent.replace('+','').trim()).trim(); tag.remove(); return; }
    if(e.target.closest('.lp-frag button, .lp-mw button')) e.preventDefault();
  });

  /* ── «Сгенерируй»: живой экран рендера — проценты растут, готовые ролики получают теги ── */
  const procRows = document.getElementById('procRows'), tplDone = document.getElementById('tpl-row-done').innerHTML, tplLoad = document.getElementById('tpl-row-load').innerHTML, tplQueue = document.getElementById('tpl-row-queue').innerHTML;
  const vids = [['Ночной город','Brat'],['Неон','Jakson'],['Ночной город','Impulse'],['Неон','Brat']];
  const bar = document.querySelector('#proc .bg-grad-main'), barTxt = bar ? bar.parentElement.querySelectorAll('span.relative') : [];
  let pk = 1, pp = 40, pT;
  function rowHtml(i){ let h = i < pk ? tplDone : i === pk ? tplLoad : tplQueue; h = h.replace(/Видео №\d/, 'Видео №' + (i+1));
    if(i < pk) h = h.replace('>Ночной город<', '>' + vids[i][0] + '<').replace('>Brat<', '>' + vids[i][1] + '<');
    if(i === pk) h = h.replace(/>\d+%</, '>' + pp + '%<'); return h; }
  function renderProc(){ procRows.innerHTML = vids.map((_, i) => rowHtml(i)).join(''); if(window.__lpTr) window.__lpTr(procRows);
    if(bar){ bar.style.width = Math.min(100, Math.round((pk + pp/100) / vids.length * 100)) + '%';
      if(barTxt[0]) barTxt[0].textContent = LP_LANG === 'en' ? 'Progress: ' + Math.min(pk, vids.length) + '/' + vids.length + ' videos' : 'Прогресс: ' + Math.min(pk, vids.length) + '/' + vids.length + ' видео';
      if(barTxt[1]){ const m = Math.max(1, (vids.length - pk) * 2 - Math.round(pp/50)); barTxt[1].textContent = pk >= vids.length ? (LP_LANG === 'en' ? 'Done' : 'Готово') : (LP_LANG === 'en' ? m + (m === 1 ? ' minute left' : ' minutes left') : 'Осталось ' + m + ' минут'); } } }
  function tickProc(){ if(pk >= vids.length){ pk = 0; pp = 0; } else { pp += 6; if(pp >= 100){ pp = 0; pk++; } } renderProc(); }
  renderProc(); (window.__lpRepaint = window.__lpRepaint || []).push(renderProc);
  if(!reduce) new IntersectionObserver(([e]) => { clearInterval(pT); if(e.isIntersecting) pT = setInterval(tickProc, 220); }).observe(document.getElementById('proc'));

  /* ── Отрывок: окно на волне тянется, края меняют длину (3–15 с) ── */
  const TRACK = 204, MIN = 3, MAX = 30;
  const fmtT = s => { const m = Math.floor(s / 60), r = s - m * 60; return m + ':' + (r < 10 ? '0' : '') + r.toFixed(1); };
  document.querySelectorAll('.w12-wave').forEach(wave => {
    const win = wave.querySelector('.w12-win'); if(!win) return;
    const bars = [...wave.querySelectorAll('.w12-bars i')], label = win.querySelector('.w12-win-label'), cut = wave.closest('.w12-cut');
    const ins = cut ? cut.querySelectorAll('.w12-tf input') : [];
    let a = parseFloat(win.style.left) / 100 * TRACK, b = a + parseFloat(win.style.width) / 100 * TRACK;
    function paint(){ win.style.left = (a / TRACK * 100) + '%'; win.style.width = ((b - a) / TRACK * 100) + '%';
      label.textContent = fmtT(a) + ' – ' + fmtT(b) + ' · ' + (LP_LANG === 'en' ? (b - a).toFixed(1) + ' s' : (b - a).toFixed(1).replace('.', ',') + ' с');
      bars.forEach((el, i) => { const tc = (i + .5) / bars.length * TRACK; el.classList.toggle('w12-in', tc >= a && tc <= b); });
      if(ins[0]) ins[0].value = fmtT(a); if(ins[1]) ins[1].value = fmtT(b); }
    let mode = null, x0 = 0, a0 = 0, b0 = 0;
    win.addEventListener('pointerdown', e => { e.preventDefault(); e.stopPropagation(); const h = e.target.closest('.w12-handle');
      mode = h ? h.dataset.handle : 'move'; x0 = e.clientX; a0 = a; b0 = b; win.setPointerCapture(e.pointerId); win.classList.add('drag');
      const st = wave.closest('.lp-steps-grid'); if(st) held = true; });
    win.addEventListener('pointermove', e => { if(!mode) return; const d = (e.clientX - x0) / wave.getBoundingClientRect().width * TRACK;
      if(mode === 'move'){ const len = b0 - a0; a = Math.min(Math.max(0, a0 + d), TRACK - len); b = a + len; }
      if(mode === 'l'){ a = Math.min(Math.max(0, a0 + d, b0 - MAX), b0 - MIN); }
      if(mode === 'r'){ b = Math.max(Math.min(TRACK, b0 + d, a0 + MAX), a0 + MIN); }
      paint(); });
    const end = () => { mode = null; win.classList.remove('drag'); };
    win.addEventListener('pointerup', end); win.addEventListener('pointercancel', end);
    paint(); (window.__lpRepaint = window.__lpRepaint || []).push(paint);
  });

  /* ── «Настрой»: мини-визард сам листает Фон → Текст → FX → Пул, пока его не трогают ── */
  const mwTabs = [...document.querySelectorAll('#mw .lp-mw-tabs [role=tab]')], mwPanes = [...document.querySelectorAll('#mw .lp-mw-p')];
  let mwi = 1, mwTimer;
  function mwPick(i){ mwi = i; mwTabs.forEach((x, j) => x.setAttribute('aria-selected', j === i)); mwPanes.forEach((p, j) => p.classList.toggle('on', j === i)); }
  let mwHeld = false; document.getElementById('mw').addEventListener('pointerdown', () => { mwHeld = true; });
  mwTabs.forEach((x, i) => x.addEventListener('click', () => { mwHeld = true; mwi = i; }));
  mwPick(1);
  if(!reduce) mwTimer = setInterval(() => { if(mwHeld || si !== 1) return; mwPick(mwi >= 4 ? 1 : mwi + 1); }, 2200);
  trTree(document.body); syncLang();
})();
