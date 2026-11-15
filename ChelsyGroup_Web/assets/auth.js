/* Chelsy Group: registration & login through Supabase Auth. */
const config = window.CHELSY_CONFIG || {};
const configured = /^https:\/\/[^/]+\.supabase\.co\/?$/.test(config.supabaseUrl || '')
  && /^(sb_publishable_|eyJ)/.test(config.supabasePublishableKey || '');
let clientPromise;
function client() {
  if (!configured) throw new Error('Регистрация ещё не подключена администратором.');
  if (!clientPromise) clientPromise = import('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.49.4/+esm')
    .then(({ createClient }) => createClient(config.supabaseUrl, config.supabasePublishableKey, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true }
    }));
  return clientPromise;
}
const $ = (selector, scope = document) => scope.querySelector(selector);
const setStatus = (element, message, success = false) => {
  if (!element) return;
  element.textContent = message;
  element.hidden = !message;
  element.classList.toggle('auth-message-success', success);
};
const humanError = (error) => {
  const value = String(error?.message || error || 'Неизвестная ошибка').toLowerCase();
  if (value.includes('invalid login credentials')) return 'Email или пароль указаны неверно.';
  if (value.includes('email not confirmed')) return 'Сначала подтвердите email по письму.';
  if (value.includes('already registered')) return 'Этот email уже зарегистрирован. Попробуйте войти.';
  if (value.includes('password') && value.includes('weak')) return 'Пароль недостаточно надёжный.';
  if (value.includes('rate limit')) return 'Слишком много попыток. Попробуйте позже.';
  if (value.includes('fetch')) return 'Не удалось подключиться к серверу. Проверьте соединение.';
  return error?.message ? String(error.message) : String(error || 'Ошибка');
};
function stopSubmit(button, busy) {
  if (!button) return;
  button.disabled = busy;
  button.classList.toggle('is-busy', busy);
}
function prepareConfigWarning() {
  if (configured) return;
  document.querySelectorAll('[data-auth-config]').forEach(el => {
    setStatus(el, 'Подключение ещё не завершено. Администратору нужно указать Supabase URL и Publishable Key в assets/config.js.');
  });
}
async function updateHeader() {
  if (!configured) return;
  const supabase = await client();
  const { data: { session } } = await supabase.auth.getSession();
  if (session) document.querySelectorAll('a.header-login').forEach(link => {
    link.href = 'account.html';
    link.textContent = 'Личный кабинет';
  });
}
async function handleRegister(form) {
  const status = $('#register-status');
  const submit = form.querySelector('[type=submit]');
  const password = form.elements.password?.value || '';
  const confirm = form.elements.password_confirm?.value || '';
  if (!form.checkValidity()) { form.reportValidity(); return; }
  if (password.length < 10) { setStatus(status, 'Пароль должен содержать минимум 10 символов.'); return; }
  if (password !== confirm) { setStatus(status, 'Пароли не совпадают.'); return; }
  if (!form.elements.consent.checked) { setStatus(status, 'Требуется согласие с политикой обработки данных.'); return; }
  stopSubmit(submit, true); setStatus(status, '');
  try {
    const supabase = await client();
    const { data, error } = await supabase.auth.signUp({
      email: String(form.elements.email.value).trim().toLowerCase(),
      password,
      options: {
        emailRedirectTo: new URL('account.html', window.location.href).href,
        data: {
          display_name: String(form.elements.name.value).trim(),
          use_case: form.elements.use.value,
          plan: form.elements.plan.value,
          note: String(form.elements.note.value).trim(),
          wants_updates: form.elements.updates.checked
        }
      }
    });
    if (error) throw error;
    if (!data.user) throw new Error('Не удалось создать пользователя.');
    form.hidden = true;
    $('.form-head', form.parentElement)?.setAttribute('hidden', '');
    const success = $('#register-success');
    if (success) success.hidden = false;
    const msg = $('#reg-success-message');
    if (msg) msg.textContent = data.session
      ? 'Аккаунт создан. Вы можете войти в личный кабинет.'
      : 'Проверьте почту и подтвердите email, затем войдите в аккаунт.';
    success?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  } catch (e) { setStatus(status, humanError(e)); }
  finally { stopSubmit(submit, false); }
}
async function handleLogin(form) {
  const status = $('#login-status');
  const submit = form.querySelector('[type=submit]');
  if (!form.checkValidity()) { form.reportValidity(); return; }
  stopSubmit(submit, true); setStatus(status, '');
  try {
    const supabase = await client();
    const { error } = await supabase.auth.signInWithPassword({
      email: String(form.elements.email.value).trim().toLowerCase(),
      password: form.elements.password.value
    });
    if (error) throw error;
    window.location.assign('account.html');
  } catch (e) { setStatus(status, humanError(e)); stopSubmit(submit, false); }
}
async function passwordReset(emailInput, messageElement) {
  const email = String(emailInput?.value || '').trim().toLowerCase();
  if (!email) { setStatus(messageElement, 'Сначала введите email.'); return; }
  try {
    const supabase = await client();
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: new URL('reset.html', window.location.href).href
    });
    if (error) throw error;
    setStatus(messageElement, 'Если такой аккаунт существует, письмо для восстановления отправлено.', true);
  } catch (e) { setStatus(messageElement, humanError(e)); }
}
async function handleAccount() {
  const status = $('#account-status');
  const content = $('#account-content');
  try {
    const supabase = await client();
    const { data: { user }, error } = await supabase.auth.getUser();
    if (error || !user) { window.location.replace('login.html'); return; }
    const { data: profile, error: profileError } = await supabase.from('profiles')
      .select('display_name,plan,use_case,created_at,email_confirmed').eq('id', user.id).single();
    if (profileError) throw profileError;
    $('#account-name').textContent = profile?.display_name || 'Пользователь';
    $('#account-email').textContent = user.email || '';
    $('#account-plan').textContent = ({start:'Start',plus:'Plus',pro:'Pro',team:'Teams'})[profile?.plan] || 'Start';
    $('#account-verified').textContent = profile?.email_confirmed ? 'Подтверждён' : 'Ожидает подтверждения';
    if (content) content.hidden = false;
    setStatus(status, '');
    $('#signout-button')?.addEventListener('click', async () => {
      await supabase.auth.signOut(); window.location.assign('index.html');
    });
  } catch (e) { setStatus(status, humanError(e)); }
}
async function handleContact(form) {
  const status = $('#contact-status');
  const button = form.querySelector('[type=submit]');
  if (!form.checkValidity()) { form.reportValidity(); return; }
  stopSubmit(button, true); setStatus(status, '');
  try {
    const supabase = await client();
    const { data: { user }, error: authError } = await supabase.auth.getUser();
    if (authError || !user) throw new Error('Чтобы отправить сообщение, войдите в аккаунт: login.html');
    const { error } = await supabase.from('inquiries').insert({
      user_id: user.id,
      name: String(form.elements.name.value).trim(),
      email: user.email,
      topic: form.elements.topic.value,
      message: String(form.elements.message.value).trim()
    });
    if (error) throw error;
    form.hidden = true;
    $('.form-head', form.parentElement)?.setAttribute('hidden','');
    const success = $('#contact-success');
    if (success) success.hidden = false;
  } catch (e) { setStatus(status, humanError(e)); }
  finally { stopSubmit(button, false); }
}
async function autofillContact() {
  if (!configured) return;
  const supabase = await client();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) { setStatus($('#contact-status'), 'Для отправки сообщения необходимо войти в аккаунт: используйте ссылку «Войти» в меню.'); return; }
  const form = $('#contact-form');
  if (!form) return;
  form.elements.email.value = user.email || '';
  const { data: profile } = await supabase.from('profiles').select('display_name').eq('id', user.id).maybeSingle();
  if (profile?.display_name) form.elements.name.value = profile.display_name;
}
async function handleRecovery(form) {
  const status = $('#reset-status');
  const button = form.querySelector('[type=submit]');
  if (!form.checkValidity()) { form.reportValidity(); return; }
  if (form.elements.password.value !== form.elements.password_confirm.value) {
    setStatus(status, 'Пароли не совпадают.'); return;
  }
  stopSubmit(button, true);
  try {
    const supabase = await client();
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) throw new Error('Ссылка восстановления недействительна или устарела.');
    const { error } = await supabase.auth.updateUser({ password: form.elements.password.value });
    if (error) throw error;
    setStatus(status, 'Пароль обновлён. Теперь можно перейти в личный кабинет.', true);
  } catch (e) { setStatus(status, humanError(e)); }
  finally { stopSubmit(button, false); }
}
prepareConfigWarning();
updateHeader().catch(() => {});
const registerForm = $('#register-form');
if (registerForm) {
  const plan = new URLSearchParams(window.location.search).get('plan');
  if (['start','plus','pro','team'].includes(plan)) registerForm.elements.plan.value = plan;
  registerForm.addEventListener('submit', e => { e.preventDefault(); handleRegister(registerForm); });
}
const loginForm = $('#login-form');
if (loginForm) loginForm.addEventListener('submit', e => { e.preventDefault(); handleLogin(loginForm); });
$('#reset-request')?.addEventListener('click', () => passwordReset($('#login-email'), $('#login-status')));
if ($('#account-content')) handleAccount();
const contactForm = $('#contact-form');
if (contactForm) {
  autofillContact().catch(() => {});
  contactForm.addEventListener('submit', e => { e.preventDefault(); handleContact(contactForm); });
}
const recoveryForm = $('#recovery-form');
if (recoveryForm) recoveryForm.addEventListener('submit', e => { e.preventDefault(); handleRecovery(recoveryForm); });
