(() => {
  const {$,esc,icon,request,notify} = Panel;
  const state = {events:[],categories:[],modifiers:[],items:[],activeEvent:null,activeMenu:[]};
  const sections = {
    menu:['Меню мероприятия','Быстро меняйте доступность. Стоп-лист действует только на активном мероприятии.','Новая позиция'],
    events:['Мероприятия','Одна активная смена. Остальные мероприятия сохраняют свою историю.','Новое мероприятие'],
    items:['Каталог','Все напитки и еда. Архив скрывает позицию во всех гостевых меню.','Новая позиция'],
    categories:['Категории','Группы позиций и их порядок в меню.','Новая категория'],
    modifiers:['Модификаторы','Лёд, добавки и варианты — в едином справочнике.','Новый модификатор']
  };
  const labels = {draft:'Черновик',active:'Активно',closed:'Завершено'};
  let section='menu', editing=null, busy=false, loaded=false, dirty=false, initialForm='', savedItemId=null;
  const endpoint = kind => '/api/v1/admin/' + (kind==='items' ? 'menu-items' : kind);
  function formSnapshot(){return JSON.stringify([...new FormData($('#editor-form')).entries()]);}
  function lock() { document.querySelectorAll('#create,#toggle-orders,[data-edit],[data-event-action],[data-available]').forEach(button => button.disabled=busy || !loaded); $('#toggle-orders').disabled ||= !state.activeEvent; $('#editor-save').disabled=busy || !loaded; }
  async function load() {
    const [events,catalog] = await Promise.all([request('/api/v1/admin/events'),request('/api/v1/admin/catalog')]);
    const active = events.events.find(event => event.status==='active') || null;
    const menu = active ? await request('/api/v1/admin/menu') : {items:[]};
    state.events=events.events; Object.assign(state,catalog); state.activeEvent=active; state.activeMenu=menu.items;
    loaded=true; Panel.error(); Panel.connection('Обновлено '+new Date().toLocaleTimeString('ru-RU'),'online');
    const category=$('#category-filter').value;
    $('#category-filter').innerHTML='<option value="">Все категории</option>'+state.categories.map(row => `<option value="${row.id}">${esc(row.name_ru)}</option>`).join(''); $('#category-filter').value=category;
    $('#active-event-name').textContent=active?.name || 'Нет активного мероприятия';
    $('#active-event-state').textContent=active ? (active.orders_enabled ? 'Приём открыт' : 'Приём на паузе') : 'Смена не началась';
    $('#active-event-state').className=`badge ${active?.orders_enabled ? 'green' : 'orange'}`;
    $('#toggle-orders').textContent=active?.orders_enabled ? 'Приостановить приём' : 'Возобновить приём';
    $('#admin-content').setAttribute('aria-busy','false');render();lock();
  }
  async function refresh() { if(busy)return;busy=true;lock();try {await load();} catch(problem){loaded=false;Panel.error(problem.message);Panel.connection('Ошибка обновления','error');}finally{busy=false;lock();} }
  function availability(item) {
    if(item.is_archived)return ['archived','В архиве',''];
    const category=state.categories.find(row=>row.id===item.category_id);
    if(category && !category.is_active)return ['stopped','Категория выключена','orange'];
    const link=item.events?.find(row=>row.event_id===state.activeEvent?.id);
    return link?.is_available ? ['available','В меню','green'] : ['stopped',link ? 'В стоп-листе' : 'Не добавлена','orange'];
  }
  function row(item) {
    const edit=`<button class="row-action" data-edit="${item.id}">Изменить ${icon('arrow')}</button>`;
    if(section==='events')return `<article class="data-row"><div><strong>${esc(item.name)}</strong><p>${esc(Panel.date(item.starts_at))} — ${esc(Panel.date(item.ends_at))}</p><small>${esc(item.code)} · ${item.menu_items_count} позиций</small></div><span class="badge ${item.status==='active'?'green':''}">${labels[item.status] || esc(item.status)}</span><div class="toolbar">${edit}<button class="row-action ${item.status==='active'?'danger':''}" data-event-action="${item.status==='active'?'close':'activate'}" data-id="${item.id}">${item.status==='active'?'Закрыть':'Активировать'}</button></div></article>`;
    if(section==='categories'||section==='modifiers')return `<article class="data-row"><div><strong>${esc(item.name_ru)}</strong><p>${esc(item.name_en)}${section==='modifiers'?' · '+esc(({ice:'Лёд',extra:'Добавка',variant:'Вариант'})[item.kind] || item.kind):''}</p></div><span class="badge ${item.is_active?'green':''}">${item.is_active?'Активна':'Выключена'}</span>${edit}</article>`;
    const catalogItem=section==='menu' ? state.items.find(row=>row.id===item.id) || item : item;
    const status=availability(catalogItem), category=state.categories.find(row=>row.id===catalogItem.category_id)?.name_ru || item.category || '';
    return `<article class="data-row"><div><strong>${esc(item.name_ru)}</strong><p>${esc(category)} <span aria-hidden="true">·</span> ${esc(item.name_en)}</p></div><span class="badge ${status[2]}">${status[1]}</span>${section==='menu'?`<button class="row-action" data-available="${item.id}" data-value="${!item.is_available}">${item.is_available?'В стоп-лист':'Вернуть в меню'}</button>`:edit}</article>`;
  }
  function render() {
    const [title,description,create]=sections[section];$('#section-title').textContent=title;$('#section-description').textContent=description;$('#create span').textContent=create;
    const isMenu=['menu','items'].includes(section);$('#category-filter').hidden=!isMenu;$('#availability-filter').hidden=!isMenu;$('#search').placeholder=isMenu?'Найти позицию…':'Поиск по названию…';
    const search=$('#search').value.trim().toLocaleLowerCase(), category=$('#category-filter').value, filter=$('#availability-filter').value;
    const all=section==='menu'?state.activeMenu:state[section];
    const rows=all.filter(item=> {
      if(!`${item.name_ru || item.name} ${item.name_en || ''} ${item.code || ''}`.toLocaleLowerCase().includes(search))return false;
      const catalogItem=section==='menu'?state.items.find(row=>row.id===item.id) || item:item;
      return !isMenu || ((!category || String(catalogItem.category_id)===category) && (!filter || availability(catalogItem)[0]===filter));
    });
    $('#list-count').textContent=`Показано ${rows.length} из ${all.length}`;
    $('#admin-list').innerHTML=rows.length?rows.map(row).join(''):`<div class="empty"><strong>${section==='menu'&&!state.activeEvent?'Сначала начните мероприятие':all.length?'Ничего не найдено':'Здесь пока пусто'}</strong>${section==='menu'&&!state.activeEvent?'Откройте раздел «Мероприятия» и активируйте нужное.':all.length?'Измените поиск или сбросьте фильтры.':'Добавьте первую запись кнопкой вверху.'}</div>`;
    lock();
  }
  function switchSection(next) {section=next;$('#search').value='';$('#category-filter').value='';$('#availability-filter').value='';document.querySelectorAll('[data-section]').forEach(button=>{const selected=button.dataset.section===section;button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;});$('#admin-content').setAttribute('aria-labelledby','tab-'+section);render();}
  function openEditor(id) {
    const kind=section==='menu'?'items':section, record=state[kind].find(item=>item.id===Number(id)) || {};
    if(kind==='items'&&!state.categories.length){notify('Сначала создайте категорию.',true);switchSection('categories');return;}
    editing={kind,record,eventId:state.activeEvent?.id};savedItemId=null;
    $('#editor-title').textContent=record.id ? (record.name_ru || record.name) : sections[kind][2];
    $('#editor-fields').innerHTML=AdminForms.fields(kind,record,state);$('#editor-error').hidden=true;$('#editor-save').textContent='Сохранить';initialForm=formSnapshot();dirty=false;$('#editor').showModal();
  }
  async function closeEditor() {if(busy)return;if(dirty && !await Panel.confirm('Закрыть без сохранения?','Внесённые изменения будут потеряны.',{label:'Закрыть форму',danger:true}))return;$('#editor').close();}
  async function save(event) {
    event.preventDefault();if(busy || !loaded)return;
    const {kind,record,eventId}=editing;
    try {
      const body=AdminForms.payload(kind,$('#editor-form'),record);busy=true;lock();$('#editor-error').hidden=true;
      const id=record.id || savedItemId;
      const saved=await request(id ? `${endpoint(kind)}/${id}`:endpoint(kind),{method:id?'PATCH':'POST',body:JSON.stringify(id && !record.id ? Object.fromEntries(Object.entries(body).filter(([key])=>key!=='add_to_active_event'&&key!=='include_catalog')):body)});
      savedItemId=saved.id;
      if(kind==='items'&&record.id&&eventId){try{await request(`/api/v1/admin/events/${eventId}/menu/${saved.id}`,{method:'PATCH',body:JSON.stringify({is_available:new FormData($('#editor-form')).has('event_available')})});}catch(problem){throw new Error('Позиция сохранена, но доступность не обновилась. '+problem.message);}}
      dirty=false;$('#editor').close();notify('Изменения сохранены');await load();
    }catch(problem){$('#editor-error').textContent=problem.message;$('#editor-error').hidden=false;if(problem.uncertain){loaded=false;dirty=false;$('#editor').close();Panel.error(problem.message+' Проверьте список перед повторной отправкой.');}}
    finally{busy=false;lock();}
  }
  async function action(task,message) {if(busy||!loaded)return;busy=true;lock();try{await task();notify(message);await load();}catch(problem){notify(problem.message,true);try{await load();}catch{loaded=false;Panel.error('Не удалось обновить данные. Действия отключены до обновления.');}}finally{busy=false;lock();}}
  async function changeEvent(target,closing) {
    if(busy||!loaded)return;
    busy=true;lock();
    try {
      await request(`/api/v1/admin/events/${target.id}/${closing?'close':'activate'}`,{method:'POST'});
      notify('Мероприятие обновлено');await load();
    } catch(problem) {
      if(problem.status===409&&problem.detail?.code==='pending_work'){
        const info=`${problem.detail.event_name}: ${problem.detail.orders} незавершённых заказов и ${problem.detail.special_requests} особых запросов. Сначала обработайте очередь; сервер не позволит переключить смену раньше.`;
        if(await Panel.confirm('Переключение пока недоступно',info,{label:'Открыть очередь'}))location.assign('/staff');
      } else {notify(problem.message,true);try{await load();}catch{loaded=false;Panel.error('Не удалось обновить данные. Действия отключены до обновления.');}}
    } finally {busy=false;lock();}
  }
  document.querySelectorAll('[data-section]').forEach(button=>button.addEventListener('click',()=>switchSection(button.dataset.section)));
  $('.section-tabs').addEventListener('keydown',event=>{const tabs=[...document.querySelectorAll('[data-section]')],index=tabs.indexOf(document.activeElement);if(index<0)return;let next=index;if(event.key==='ArrowRight')next=(index+1)%tabs.length;else if(event.key==='ArrowLeft')next=(index-1+tabs.length)%tabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else return;event.preventDefault();tabs[next].focus();switchSection(tabs[next].dataset.section);});
  ['search','category-filter','availability-filter'].forEach(id=>$('#'+id).addEventListener('input',render));
  $('#admin-list').addEventListener('click',async event=>{
    const edit=event.target.closest('[data-edit]');if(edit){openEditor(edit.dataset.edit);return;}
    const availabilityButton=event.target.closest('[data-available]');if(availabilityButton){const id=state.activeEvent?.id;if(!id)return;await action(()=>request(`/api/v1/admin/events/${id}/menu/${availabilityButton.dataset.available}`,{method:'PATCH',body:JSON.stringify({is_available:availabilityButton.dataset.value==='true'})}),'Доступность обновлена');return;}
    const eventButton=event.target.closest('[data-event-action]');if(!eventButton)return;
    const target=state.events.find(row=>row.id===Number(eventButton.dataset.id)),closing=eventButton.dataset.eventAction==='close';
    const description=closing?`«${target.name}» будет закрыто. Новые заказы поступать не будут.`:`Активировать «${target.name}» и включить приём заказов? ${state.activeEvent?`Текущее мероприятие «${state.activeEvent.name}» будет закрыто.`:''} Сервер проверит незавершённые заказы.`;
    if(await Panel.confirm(closing?'Завершить мероприятие?':'Начать мероприятие?',description,{label:closing?'Завершить':'Активировать',danger:closing}))await changeEvent(target,closing);
  });
  $('#toggle-orders').addEventListener('click',async()=>{const event=state.activeEvent;if(!event)return;const enabled=!event.orders_enabled;if(await Panel.confirm(enabled?'Возобновить приём?':'Приостановить приём?',enabled?'Гости снова смогут отправлять заказы.':'Новые заказы временно недоступны. Текущие остаются в очереди.',{label:enabled?'Возобновить':'Приостановить'}))await action(()=>request(`/api/v1/admin/events/${event.id}/orders-enabled`,{method:'PATCH',body:JSON.stringify({is_available:enabled})}),'Приём заказов обновлён');});
  $('#create').addEventListener('click',()=>openEditor());$('#refresh').addEventListener('click',refresh);$('#editor-close').addEventListener('click',closeEditor);$('#editor-cancel').addEventListener('click',closeEditor);$('#editor').addEventListener('cancel',event=>{event.preventDefault();closeEditor();});$('#editor-form').addEventListener('input',()=>dirty=formSnapshot()!==initialForm);$('#editor-form').addEventListener('submit',save);
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  Panel.start(refresh);
})();
