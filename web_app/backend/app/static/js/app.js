(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const page = document.body.dataset.page;

  function toast(message, type = 'success', action) {
    const stack = $('#toast-stack');
    if (!stack) return;
    const item = document.createElement('div');
    item.className = `toast ${type}`;
    item.innerHTML = `<strong>${type === 'error' ? 'Ошибка' : 'Blast'}</strong><span>${message}</span>${action ? `<a class="btn secondary small" href="${action.href}">${action.label}</a>` : ''}`;
    stack.appendChild(item);
    setTimeout(() => item.remove(), 4000);
  }

  function setLoading(btn, state) {
    if (!btn) return;
    if (state) {
      btn.dataset.label = btn.textContent;
      btn.classList.add('loading');
      btn.disabled = true;
    } else {
      btn.classList.remove('loading');
      btn.disabled = false;
      if (btn.dataset.label) btn.textContent = btn.dataset.label;
    }
  }

  async function api(url, options = {}) {
    const headers = options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' };
    const response = await fetch(url, { ...options, headers: { ...headers, ...(options.headers || {}) } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
    return data;
  }

  $$('[data-mock-toast]').forEach((el) => el.addEventListener('click', () => toast(el.dataset.mockToast, 'success')));

  $('[data-drawer-open]')?.addEventListener('click', () => {
    $('.mobile-drawer')?.classList.add('open');
    $('.drawer-overlay')?.classList.add('open');
  });
  $$('[data-drawer-close]').forEach((el) => el.addEventListener('click', () => {
    $('.mobile-drawer')?.classList.remove('open');
    $('.drawer-overlay')?.classList.remove('open');
  }));

  let lastActiveJobId = null;
  async function pollActiveJob() {
    const dot = $('#sidebar-job-dot');
    const link = $('#nav-generate-link');
    if (!dot || !link) return;
    try {
      const { job } = await api('/api/jobs/active');
      if (job) {
        dot.hidden = false;
        link.href = `/app/processing/${job.id}`;
        lastActiveJobId = job.id;
      } else {
        dot.hidden = true;
        link.href = '/app/generate';
        if (lastActiveJobId) toast('Ролики готовы', 'success', { label: 'Открыть', href: `/app/processing/${lastActiveJobId}` });
        lastActiveJobId = null;
      }
    } catch (_) {}
  }
  pollActiveJob();
  setInterval(pollActiveJob, 5000);

  function initAuth() {
    $('[data-toggle-password]')?.addEventListener('click', (event) => {
      const input = event.currentTarget.closest('.password-field').querySelector('input');
      input.type = input.type === 'password' ? 'text' : 'password';
    });

    $('#login-form')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const btn = event.submitter;
      setLoading(btn, true);
      try {
        const form = new FormData(event.currentTarget);
        const data = await api('/api/auth/login', { method: 'POST', body: JSON.stringify(Object.fromEntries(form)) });
        toast('Вход выполнен');
        location.href = data.redirectTo || '/app';
      } catch (err) {
        toast(err.message, 'error');
      } finally {
        setLoading(btn, false);
      }
    });

    $('#register-form')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const btn = event.submitter;
      setLoading(btn, true);
      try {
        const form = new FormData(event.currentTarget);
        const data = await api('/api/auth/register', { method: 'POST', body: JSON.stringify(Object.fromEntries(form)) });
        const modal = $('#tg-modal');
        $('#tg-open-link').href = data.deepLink;
        $('#tg-check-btn').dataset.userId = data.user.id;
        modal.hidden = false;
        toast('Пользователь создан. Осталась TG-верификация.');
      } catch (err) {
        toast(err.message, 'error');
      } finally {
        setLoading(btn, false);
      }
    });

    $('#tg-check-btn')?.addEventListener('click', async (event) => {
      const btn = event.currentTarget;
      setLoading(btn, true);
      try {
        const data = await api(`/api/auth/tg-verify?userId=${encodeURIComponent(btn.dataset.userId || '')}`);
        if (data.verified) {
          $('#tg-status').textContent = 'Аккаунт подтверждён. Перенаправляем...';
          toast('TG-верификация пройдена');
          setTimeout(() => location.href = '/app?verified=true', 700);
        } else {
          $('#tg-status').textContent = 'Пока не видим запуск бота. Нажми ещё раз в мок-режиме.';
        }
      } catch (err) {
        toast(err.message, 'error');
      } finally {
        setLoading(btn, false);
      }
    });
  }

  function initProjects() {
    const modal = $('#project-modal');
    $$('[data-open-project-modal]').forEach((btn) => btn.addEventListener('click', () => { if (modal) modal.hidden = false; }));
    $$('[data-close-modal]').forEach((btn) => btn.addEventListener('click', () => { if (modal) modal.hidden = true; }));
    modal?.addEventListener('click', (event) => { if (event.target === modal) modal.hidden = true; });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && modal) modal.hidden = true; });
    $('#project-form')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const btn = event.submitter;
      setLoading(btn, true);
      try {
        const form = new FormData(event.currentTarget);
        const data = await api('/api/projects', { method: 'POST', body: JSON.stringify(Object.fromEntries(form)) });
        toast('Проект создан');
        location.href = data.redirectTo;
      } catch (err) {
        toast(err.message, 'error');
      } finally {
        setLoading(btn, false);
      }
    });
  }

  function initPricing() {
    const checkbox = $('#offer-checkbox');
    const buttons = $$('[data-buy-plan]');
    const sync = () => buttons.forEach((btn) => btn.disabled = !checkbox?.checked);
    checkbox?.addEventListener('change', sync);
    sync();
    buttons.forEach((btn) => btn.addEventListener('click', async () => {
      setLoading(btn, true);
      try {
        const data = await api('/api/payments/create-order', { method: 'POST', body: JSON.stringify({ packageType: btn.dataset.buyPlan }) });
        toast(`Создан mock-order ${data.orderId}`);
        location.href = data.paymentUrl;
      } catch (err) {
        toast(err.message, 'error');
      } finally {
        setLoading(btn, false);
      }
    }));
  }

  function initProfile() {
    $('#profile-form')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        await api('/api/profile', { method: 'PATCH', body: JSON.stringify(Object.fromEntries(form)) });
        toast('Профиль обновлён');
      } catch (err) {
        toast(err.message, 'error');
      }
    });
    $('#avatar-input')?.addEventListener('change', async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;
      const fd = new FormData();
      fd.append('file', file);
      try {
        await api('/api/profile/avatar', { method: 'POST', body: fd });
        toast('Аватар загружен');
        setTimeout(() => location.reload(), 700);
      } catch (err) {
        toast(err.message, 'error');
      }
    });
    $('#disconnect-tiktok')?.addEventListener('click', async () => {
      try {
        await api('/api/tiktok/disconnect', { method: 'DELETE' });
        toast('TikTok отключён');
        setTimeout(() => location.reload(), 700);
      } catch (err) {
        toast(err.message, 'error');
      }
    });
    $('#cancel-sub')?.addEventListener('click', async () => {
      try {
        await api('/api/payments/cancel-sub', { method: 'POST', body: '{}' });
        toast('Подписка отменена в мок-режиме');
      } catch (err) {
        toast(err.message, 'error');
      }
    });
  }

  function initProjectDetail() {
    $$('[data-tiktok-post]').forEach((btn) => btn.addEventListener('click', async () => {
      try {
        await api('/api/tiktok/post', { method: 'POST', body: JSON.stringify({ videoId: btn.dataset.tiktokPost }) });
        toast('Публикация поставлена в очередь');
      } catch (err) {
        toast('Подключи TikTok в профиле', 'error');
      }
    }));
  }

  function initProcessing() {
    const root = $('.processing-page');
    if (!root) return;
    const jobId = root.dataset.jobId;
    const list = $('#job-list');
    const ratingCard = $('#rating-card');
    const followup = $('#rating-followup');

    const statusClass = (status) => status === 'COMPLETED' ? 'success' : status === 'FAILED' ? 'error' : 'info';
    const statusText = (status) => status === 'COMPLETED' ? 'Готово' : status === 'PENDING' ? 'В очереди' : status === 'FAILED' ? 'Ошибка' : 'Генерируется';

    async function refreshJob() {
      try {
        const { job } = await api(`/api/jobs/${jobId}`);
        job.videos.forEach((video) => {
          const row = list.querySelector(`[data-video-id="${video.id}"]`);
          if (!row) return;
          row.querySelector('.progress-wrap > div').style.width = `${video.progress}%`;
          row.querySelector('.job-progress strong').textContent = `${video.progress}%`;
          const badge = row.querySelector('.status-badge');
          badge.className = `status-badge ${statusClass(video.status)}`;
          badge.textContent = statusText(video.status);
          const download = row.querySelector('.download-link');
          if (video.downloadUrl) {
            download.href = video.downloadUrl;
            download.classList.remove('disabled');
          }
          if (video.status === 'COMPLETED') row.querySelector('.job-thumb').classList.remove('shimmer');
        });
        if (job.status === 'COMPLETED') {
          ratingCard.hidden = false;
          clearInterval(timer);
        }
      } catch (err) {
        toast(err.message, 'error');
      }
    }
    const timer = setInterval(refreshJob, 3000);
    refreshJob();

    $$('[data-scroll-rating]').forEach((btn) => btn.addEventListener('click', () => {
      ratingCard.hidden = false;
      ratingCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }));
    $('[data-hide-rating]')?.addEventListener('click', () => ratingCard.hidden = true);
    $$('.rating-options [data-rating]').forEach((btn) => btn.addEventListener('click', () => {
      const rating = Number(btn.dataset.rating);
      if (rating <= 4) {
        followup.innerHTML = `<label>Опиши, что не так<textarea rows="4" id="rating-feedback" placeholder="Например: не тот футаж, субтитры слишком быстрые"></textarea></label><button class="btn primary small" id="send-rating">Отправить</button><p class="muted">Мы исправим бесплатно — свяжемся с тобой.</p>`;
      } else if (rating <= 6) {
        followup.innerHTML = `<div class="rating-tags"><button class="pill">Субтитры</button><button class="pill">Исходники</button><button class="pill">Переходы</button><button class="pill">Другое</button></div><button class="btn primary small" id="send-rating">Сохранить</button>`;
      } else {
        followup.innerHTML = `<p>Отлично! Спасибо за оценку.</p><a class="btn secondary small" href="/app/pricing">Посмотреть тарифы</a><button class="btn primary small" id="send-rating">Сохранить</button>`;
      }
      $('#send-rating')?.addEventListener('click', async () => {
        try {
          const feedback = $('#rating-feedback')?.value || '';
          await api(`/api/jobs/${jobId}/rate`, { method: 'POST', body: JSON.stringify({ rating, feedback }) });
          followup.innerHTML = '<p class="status-badge success">Спасибо за оценку ✓</p>';
        } catch (err) {
          toast(err.message, 'error');
        }
      });
    }));
  }

  function initWizard() {
    const root = $('.wizard-page');
    if (!root) return;
    const settings = $('#wizard-settings');
    const stepper = $('#wizard-stepper');
    const next = $('#wizard-next');
    const previewVisual = $('#preview-visual');
    const previewLabel = $('.preview-label');
    const trackTitle = $('#track-title');
    const trackMeta = $('#track-meta');
    const steps = ['Трек', 'Фон', 'Хук', 'Титры', 'Финал'];
    const state = {
      step: 1,
      projectId: root.dataset.projectId || '',
      track: null,
      lyrics: '',
      timingMode: 'ai',
      timingFrom: '1:24',
      timingTo: '1:46',
      bgMode: 'footage',
      vibes: [],
      color: 'black',
      drop: '1:24',
      hook: null,
      subtitles: 'Impulse',
      subtitleColor: '#ffffff',
      accentColor: '#8b6fe6',
      videosToGenerate: 1,
      idempotencyKey: crypto.randomUUID?.() || `${Date.now()}`,
    };

    function saveSession() {
      const stageData = buildStageData();
      api('/api/wizard/session', { method: 'POST', body: JSON.stringify({ projectId: state.projectId, stage: state.step, data: stageData }) }).catch(() => {});
      localStorage.setItem('blastWizardState', JSON.stringify(state));
    }

    function buildStageData() {
      return {
        track: state.track,
        lyrics: state.lyrics,
        timing: state.timingMode === 'manual' ? { from: state.timingFrom, to: state.timingTo } : { mode: 'ai' },
        background: { mode: state.bgMode, vibes: state.vibes, color: state.color },
        hook: state.hook,
        subtitles: { style: state.subtitles },
        colors: { subtitle: state.subtitleColor, accent: state.accentColor },
      };
    }

    function setPreview(label, symbol = '♪') {
      previewLabel.textContent = label;
      previewVisual.style.opacity = '0.25';
      setTimeout(() => {
        previewVisual.innerHTML = `<span>${symbol}</span>`;
        previewVisual.style.opacity = '1';
      }, 120);
    }

    function renderStepper() {
      stepper.innerHTML = `<div class="stepper-inner">${steps.map((label, index) => {
        const num = index + 1;
        const cls = num < state.step ? 'done' : num === state.step ? 'current' : '';
        const dot = num < state.step ? '✓' : num;
        return `<button type="button" class="step-item ${cls}" data-step="${num}" ${num > state.step ? 'disabled' : ''}><span class="step-dot">${dot}</span><span>${label}</span></button>`;
      }).join('')}</div>`;
      $$('.step-item', stepper).forEach((btn) => btn.addEventListener('click', () => {
        const target = Number(btn.dataset.step);
        if (target <= state.step) {
          state.step = target;
          render();
        }
      }));
    }

    function validate() {
      if (state.step === 1) return !!state.track && state.lyrics.trim().length > 0;
      if (state.step === 2) return state.bgMode === 'color' ? !!state.color : state.vibes.length > 0;
      if (state.step === 3) return state.hook === null || !!state.hook;
      if (state.step === 4) return !!state.subtitles;
      if (state.step === 5) return true;
      return false;
    }

    function bindCommonInputs() {
      $$('[data-next-enable]').forEach((el) => el.addEventListener('input', () => {
        if (el.name === 'lyrics') state.lyrics = el.value;
        next.disabled = !validate();
      }));
    }

    async function renderTrack() {
      settings.innerHTML = `
        <div class="wizard-stage">
          <section class="stage-section">
            <p class="eyebrow">Загрузи трек</p>
            <label class="drop-zone" id="track-drop"><input type="file" id="track-file" hidden accept="audio/*" /><strong>⇧ Перетащи файл или выбери с устройства</strong><span>MP3, M4A, WAV · до 200 МБ</span></label>
            <button class="link-button" type="button" id="previous-track">↻ Использовать предыдущий трек</button>
          </section>
          <section class="stage-section">
            <div class="section-head"><p class="eyebrow">Текст песни</p><label class="radio-line"><input type="checkbox" id="fragment-check" /> Указать конкретный отрывок</label></div>
            <textarea name="lyrics" data-next-enable rows="6" placeholder="Вставь полный текст трека">${state.lyrics}</textarea>
            <textarea id="fragment-text" rows="3" placeholder="Скопируй строки, которые должны войти в ролик" hidden></textarea>
          </section>
          <section class="stage-section">
            <p class="eyebrow">Тайминг</p>
            <div class="segmented"><button type="button" data-timing="ai" class="${state.timingMode === 'ai' ? 'active' : ''}">На усмотрение ИИ</button><button type="button" data-timing="manual" class="${state.timingMode === 'manual' ? 'active' : ''}">Указать вручную</button></div>
            <div class="timing-row" id="timing-row" ${state.timingMode === 'manual' ? '' : 'hidden'}><span>С</span><input id="timing-from" value="${state.timingFrom}" /><span>По</span><input id="timing-to" value="${state.timingTo}" /><span class="muted">5–22 секунды</span></div>
          </section>
        </div>`;
      setPreview(state.track ? 'Waveform превью' : 'Загрузи трек, чтобы увидеть превью', state.track ? '▱' : '♪');
      $('#track-drop').addEventListener('click', () => $('#track-file').click());
      $('#track-file').addEventListener('change', async (event) => {
        const file = event.target.files?.[0];
        if (!file) return;
        const fd = new FormData();
        fd.append('file', file);
        try {
          const data = await api('/api/wizard/upload-track', { method: 'POST', body: fd });
          state.track = data.track;
          trackTitle.textContent = data.track.filename;
          trackMeta.textContent = `MP3 · ${Math.floor(data.track.durationS / 60)}:${String(Math.floor(data.track.durationS % 60)).padStart(2, '0')}`;
          toast('Трек загружен');
          await api('/api/wizard/analyze-track', { method: 'POST', body: JSON.stringify({ s3Key: data.track.s3Key }) });
          await api('/api/wizard/rank-vibes', { method: 'POST', body: JSON.stringify({ lyrics: state.lyrics }) });
          render();
        } catch (err) { toast(err.message, 'error'); }
      });
      $('#previous-track').addEventListener('click', async () => {
        const { track } = await api('/api/wizard/previous-track');
        if (track) {
          state.track = track;
          trackTitle.textContent = track.filename;
          trackMeta.textContent = `MP3 · ${Math.floor(track.durationS / 60)}:${String(Math.floor(track.durationS % 60)).padStart(2, '0')}`;
          toast('Предыдущий трек выбран');
          render();
        }
      });
      $('#fragment-check').addEventListener('change', (event) => $('#fragment-text').hidden = !event.target.checked);
      $$('[data-timing]').forEach((btn) => btn.addEventListener('click', () => {
        state.timingMode = btn.dataset.timing;
        render();
      }));
      $('#timing-from')?.addEventListener('input', (event) => state.timingFrom = event.target.value);
      $('#timing-to')?.addEventListener('input', (event) => state.timingTo = event.target.value);
      bindCommonInputs();
    }

    async function renderBackground() {
      settings.innerHTML = `<div class="wizard-stage"><div class="pill-tabs"><button data-bg="footage" class="${state.bgMode === 'footage' ? 'active' : ''}">Футажи</button><button data-bg="color" class="${state.bgMode === 'color' ? 'active' : ''}">Цветной фон</button><button data-bg="photo" class="${state.bgMode === 'photo' ? 'active' : ''}">Фото</button></div><div id="bg-content"></div></div>`;
      $$('[data-bg]').forEach((btn) => btn.addEventListener('click', () => { state.bgMode = btn.dataset.bg; renderBackground(); }));
      const bg = $('#bg-content');
      if (state.bgMode === 'color') {
        bg.innerHTML = `<section class="stage-section"><p class="eyebrow">Цвет</p><div class="color-grid"><button class="choice-card" data-color="white"><strong>Белый</strong></button><button class="choice-card" data-color="black"><strong>Чёрный</strong></button><button class="choice-card" data-color="green"><strong>Хромакей</strong></button></div><label class="radio-line"><input type="checkbox" /> Сделать интерактивным — цвет меняется в такт треку <span class="muted">скоро</span></label></section>`;
        $$('[data-color]').forEach((card) => {
          if (card.dataset.color === state.color) card.classList.add('selected');
          card.addEventListener('click', () => { state.color = card.dataset.color; setPreview(`Цветной фон · ${state.color}`, '■'); renderBackground(); });
        });
      } else {
        bg.innerHTML = `<section class="stage-section"><p class="eyebrow">${state.bgMode === 'photo' ? 'Фото из S3-библиотеки' : 'Вайбы футажей'}</p><p class="muted">Анализируем трек...</p></section>`;
        const { vibes } = await api('/api/wizard/vibes');
        bg.innerHTML = `<section class="stage-section"><div class="section-head"><p class="eyebrow">${state.bgMode === 'photo' ? 'Фото' : 'Футажи'}</p><button class="btn secondary small" id="auto-vibe">Автовыбор по треку</button></div><div class="vibe-grid">${vibes.map(v => `<button class="choice-card ${state.vibes.includes(v.name) ? 'selected' : ''}" data-vibe="${v.name}"><strong>${v.name}</strong><span class="muted">score ${(v.score * 100).toFixed(0)}%</span></button>`).join('')}</div><p class="muted">${state.vibes.length > 1 ? 'Один вайб = один ролик. Финальное число роликов задаётся на Этапе 5.' : state.bgMode === 'photo' ? 'Фотографии подбираются из нашей библиотеки по вайбу трека.' : 'Hover/выбор обновляет Preview Panel.'}</p></section>`;
        $$('.choice-card[data-vibe]').forEach((card) => {
          card.addEventListener('mouseenter', () => setPreview(`${state.bgMode === 'photo' ? 'Фото' : 'Футаж'} · ${card.dataset.vibe}`, state.bgMode === 'photo' ? '□' : '▶'));
          card.addEventListener('click', () => {
            const name = card.dataset.vibe;
            state.vibes = state.vibes.includes(name) ? state.vibes.filter(v => v !== name) : [...state.vibes, name];
            setPreview(`${state.bgMode === 'photo' ? 'Фото' : 'Футаж'} · ${state.vibes[0] || name}`, state.bgMode === 'photo' ? '□' : '▶');
            renderBackground();
          });
        });
        $('#auto-vibe').addEventListener('click', () => { state.vibes = [vibes[0].name]; toast('Выбран top-1 вайб ранкера'); renderBackground(); });
      }
    }

    async function renderHook() {
      const drops = await api('/api/wizard/drops');
      settings.innerHTML = `
        <div class="wizard-stage">
          <button class="btn secondary full" type="button" id="skip-hook">Без хука →</button>
          <section class="stage-section"><p class="eyebrow">Дроп-момент</p><div class="rating-options">${drops.drops.map(d => `<button class="pill ${state.drop === d.time ? 'selected' : ''}" data-drop="${d.time}">${d.time}${d.best ? ' ★' : ''}</button>`).join('')}<button class="pill" data-drop="none">В отрывке нет дропа</button></div></section>
          <section class="stage-section"><p class="eyebrow">Тип хука</p><div class="hook-grid">${['F1 — Звук','F2 — Объект','F3 — Эффект','F4 — Движение','F5 — Мысль'].map(h => `<button class="choice-card ${state.hook?.label === h ? 'selected' : ''}" data-hook="${h}"><strong>${h}</strong><span class="muted">${h.includes('F5') ? 'TTS-вставка' : 'pre-rendered preview'}</span></button>`).join('')}</div><div id="hook-options"></div></section>
        </div>`;
      $('#skip-hook').addEventListener('click', () => { state.hook = null; state.step = 4; render(); });
      $$('[data-drop]').forEach((btn) => btn.addEventListener('click', () => { state.drop = btn.dataset.drop; renderHook(); }));
      $$('[data-hook]').forEach((card) => card.addEventListener('click', () => {
        const label = card.dataset.hook;
        state.hook = { label, drop: state.drop, option: label.includes('F4') ? 'Свайп' : label.includes('F2') ? 'Ромб' : 'Стандарт' };
        setPreview(`Хук · ${label}`, '✦');
        renderHook();
      }));
      const options = $('#hook-options');
      if (state.hook) options.innerHTML = `<div class="notice">Выбрано: ${state.hook.label} · ${state.hook.option}. В бою здесь будут суб-опции F1–F5 и pre-render из S3.</div>`;
    }

    function renderSubtitles() {
      const styles = ['Impulse', 'Jakson', 'Tape', 'Trendy', 'Brat'];
      settings.innerHTML = `<div class="wizard-stage"><section class="stage-section"><p class="eyebrow">Стили субтитров</p><div class="subtitle-grid">${styles.map(style => `<button class="choice-card ${state.subtitles === style ? 'selected' : ''}" data-subtitle="${style}"><strong>${style}</strong></button>`).join('')}</div></section></div>`;
      $$('[data-subtitle]').forEach((card) => card.addEventListener('click', async () => {
        state.subtitles = card.dataset.subtitle;
        await api(`/api/preview/subtitle?style=${encodeURIComponent(state.subtitles)}&lyrics=${encodeURIComponent((state.lyrics || 'first line').split('\n')[0])}`);
        setPreview(`Субтитры · ${state.subtitles}`, 'Aa');
        renderSubtitles();
      }));
    }

    function renderFinal() {
      const maxVibes = state.bgMode === 'color' ? 1 : Math.max(1, state.vibes.length);
      state.videosToGenerate = Math.min(state.videosToGenerate || 1, maxVibes);
      settings.innerHTML = `
        <div class="wizard-stage">
          <section class="stage-section"><p class="eyebrow">Цвета</p><div class="color-input-row"><span>Цвет субтитров</span><input type="color" id="subtitle-color" value="${state.subtitleColor}" /><input id="subtitle-hex" value="${state.subtitleColor}" /><span>Акцентный цвет</span><input type="color" id="accent-color" value="${state.accentColor}" /><input id="accent-hex" value="${state.accentColor}" /></div></section>
          ${state.bgMode !== 'color' ? `<section class="stage-section"><p class="eyebrow">Количество роликов</p><div class="rating-options">${Array.from({ length: maxVibes }, (_, i) => `<button class="pill ${state.videosToGenerate === i + 1 ? 'selected' : ''}" data-count="${i + 1}">${i + 1}</button>`).join('')}</div><p class="muted">Один вайб = один ролик. Будут взяты первые выбранные вайбы.</p></section>` : ''}
          <section class="stage-section"><p class="eyebrow">Итог</p><div class="summary-table"><div><span>Субтитры</span><strong>${state.subtitles}</strong></div><div><span>Тайминг</span><strong>${state.timingMode === 'manual' ? `${state.timingFrom} — ${state.timingTo}` : 'На усмотрение ИИ'}</strong></div><div><span>Исходники</span><strong>${state.bgMode === 'color' ? state.color : (state.vibes.join(', ') || 'не выбрано')}</strong></div><div><span>Хук</span><strong>${state.hook?.label || 'Без хука'}</strong></div><div><span>Цвет субтитров</span><strong>${state.subtitleColor}</strong></div><div><span>Акцентный цвет</span><strong>${state.accentColor}</strong></div><div><span>Роликов</span><strong>${state.videosToGenerate}</strong></div></div><button class="btn secondary" id="reset-wizard" type="button">Начать заново</button></section>
        </div>`;
      $('#subtitle-color').addEventListener('input', (event) => { state.subtitleColor = event.target.value; $('#subtitle-hex').value = state.subtitleColor; });
      $('#subtitle-hex').addEventListener('input', (event) => { if (/^#[0-9a-fA-F]{6}$/.test(event.target.value)) state.subtitleColor = event.target.value; });
      $('#accent-color').addEventListener('input', (event) => { state.accentColor = event.target.value; $('#accent-hex').value = state.accentColor; });
      $('#accent-hex').addEventListener('input', (event) => { if (/^#[0-9a-fA-F]{6}$/.test(event.target.value)) state.accentColor = event.target.value; });
      $$('[data-count]').forEach((btn) => btn.addEventListener('click', () => { state.videosToGenerate = Number(btn.dataset.count); renderFinal(); }));
      $('#reset-wizard').addEventListener('click', () => { localStorage.removeItem('blastWizardState'); location.href = '/app/generate'; });
      setPreview('Финальное превью', '✓');
    }

    async function render() {
      renderStepper();
      next.textContent = state.step === 5 ? 'Запустить →' : 'Далее →';
      if (state.step === 1) await renderTrack();
      if (state.step === 2) await renderBackground();
      if (state.step === 3) await renderHook();
      if (state.step === 4) renderSubtitles();
      if (state.step === 5) renderFinal();
      next.disabled = !validate();
      saveSession();
    }

    next.addEventListener('click', async () => {
      if (!validate()) return;
      if (state.step < 5) {
        if (state.step === 2 && state.bgMode !== 'color') state.videosToGenerate = Math.max(1, Math.min(state.videosToGenerate, state.vibes.length));
        state.step += 1;
        render();
      } else {
        setLoading(next, true);
        try {
          const data = await api('/api/wizard/submit', { method: 'POST', body: JSON.stringify({ projectId: state.projectId, stageData: buildStageData(), videosToGenerate: state.videosToGenerate, idempotencyKey: state.idempotencyKey }) });
          toast('Генерация запущена');
          location.href = data.redirectTo;
        } catch (err) {
          toast(err.message, 'error');
        } finally {
          setLoading(next, false);
        }
      }
    });

    const saved = localStorage.getItem('blastWizardState');
    if (saved) {
      try { Object.assign(state, JSON.parse(saved)); } catch (_) {}
      if (state.track) {
        trackTitle.textContent = state.track.filename;
        trackMeta.textContent = `MP3 · ${Math.floor(state.track.durationS / 60)}:${String(Math.floor(state.track.durationS % 60)).padStart(2, '0')}`;
      }
    }
    render();
  }

  initAuth();
  initProjects();
  initPricing();
  initProfile();
  initProjectDetail();
  initProcessing();
  initWizard();
})();
