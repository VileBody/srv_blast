/* ═══════════════════════════════════════════════════════════════
   Blast Landing — main.js
   • Burger / mobile menu toggle
   • Smooth scroll for nav links (closes mobile menu)
   • Active nav link on scroll (IntersectionObserver)
   ═══════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ─── Burger menu ─────────────────────────────────────────── */
  const burger     = document.querySelector('.burger');
  const mobileMenu = document.querySelector('.mobile-menu');

  if (burger && mobileMenu) {
    burger.addEventListener('click', () => {
      const isOpen = burger.classList.toggle('open');
      mobileMenu.classList.toggle('open', isOpen);
      burger.setAttribute('aria-expanded', String(isOpen));
      mobileMenu.setAttribute('aria-hidden', String(!isOpen));
    });

    // Close menu when any link inside is clicked
    mobileMenu.querySelectorAll('a').forEach(link => {
      link.addEventListener('click', () => {
        burger.classList.remove('open');
        mobileMenu.classList.remove('open');
        burger.setAttribute('aria-expanded', 'false');
        mobileMenu.setAttribute('aria-hidden', 'true');
      });
    });

    // Close on outside click
    document.addEventListener('click', (e) => {
      if (!burger.contains(e.target) && !mobileMenu.contains(e.target)) {
        burger.classList.remove('open');
        mobileMenu.classList.remove('open');
        burger.setAttribute('aria-expanded', 'false');
        mobileMenu.setAttribute('aria-hidden', 'true');
      }
    });
  }

  /* ─── Smooth scroll ───────────────────────────────────────── */
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', (e) => {
      const targetId = anchor.getAttribute('href');
      if (targetId === '#') return;
      const target = document.querySelector(targetId);
      if (!target) return;
      e.preventDefault();
      const navHeight = document.querySelector('.navbar-wrap')?.offsetHeight || 0;
      const top = target.getBoundingClientRect().top + window.scrollY - navHeight - 16;
      window.scrollTo({ top, behavior: 'smooth' });
    });
  });

  /* ─── Active nav link (IntersectionObserver) ──────────────── */
  const sections  = document.querySelectorAll('section[id]');
  const navLinks  = document.querySelectorAll('.navbar-links a[href^="#"]');

  if (sections.length && navLinks.length) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const id = entry.target.id;
          navLinks.forEach(link => {
            link.classList.toggle('active', link.getAttribute('href') === `#${id}`);
          });
        }
      });
    }, { rootMargin: '-30% 0px -60% 0px' });

    sections.forEach(s => observer.observe(s));
  }

  /* ─── Add .active styles inline so no extra CSS needed ───── */
  const styleEl = document.createElement('style');
  styleEl.textContent = `.navbar-links a.active { opacity: 1; color: #A080FF; }`;
  document.head.appendChild(styleEl);

  const mediaConfig = window.BLAST_MEDIA_CONFIG;
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function resolveMediaUrl(mediaKey) {
    if (!mediaConfig || typeof mediaConfig !== 'object') {
      throw new Error('Missing BLAST_MEDIA_CONFIG');
    }
    const baseUrl = String(mediaConfig.baseUrl || '').trim().replace(/\/+$/, '');
    if (!baseUrl) {
      throw new Error('BLAST_MEDIA_CONFIG.baseUrl is required');
    }
    const files = mediaConfig.files;
    if (!files || typeof files !== 'object') {
      throw new Error('BLAST_MEDIA_CONFIG.files is required');
    }
    const fileName = String(files[mediaKey] || '').trim();
    if (!fileName) {
      throw new Error(`Missing media mapping for key: ${mediaKey}`);
    }
    return `${baseUrl}/${fileName}`;
  }

  function safePlay(video) {
    if (!video || prefersReducedMotion) return;
    const promise = video.play();
    if (promise && typeof promise.catch === 'function') {
      promise.catch(() => {});
    }
  }

  /* ─── Hero video source (S3) ─────────────────────────────── */
  const heroVideo = document.querySelector('.hero-video[data-media-key]');
  if (heroVideo) {
    try {
      heroVideo.src = resolveMediaUrl(heroVideo.dataset.mediaKey);
      heroVideo.load();
      safePlay(heroVideo);
    } catch (err) {
      console.error('[landing] hero media init failed', err);
    }
  }

  /* ─── How It Works — sticky 3-state scroll ────────────────── */
  const stepsWrapper = document.querySelector('.steps-scroll-wrapper');
  const stepsLines   = document.querySelectorAll('.steps-line');
  const stepsStages  = document.querySelectorAll('.steps-stage');

  const stepsCard = document.querySelector('.steps-card');
  const statOverlayMob = document.querySelector('.steps-stat-overlay--mob');
  function setStep(index) {
    stepsLines.forEach((el, i)  => el.classList.toggle('active', i === index));
    stepsStages.forEach(el => {
      const step = parseInt(el.dataset.step, 10);
      el.classList.toggle('active', step === index);
    });
    if (statOverlayMob && window.innerWidth <= 768) {
      statOverlayMob.style.opacity = index === 2 ? '1' : '0';
      statOverlayMob.style.pointerEvents = index === 2 ? 'auto' : 'none';
    }
  }

  function onStepsScroll() {
    if (!stepsWrapper) return;
    const rect     = stepsWrapper.getBoundingClientRect();
    const scrolled = -rect.top;                          // px scrolled past top of wrapper
    const total    = rect.height - window.innerHeight;   // total scrollable distance
    const progress = Math.max(0, Math.min(1, scrolled / total));
    const step     = progress < 0.33 ? 0 : progress < 0.66 ? 1 : 2;
    setStep(step);
  }

  if (stepsWrapper) {
    window.addEventListener('scroll', onStepsScroll, { passive: true });
    onStepsScroll(); // init on load
  }

  /* ─── 1. Reveal on scroll ────────────────────────────────── */
  const revealTargets = document.querySelectorAll(
    '.section-head, .feat-col, .example-col, .steps-card, .cta-head, .cta-card'
  );
  const revealObs = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('revealed');
        revealObs.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });
  revealTargets.forEach((el, i) => {
    el.classList.add('reveal');
    el.style.transitionDelay = (i % 3) * 80 + 'ms';
    revealObs.observe(el);
  });

  /* ─── 3. Counter animation ───────────────────────────────── */
  function animateCount(el, target, suffix, duration) {
    const start = performance.now();
    (function tick(now) {
      const p = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * eased) + suffix;
      if (p < 1) requestAnimationFrame(tick);
    })(start);
  }
  const counterEls = document.querySelectorAll('.social-count .gradient-text');
  const counterObs = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const el = entry.target;
      const raw = el.textContent.trim();
      const num = parseInt(raw);
      const suffix = raw.replace(/[0-9]/g, '');
      animateCount(el, num, suffix, 1400);
      counterObs.unobserve(el);
    });
  }, { threshold: 0.5 });
  counterEls.forEach(el => counterObs.observe(el));

  /* ─── 5. Typing effect on "— вирусным" ───────────────────── */
  const typingEl = document.querySelector('.hero-h1-italic');
  if (typingEl) {
    const hasBr = !!typingEl.querySelector('br');
    const fullText = typingEl.textContent.replace(/\n/g, '').trim();
    const textNode = document.createTextNode('');
    typingEl.innerHTML = '';
    typingEl.appendChild(textNode);
    if (hasBr) typingEl.appendChild(document.createElement('br'));
    const caret = document.createElement('span');
    caret.className = 'typing-cursor';
    typingEl.parentElement.insertBefore(caret, typingEl.nextSibling);
    let i = 0;
    function typeNext() {
      if (i < fullText.length) {
        textNode.textContent += fullText[i++];
        setTimeout(typeNext, fullText[i - 1] === ' ' ? 40 : 75);
      } else {
        setTimeout(() => caret.remove(), 1200);
      }
    }
    setTimeout(typeNext, 400);
  }

/* ─── Examples: stream from S3 + lazy viewport autoplay ──── */
  const exampleSlides = Array.from(document.querySelectorAll('.example-slide[data-media-key]'));
  const exampleVideos = [];
  exampleSlides.forEach((slide) => {
    const mediaKey = String(slide.dataset.mediaKey || '').trim();
    const video = slide.querySelector('video');
    if (!mediaKey || !(video instanceof HTMLVideoElement)) return;
    try {
      const url = resolveMediaUrl(mediaKey);
      slide.dataset.mediaUrl = url;
      video.dataset.mediaUrl = url;
      video.preload = 'none';
      exampleVideos.push(video);
    } catch (err) {
      console.error('[landing] example media init failed', { mediaKey, err });
    }
  });

  if (exampleVideos.length) {
    const ensureLoaded = (video) => {
      if (video.dataset.loaded === '1') return;
      const mediaUrl = String(video.dataset.mediaUrl || '').trim();
      if (!mediaUrl) return;
      video.src = mediaUrl;
      video.load();
      video.dataset.loaded = '1';
    };

    if (!('IntersectionObserver' in window)) {
      exampleVideos.forEach((video) => {
        ensureLoaded(video);
        safePlay(video);
      });
    } else {
      const previewObserver = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          const video = entry.target;
          if (!(video instanceof HTMLVideoElement)) return;
          if (entry.isIntersecting) {
            ensureLoaded(video);
            safePlay(video);
          } else {
            video.pause();
          }
        });
      }, { threshold: 0.35, rootMargin: '180px 0px' });

      exampleVideos.forEach((video) => previewObserver.observe(video));
    }
  }

/* ─── Examples: highlight centered card on mobile ────────── */
  if (window.innerWidth <= 768) {
    const exScroll = document.querySelector('.examples-scroll');
    if (exScroll) {
      const updateCentered = () => {
        const cols = exScroll.querySelectorAll('.example-col');
        const center = exScroll.scrollLeft + exScroll.offsetWidth / 2;
        let closest = null;
        let minDist = Infinity;
        cols.forEach(col => {
          const colCenter = col.offsetLeft + col.offsetWidth / 2;
          const scrollCenter = exScroll.scrollLeft + exScroll.offsetWidth / 2;
          const dist = Math.abs(scrollCenter - colCenter);
          if (dist < minDist) { minDist = dist; closest = col; }
        });
        cols.forEach(col => col.classList.toggle('is-centered', col === closest));
      };
      exScroll.addEventListener('scroll', updateCentered, { passive: true });
      exScroll.addEventListener('scrollend', updateCentered);
      // Center 3rd card — overflow starts hidden so scrollLeft won't cause page jump
      const centerThird = () => {
        const thirdCol = exScroll.querySelectorAll('.example-col')[2];
        if (!thirdCol) return;
        const scrollPad = parseFloat(getComputedStyle(exScroll).paddingLeft) || 0;
        // Set scrollLeft while overflow is hidden (no page jump possible)
        exScroll.style.overflowX = 'hidden';
        exScroll.scrollLeft = thirdCol.offsetLeft - scrollPad;
        // Re-enable scrolling and snap on next frame
        requestAnimationFrame(() => {
          exScroll.style.overflowX = 'auto';
          exScroll.style.scrollSnapType = 'x mandatory';
          updateCentered();
        });
      };
      centerThird();
      // Re-center after fonts/images load (layout may shift)
      window.addEventListener('load', () => {
        centerThird();
      });
    }
  }

/* ─── Video modal ─────────────────────────────────────────── */
  const videoModal   = document.getElementById('videoModal');
  const modalPlayer  = videoModal && videoModal.querySelector('.video-modal-player');
  const modalClose   = videoModal && videoModal.querySelector('.video-modal-close');
  const modalBackdrop = videoModal && videoModal.querySelector('.video-modal-backdrop');

  function openVideoModal(src, portrait) {
    modalPlayer.src = src;
    videoModal.classList.toggle('video-modal--portrait', !!portrait);
    videoModal.classList.add('open');
    videoModal.setAttribute('aria-hidden', 'false');
    modalPlayer.play();
  }

  function closeVideoModal() {
    videoModal.classList.remove('open', 'video-modal--portrait');
    videoModal.setAttribute('aria-hidden', 'true');
    modalPlayer.pause();
    modalPlayer.src = '';
  }

  if (videoModal) {
    document.querySelectorAll('.example-slide[data-media-key]').forEach(slide => {
      slide.addEventListener('click', () => {
        const mediaKey = String(slide.dataset.mediaKey || '').trim();
        if (!mediaKey) return;
        try {
          openVideoModal(resolveMediaUrl(mediaKey), slide.dataset.portrait);
        } catch (err) {
          console.error('[landing] modal media resolve failed', { mediaKey, err });
        }
      });
    });
    modalClose.addEventListener('click', closeVideoModal);
    modalBackdrop.addEventListener('click', closeVideoModal);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeVideoModal(); });
  }

})();
