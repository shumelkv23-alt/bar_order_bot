/* Shared shell. Credentials stay in sessionStorage, never in rendered links. */
window.Panel = (() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="/static/panel-icons.svg#${name}"></use></svg>`;
  const isoDate = value => new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : value + 'Z');
  const date = value => isoDate(value).toLocaleString('ru-RU', {day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
  let token = sessionStorage.getItem('barPanelToken') || '';
  let active = false;
  let role = '';
  const legacyKeys = ['adminToken', 'staffToken', 'analyticsToken'];
  const translations = {'No active event':'Нет активного мероприятия', 'Event not found':'Мероприятие не найдено', 'Event code already exists':'Этот код мероприятия уже используется', 'ends_at must be later than starts_at':'Окончание должно быть позже начала', 'max_same_item cannot exceed max_items_per_order':'Лимит одной позиции не может превышать общий лимит'};
  function notify(message, error = false) {
    const element = $('#toast'); element.textContent = message; element.className = `toast${error ? ' error' : ''}`; element.hidden = false;
    clearTimeout(notify.timer); notify.timer = setTimeout(() => element.hidden = true, error ? 8000 : 4000);
  }
  function connection(message, state = '') { $('#connection').textContent = message; $('#connection').className = `connection ${state}`; }
  function error(message = '') { $('#page-error').textContent = message; $('#page-error').hidden = !message; }
  function logout() {
    active = false; token = ''; sessionStorage.removeItem('barPanelToken'); legacyKeys.forEach(key => localStorage.removeItem(key));
    location.replace(location.pathname);
  }
  async function request(path, options = {}) {
    let response;
    try {
      const authHeader = {'staff':'X-Staff-Token','admin':'X-Admin-Token','analytics':'X-Owner-Token'}[document.body.dataset.panel];
      response = await fetch(path, {...options, signal:options.signal || AbortSignal.timeout(15000), headers:{'Content-Type':'application/json', [path === '/api/v1/session' ? 'X-Access-Token' : authHeader]:token, ...options.headers}});
    } catch (cause) {
      const problem = new Error(options.method && options.method !== 'GET' ? 'Связь прервалась. Результат действия неизвестен — обновите данные перед повтором.' : 'Нет связи с сервером. Проверьте подключение и попробуйте снова.');
      problem.uncertain = Boolean(options.method && options.method !== 'GET'); throw problem;
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      let message = typeof body.detail === 'string' ? (translations[body.detail] || body.detail) : body.detail?.message || `Проверьте поля формы (ошибка ${response.status}).`;
      if ([401,403].includes(response.status)) {
        message = 'Ключ не подходит или доступ изменился. Введите ключ вашей роли.';
        active = false; sessionStorage.removeItem('barPanelToken'); $('#app-shell').hidden = true; $('#login-screen').hidden = false;
        $('#login-error').textContent = message; $('#login-error').hidden = false;
      }
      const problem = new Error(message); problem.status = response.status; problem.detail = body.detail;
      problem.uncertain = response.status >= 500 && Boolean(options.method && options.method !== 'GET');
      if(problem.uncertain) problem.message = 'Сервер вернул ошибку. Результат действия неизвестен — обновите данные перед повтором.';
      throw problem;
    }
    if (options.raw) return response;
    return response.status === 204 ? null : response.json();
  }
  function confirm(title, description, {label = 'Подтвердить', note = false, danger = false} = {}) {
    const dialog = $('#confirm-dialog'); $('#confirm-title').textContent = title; $('#confirm-description').textContent = description;
    $('#confirm-note-label').hidden = !note; $('#confirm-note').value = '';
    $('#confirm-submit').textContent = label; $('#confirm-submit').className = danger ? 'danger' : 'primary'; dialog.returnValue = ''; dialog.showModal();
    return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm' ? {note:$('#confirm-note').value.trim()} : null), {once:true}));
  }
  async function start(ready) {
    $('#logout').addEventListener('click', logout);
    async function login() {
      const button = $('#login-form button'); button.disabled = true; $('#login-error').hidden = true;
      try {
        const session = await request('/api/v1/session');
        role = session.role;
        const panel = document.body.dataset.panel;
        if (!session.permissions.includes(panel)) {
          const destinations = {staff:'Очередь бармена',admin:'Управление баром',analytics:'Аналитика'};
          $('#login-error').textContent = 'Для вашей роли откройте: ' + session.permissions.map(name => destinations[name]).join(', ') + '.';
          $('#login-error').hidden = false; return;
        }
        sessionStorage.setItem('barPanelToken', token); legacyKeys.forEach(key => localStorage.removeItem(key));
        const roles = {admin:'Администратор',bartender:'Бармен',owner:'Владелец',analyst:'Аналитик'};
        $('#session-role').textContent = roles[session.role];
        document.querySelectorAll('[data-access]').forEach(link => { link.hidden = !session.permissions.includes(link.dataset.access); if (link.dataset.access === panel) link.setAttribute('aria-current', 'page'); });
        active = true; $('#app-shell').hidden = false; $('#login-screen').hidden = true; $('#access-token').value = '';
        connection('Подключено', 'online'); await ready();
      } catch (problem) {
        if (active) { error(problem.message); connection('Ошибка загрузки', 'error'); }
        else { $('#login-error').hidden = false; $('#login-error').textContent = problem.message; }
      } finally { button.disabled = false; }
    }
    $('#login-form').addEventListener('submit', event => {event.preventDefault();token = $('#access-token').value.trim();login();});
    const params = new URLSearchParams(location.search);
    const legacyKey = {staff:'staffToken',admin:'adminToken',analytics:'analyticsToken'}[document.body.dataset.panel];
    token = params.get('token') || token || localStorage.getItem(legacyKey) || '';
    if (params.has('token')) { params.delete('token'); history.replaceState(null, '', location.pathname + (params.size ? '?' + params : '') + location.hash); }
    if (token) await login();
  }
  function download(content, filename, type) { const url = URL.createObjectURL(new Blob([content], {type})); const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  return {$,esc,icon,date,isoDate,request,notify,connection,error,confirm,start,download,get active(){return active;},get role(){return role;}};
})();
