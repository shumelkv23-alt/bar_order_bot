(() => {
  const {$,esc,icon,request,notify} = Panel;
  const statuses = [['submitted','Новые'],['accepted','Принятые'],['preparing','Готовятся'],['ready','Готовы']];
  const next = {submitted:['accepted','Принять'],accepted:['preparing','Начать готовить'],preparing:['ready','Готово'],ready:['completed','Выдать гостю']};
  let data = null, online = false, initialized = false, loading = false, pending = false, filter = 'all', timer, audio, sound = false;
  let previous = new Set();
  function age(iso) { return Math.max(0, Math.floor((Date.now() - Panel.isoDate(iso).getTime()) / 60000)); }
  function urgency(iso) { const minutes = age(iso); return minutes >= data.configuration.critical_minutes ? 'critical' : minutes >= data.configuration.warning_minutes ? 'warning' : ''; }
  function ageLabel(iso) { const level = urgency(iso); return `<span class="age" title="С момента создания: ${esc(Panel.date(iso))}">${level ? (level === 'critical' ? 'Долго · ' : 'Ожидает · ') : ''}${age(iso)} мин</span>`; }
  function card(order) {
    const [target,label] = next[order.status];
    const deadline = data.configuration.auto_progress_enabled && order.next_transition_at ? `<p class="auto-deadline">${order.status === 'ready' ? 'Автозакрытие' : 'Автопереход'}: ${esc(Panel.date(order.next_transition_at))}</p>` : '';
    return `<article class="order-card ${urgency(order.created_at)}" data-key="o${order.id}"><div class="card-top"><span class="order-number">${esc(order.public_number)}</span>${ageLabel(order.created_at)}</div><p class="guest">${esc(order.guest)}</p>${order.status_automatically ? '<p class="auto-deadline">Расчётный этап · сотрудник ещё не подтвердил</p>' : ''}<ul class="items">${order.items.map(item => `<li><b>${item.quantity} × ${esc(item.name)}</b>${item.modifiers.length ? `<div class="modifiers">${item.modifiers.map(esc).join(' · ')}</div>` : ''}</li>`).join('')}</ul>${order.comment ? `<p class="order-comment">${esc(order.comment)}</p>` : ''}${deadline}<div class="card-actions"><button class="primary" data-id="${order.id}" data-action="${target}" data-version="${order.version}">${label}${icon('arrow')}</button>${order.status !== 'ready' ? `<button class="cancel" data-id="${order.id}" data-action="cancelled" data-version="${order.version}" aria-label="Отменить заказ ${esc(order.public_number)}">Отменить</button>` : ''}</div></article>`;
  }
  function autoClosedCard(order) {
    return `<article class="prep-batch" data-key="o${order.id}"><strong>${esc(order.public_number)} · ${esc(order.guest)}</strong><span>${order.items.map(item => `${item.quantity} × ${esc(item.name)}`).join(' · ')}</span><button data-id="${order.id}" data-action="confirm-collection" data-version="${order.version}">Подтвердить выдачу</button></article>`;
  }
  function renderPrep() {
    const batches = data.prep_batches || [];
    $('#prep-zone').hidden = !batches.length;
    $('#prep-list').innerHTML = batches.map(batch => `<article class="prep-batch"><strong>${batch.quantity} × ${esc(batch.name)}</strong>${batch.modifiers.length ? `<span>${batch.modifiers.map(esc).join(' · ')}</span>` : ''}${batch.comment ? `<span>К позиции: ${esc(batch.comment)}</span>` : ''}${batch.order_comment ? `<span>К заказу: ${esc(batch.order_comment)}</span>` : ''}<p>${batch.orders.map(row => `${esc(row.number)} (${row.quantity})`).join(' · ')}</p></article>`).join('');
  }
  function specialCard(row) {
    const safeSource = /^https:\/\/www\.thecocktaildb\.com\/drink\/\d+$/.test(row.recipe_source_url || '') ? row.recipe_source_url : '';
    const recipe = row.recipe_name ? `<div class="special-recipe"><strong>Рецепт для проверки: ${esc(row.recipe_name)}</strong><ul>${(row.recipe_ingredients || []).map(value => `<li>${esc(value)}</li>`).join('')}</ul><p>${esc(row.recipe_instructions || '')}</p>${safeSource ? `<a href="${esc(safeSource)}" target="_blank" rel="noopener noreferrer">Источник: TheCocktailDB</a>` : ''}<p>Сверьте пожелания гостя и наличие ингредиентов.</p></div>` : '';
    return `<article class="order-card ${urgency(row.created_at)}" data-key="s${row.id}"><div class="card-top"><span class="badge orange">${row.status === 'accepted' ? 'Принят' : 'Новый запрос'}</span>${ageLabel(row.created_at)}</div><p class="special-title"><b>${row.quantity} × ${esc(row.request_text)}</b></p><p class="guest">${esc(row.guest)}</p>${recipe}${row.source_transcript ? `<p class="special-source">«${esc(row.source_transcript)}»</p>` : ''}<div class="card-actions"><button class="primary" data-special="${row.id}" data-action="${row.status === 'accepted' ? 'fulfilled' : 'accepted'}">${row.status === 'accepted' ? 'Готово' : 'Приготовим'}</button><button class="cancel" data-special="${row.id}" data-action="rejected">Отклонить</button></div></article>`;
  }
  // Only replace changed cards, not the whole queue on every polling tick.
  function reconcile(container, rows, renderCard) {
    const existing = new Map([...container.querySelectorAll('[data-key]')].map(node => [node.dataset.key, node]));
    const keep = new Set();
    for (const row of rows) {
      const html = renderCard(row), template = document.createElement('template'); template.innerHTML = html;
      const fresh = template.content.firstElementChild, key = fresh.dataset.key; keep.add(key);
      let node = existing.get(key);
      if (!node) { node = fresh; container.append(node); }
      else if (node.dataset.render !== html) {
        const focus = document.activeElement;
        const action = node.contains(focus) ? focus.dataset.action : null;
        node.replaceWith(fresh); node = fresh;
        if (action) node.querySelector(`[data-action="${action}"]`)?.focus({preventScroll:true});
      }
      node.dataset.render = html;
    }
    existing.forEach((node,key) => { if (!keep.has(key)) node.remove(); });
    container.querySelector('.queue-empty')?.remove();
    if (!rows.length) container.insertAdjacentHTML('beforeend','<p class="queue-empty">Здесь пока нет заказов</p>');
  }
  function render() {
    if (!data) return;
    $('#event-name').textContent = data.event.name;
    $('#event-state').textContent = data.event.orders_enabled ? 'Приём открыт' : 'Приём на паузе';
    $('#event-state').className = `badge ${data.event.orders_enabled ? 'green' : 'orange'}`;
    $('#total-count').textContent = data.orders.filter(row => row.status !== 'completed').length;
    $('#queue-legend').textContent = `Ожидание: ${data.configuration.warning_minutes}+ мин · долго: ${data.configuration.critical_minutes}+ мин`;
    renderPrep();
    if (!$('#board .column')) $('#board').innerHTML = statuses.map(([status,label]) => `<section class="column" data-status="${status}"><div class="column-heading"><h2>${label}</h2><span class="count"></span></div><div class="cards"></div></section>`).join('');
    statuses.forEach(([status]) => {
      const column = $(`[data-status="${status}"]`), rows = data.orders.filter(row => row.status === status).sort((a,b) => Panel.isoDate(a.created_at) - Panel.isoDate(b.created_at) || a.id - b.id);
      column.hidden = filter !== 'all' && filter !== status; $('.count',column).textContent = rows.length;
      reconcile($('.cards',column),rows,card);
    });
    const autoClosed = data.orders.filter(row => row.status === 'completed' && row.completed_automatically);
    $('#auto-closed-zone').hidden = !autoClosed.length;
    reconcile($('#auto-closed-list'),autoClosed,autoClosedCard);
    const special = data.special_requests || [];
    $('#special-zone').hidden = !special.length; $('#special-count').textContent = special.length;
    reconcile($('#special-list'),special,specialCard);
    lockActions(); $('#board').setAttribute('aria-busy','false');
  }
  function lockActions() { document.querySelectorAll('[data-action]').forEach(button => button.disabled = !online || pending); }
  function play() {
    if (!sound || !audio || audio.state !== 'running') return;
    const oscillator = audio.createOscillator(), gain = audio.createGain(); oscillator.connect(gain); gain.connect(audio.destination);
    oscillator.frequency.setValueAtTime(740,audio.currentTime); gain.gain.setValueAtTime(.12,audio.currentTime); gain.gain.exponentialRampToValueAtTime(.001,audio.currentTime+.3); oscillator.start(); oscillator.stop(audio.currentTime+.3);
  }
  async function load() {
    if (loading || !Panel.active) return; loading = true; clearTimeout(timer);
    try {
      const fresh = await request('/api/v1/staff/orders');
      const keys = new Set([...fresh.orders.map(row => `o${row.id}`),...(fresh.special_requests || []).map(row => `s${row.id}`)]);
      if (initialized && data?.event.id === fresh.event.id && [...keys].some(key => !previous.has(key))) { play(); $('#queue-status').textContent = 'Поступил новый заказ или особый запрос'; }
      previous = keys; initialized = true; online = true; data = fresh; Panel.error(); Panel.connection('Обновлено ' + new Date().toLocaleTimeString('ru-RU'), 'online'); render();
    } catch (problem) {
      online = false; initialized = false; lockActions(); Panel.connection('Данные не обновляются', 'error');
      const absent = problem.status === 404 || /active event|активного мероприятия/i.test(problem.message);
      if (absent) { data = null; $('#event-name').textContent = 'Нет активного мероприятия'; $('#event-state').textContent = ''; $('#total-count').textContent = ''; $('#prep-zone').hidden = true; $('#auto-closed-zone').hidden = true; $('#special-zone').hidden = true; $('#board').innerHTML = '<div class="empty wide"><strong>Смена ещё не началась</strong>Администратор должен активировать мероприятие. Очередь появится автоматически.</div>'; Panel.error(); }
      else Panel.error(problem.message + (data ? ' Показаны последние полученные данные; действия временно отключены.' : ''));
      $('#board').setAttribute('aria-busy','false');
    } finally { loading = false; timer = setTimeout(load,data?.configuration.poll_interval_ms || 2500); }
  }
  async function mutate(button) {
    if (pending || !online) return;
    const special = button.dataset.special, action = button.dataset.action, id = special || button.dataset.id;
    let decision = {};
    if (action === 'cancelled' || action === 'rejected') decision = await Panel.confirm(special ? 'Отклонить особый запрос?' : 'Отменить заказ?', 'Гость получит уведомление. Это действие нельзя отменить.', {label:special ? 'Отклонить' : 'Отменить заказ',danger:true,note:Boolean(special)});
    if (!decision || !online || pending) return;
    pending = true; lockActions();
    try {
      await request(special ? `/api/v1/staff/special-requests/${id}` : action === 'confirm-collection' ? `/api/v1/staff/orders/${id}/confirm-collection` : `/api/v1/staff/orders/${id}/status`, {method:action === 'confirm-collection' ? 'POST' : 'PATCH',body:JSON.stringify(special ? {status:action,note:decision.note || ''} : action === 'confirm-collection' ? {expected_version:Number(button.dataset.version)} : {status:action,expected_version:Number(button.dataset.version)})});
      notify('Статус обновлён');
    } catch (problem) { notify(problem.status === 409 ? 'Заказ уже изменился. Обновляем очередь.' : problem.message, true); }
    finally { pending = false; online = false; lockActions(); await load(); }
  }
  document.addEventListener('click', event => {const button = event.target.closest('[data-action]');if (button) mutate(button);});
  document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {filter=button.dataset.filter; document.querySelectorAll('[data-filter]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));render();}));
  $('#sound-toggle').addEventListener('click',async () => {try {audio ||= new (window.AudioContext || window.webkitAudioContext)(); await audio.resume(); sound = !sound; $('#sound-toggle').setAttribute('aria-pressed',String(sound)); $('#sound-toggle span').textContent = sound ? 'Звук включён' : 'Включить звук'; if(sound) play();} catch {notify('Браузер не разрешил звук. Очередь продолжает обновляться.',true);}});
  $('#refresh').addEventListener('click',load);
  Panel.start(load);
})();
