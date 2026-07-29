(() => {
  'use strict';

  const STORAGE_KEY = 'blast_cookie_consent';
  const VERSION = 1;
  const banner = document.querySelector('[data-cookie-banner]');
  const dialog = document.querySelector('[data-cookie-dialog]');
  if (!banner || !dialog) throw new Error('[landing] cookie consent markup is missing');

  let lastFocused = null;

  function readConsent() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
      if (!parsed || parsed.version !== VERSION) return null;
      return {
        version: VERSION,
        necessary: true,
        analytics: parsed.analytics === true,
        marketing: parsed.marketing === true,
        updatedAt: String(parsed.updatedAt || '')
      };
    } catch (error) {
      console.warn('[landing] invalid cookie consent state', error);
      return null;
    }
  }

  function activateDeferredScripts(consent) {
    document.querySelectorAll('script[type="text/plain"][data-consent-category]').forEach(source => {
      const category = source.dataset.consentCategory;
      if (!consent[category] || source.dataset.activated === '1') return;
      const script = document.createElement('script');
      Array.from(source.attributes).forEach(attribute => {
        if (attribute.name === 'type' || attribute.name === 'data-consent-category' || attribute.name === 'data-src') return;
        script.setAttribute(attribute.name, attribute.value);
      });
      if (source.dataset.src) script.src = source.dataset.src;
      else script.textContent = source.textContent;
      source.dataset.activated = '1';
      source.after(script);
    });
  }

  function saveConsent(next) {
    const consent = {
      version: VERSION,
      necessary: true,
      analytics: next.analytics === true,
      marketing: next.marketing === true,
      updatedAt: new Date().toISOString()
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(consent));
    banner.hidden = true;
    closeDialog();
    activateDeferredScripts(consent);
    document.dispatchEvent(new CustomEvent('blast:consentchange', { detail: consent }));
    return consent;
  }

  function syncInputs(consent) {
    dialog.querySelector('[data-cookie-category="analytics"]').checked = consent?.analytics === true;
    dialog.querySelector('[data-cookie-category="marketing"]').checked = consent?.marketing === true;
  }

  function openDialog() {
    lastFocused = document.activeElement;
    syncInputs(readConsent());
    dialog.hidden = false;
    dialog.setAttribute('aria-hidden', 'false');
    document.body.classList.add('cookie-dialog-open');
    dialog.querySelector('[data-cookie-close]').focus();
  }

  function closeDialog() {
    dialog.hidden = true;
    dialog.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('cookie-dialog-open');
    if (lastFocused instanceof HTMLElement) lastFocused.focus();
    lastFocused = null;
  }

  function rejectOptional() { saveConsent({ analytics: false, marketing: false }); }
  function acceptAll() { saveConsent({ analytics: true, marketing: true }); }
  function saveChoices() {
    saveConsent({
      analytics: dialog.querySelector('[data-cookie-category="analytics"]').checked,
      marketing: dialog.querySelector('[data-cookie-category="marketing"]').checked
    });
  }

  document.querySelectorAll('[data-cookie-accept]').forEach(button => button.addEventListener('click', acceptAll));
  document.querySelectorAll('[data-cookie-reject]').forEach(button => button.addEventListener('click', rejectOptional));
  document.querySelectorAll('[data-cookie-open-settings], [data-cookie-settings]').forEach(button => button.addEventListener('click', openDialog));
  document.querySelectorAll('[data-cookie-close]').forEach(button => button.addEventListener('click', closeDialog));
  document.querySelector('[data-cookie-save]').addEventListener('click', saveChoices);
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !dialog.hidden) closeDialog(); });

  const consent = readConsent();
  banner.hidden = consent !== null;
  if (consent) activateDeferredScripts(consent);

  window.BLAST_CONSENT = { get: readConsent, openSettings: openDialog };
})();