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

  /* ─── Example videos autoplay only in viewport ───────────── */
  const exampleVideos = Array.from(document.querySelectorAll('.example-video'));
  if (exampleVideos.length) {
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const safePlay = (video) => {
      if (!video) return;
      if (prefersReducedMotion) return;
      const promise = video.play();
      if (promise && typeof promise.catch === 'function') {
        promise.catch(() => {});
      }
    };

    if (!('IntersectionObserver' in window)) {
      exampleVideos.forEach(safePlay);
    } else {
      const videoObserver = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          const video = entry.target;
          if (!(video instanceof HTMLVideoElement)) return;
          if (entry.isIntersecting) {
            safePlay(video);
          } else {
            video.pause();
          }
        });
      }, { threshold: 0.5 });

      exampleVideos.forEach((video) => {
        videoObserver.observe(video);
      });
    }
  }

})();
