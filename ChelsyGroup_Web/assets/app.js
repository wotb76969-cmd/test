/* Chelsy Group presentation and interface utilities. Auth and messages: auth.js. */
(() => {
  'use strict';
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));
  const navToggle = $('#mobile-toggle');
  const mobileNav = $('#mobile-nav');
  if (navToggle && mobileNav) {
    navToggle.addEventListener('click', () => {
      const isOpen = navToggle.getAttribute('aria-expanded') === 'true';
      navToggle.setAttribute('aria-expanded', String(!isOpen));
      navToggle.setAttribute('aria-label', isOpen ? 'Открыть меню' : 'Закрыть меню');
      mobileNav.hidden = isOpen;
    });
    $$('a', mobileNav).forEach(a => a.addEventListener('click', () => {
      mobileNav.hidden = true;
      navToggle.setAttribute('aria-expanded', 'false');
    }));
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') { mobileNav.hidden = true; navToggle.setAttribute('aria-expanded', 'false'); }
    });
  }
  $$('[data-year]').forEach(el => { el.textContent = String(new Date().getFullYear()); });

  // Pricing UI: the numbers shown here are only hypothetical, not checkout prices.
  const billingSwitch = $('#billing-switch');
  if (billingSwitch) {
    billingSwitch.addEventListener('click', () => {
      const annual = billingSwitch.getAttribute('aria-checked') !== 'true';
      billingSwitch.setAttribute('aria-checked', String(annual));
      $('#annual-label')?.classList.toggle('billing-current', annual);
      $('#monthly-label')?.classList.toggle('billing-current', !annual);
      $$('[data-monthly][data-annual]').forEach(el => {
        el.textContent = Number(el.dataset[annual ? 'annual' : 'monthly']).toLocaleString('ru-RU');
      });
      $$('.price-card .price-period').forEach(el => { el.textContent = '/мес'; });
      $('.pricing-note')?.setAttribute('data-billing', annual ? 'annual' : 'monthly');
    });
  }

  const examples = {
    ideas: ['Помоги придумать название для нового проекта.', 'Начнём с трёх направлений: яркое и смелое, минималистичное и технологичное, тёплое и человечное. Какое настроение вам ближе?'],
    work: ['Помоги подготовить план презентации для команды.', 'Предлагаю начать с цели и аудитории, затем выделить три ключевые идеи, подкрепить их примерами и завершить конкретными следующими шагами.'],
    learning: ['Объясни сложную тему простыми словами.', 'Давай разделим её на небольшие части. Сначала разберём основную идею на понятном примере, а затем постепенно добавим детали.']
  };
  $$('.demo-tab').forEach(tab => tab.addEventListener('click', () => {
    $$('.demo-tab').forEach(t => {
      t.classList.toggle('active', t === tab);
      t.setAttribute('aria-selected', String(t === tab));
    });
    const pair = examples[tab.dataset.demo];
    if (pair) { $('#demo-question').textContent = pair[0]; $('#demo-answer').textContent = pair[1]; }
  }));

  const toast = $('#toast');
  let toastTimeout;
  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => { toast.hidden = true; }, 4500);
  }
  function getEntries(key) {
    try {
      const data = JSON.parse(localStorage.getItem(key) || '[]');
      return Array.isArray(data) ? data : [];
    } catch { return []; }
  }
  function setEntries(key, entries) {
    try { localStorage.setItem(key, JSON.stringify(entries)); return true; }
    catch { showToast('Не удалось сохранить данные. Проверьте настройки браузера.'); return false; }
  }
  function setError(form, name, message) {
    const error = form.querySelector(`[data-error-for="${name}"]`);
    if (error) error.textContent = message;
    const field = form.elements.namedItem(name);
    if (field && field instanceof HTMLElement) field.setAttribute('aria-invalid', message ? 'true' : 'false');
  }
  function validate(form, requiredNames) {
    requiredNames.forEach(name => setError(form, name, ''));
    let firstInvalid = null;
    for (const name of requiredNames) {
      const field = form.elements.namedItem(name);
      if (!field) continue;
      let message = '';
      if (field.type === 'checkbox' && !field.checked) message = 'Подтвердите согласие.';
      else if (!field.value?.trim()) message = 'Заполните это поле.';
      else if (field.validity?.typeMismatch) message = 'Проверьте формат email.';
      else if (field.validity?.tooShort || (field.getAttribute('minlength') && field.value.trim().length < Number(field.getAttribute('minlength')))) message = `Минимум ${field.getAttribute('minlength')} символов.`;
      if (message) { setError(form, name, message); firstInvalid ||= field; }
    }
    if (firstInvalid) { firstInvalid.focus(); return false; }
    return true;
  }
  function bindValidation(form, fields) {
    fields.forEach(name => {
      const field = form.elements.namedItem(name);
      field?.addEventListener('input', () => setError(form, name, ''));
      field?.addEventListener('change', () => setError(form, name, ''));
    });
  }
  $$('[data-reset-form]').forEach(btn => btn.addEventListener('click', () => {
    const form = document.getElementById(btn.dataset.resetForm);
    if (!form) return;
    form.reset();
    form.hidden = false;
    $('.form-head', form.parentElement).hidden = false;
    $('.form-success', form.parentElement).hidden = true;
    $$('[aria-invalid]', form).forEach(el => el.removeAttribute('aria-invalid'));
    $$('.field-error', form).forEach(el => el.textContent = '');
    form.scrollIntoView({behavior:'smooth',block:'center'});
  }));

})();
