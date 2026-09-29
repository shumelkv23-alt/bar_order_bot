(() => {
  const {$,esc,request,notify} = Panel;
  const statuses={submitted:'Новые',accepted:'Приняты',preparing:'Готовятся',ready:'Готовы',completed:'Выданы',cancelled:'Отменены',rejected:'Отклонены'};
  let events=[],summary=null,revision=0,timer,loading=false;
  function clear(message) { summary=null;$('#csv').disabled=true;$('#json').disabled=true;$('#metrics').querySelectorAll('strong').forEach(node=>node.textContent='—');['timeline','popular','statuses'].forEach(id=>$('#'+id).innerHTML=`<p class="empty">${esc(message)}</p>`);$('#timeline-table').innerHTML='';$('#extra-metrics').textContent=''; }
  // Continuous hours, including quiet hours. Long events use daily buckets.
  function timelinePoints() {
    if(!summary.orders_by_hour.length)return {points:[],daily:false};
    const source=summary.orders_by_hour.map(point=>({time:Panel.isoDate(point.hour).getTime(),orders:point.orders}));
    const daily=(source[source.length-1].time-source[0].time)/3600000>168;
    const step=daily?86400000:3600000, counts=new Map();
    source.forEach(point=>{const bucket=new Date(point.time);if(daily)bucket.setHours(0,0,0,0);else bucket.setMinutes(0,0,0);const key=bucket.getTime();counts.set(key,(counts.get(key)||0)+point.orders);});
    const keys=[...counts.keys()].sort((a,b)=>a-b), points=[];
    for(let time=keys[0];time<=keys[keys.length-1];time+=step)points.push({time,orders:counts.get(time)||0});
    return {points,daily};
  }
  function render() {
    const values=[summary.total_orders,summary.total_items,summary.unique_guests,summary.active_orders,summary.total_orders?`${summary.completion_rate}%`:'—',summary.average_fulfillment_minutes===null?'—':`${summary.average_fulfillment_minutes} м`];
    $('#metrics').querySelectorAll('strong').forEach((node,index)=>node.textContent=values[index]);
    const total=Math.max(1,summary.total_orders);
    $('#statuses').innerHTML=Object.entries(statuses).map(([status,label])=>{const count=summary.by_status[status] || 0;return `<div class="bar-row" data-status="${status}"><div class="bar-label"><span>${label}</span><b>${count}</b></div><div class="bar-track" aria-hidden="true"><i style="width:${count/total*100}%"></i></div></div>`;}).join('');
    const max=Math.max(1,...summary.popular_items.map(item=>item.quantity));
    $('#popular').innerHTML=summary.popular_items.length?summary.popular_items.map((item,index)=>`<div class="rank-row"><span class="rank">${String(index+1).padStart(2,'0')}</span><div><b>${esc(item.name)}</b><div class="bar-track" aria-hidden="true"><i style="width:${item.quantity/max*100}%"></i></div></div><strong>${item.quantity}</strong></div>`).join(''):'<p class="empty"><strong>Первые заказы ещё впереди</strong>Здесь появятся самые популярные позиции.</p>';
    const {points,daily}=timelinePoints(), maxHour=Math.max(1,...points.map(point=>point.orders));
    const crossesDays=points.length && new Date(points[0].time).toDateString()!==new Date(points[points.length-1].time).toDateString();
    const label=time=>new Date(time).toLocaleString('ru-RU',daily?{day:'2-digit',month:'2-digit'}:crossesDays?{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}:{hour:'2-digit',minute:'2-digit'});
    $('#timeline').innerHTML=points.length?points.map(point=>`<div class="timeline-point"><b>${point.orders}</b><i style="height:${point.orders/maxHour*120}px" aria-hidden="true"></i><span>${esc(label(point.time))}</span></div>`).join(''):'<p class="empty">Пока нет заказов — график появится после первого.</p>';
    $('#timeline-table').innerHTML=points.map(point=>`<tr><td>${esc(new Date(point.time).toLocaleString('ru-RU'))}</td><td>${point.orders}</td></tr>`).join('');
    $('#timeline-caption').textContent=daily?'Длительное мероприятие: группировка по календарным дням в часовом поясе устройства.':'Время в часовом поясе устройства. Нулевые часы между первым и последним заказами сохранены.';
    $('#extra-metrics').textContent=`Выдано: ${summary.completed_orders} · Отменено и отклонено: ${summary.cancelled_orders} · Средний заказ: ${summary.total_orders?summary.average_items_per_order:'—'} ед.`;
    $('#csv').disabled=false;$('#json').disabled=false;
  }
  async function loadSummary(changed=false) {
    clearTimeout(timer);const id=$('#event-select').value, run=++revision;
    if(!id)return;
    if(changed)clear('Загружаем данные…');
    $('#analytics-data').setAttribute('aria-busy','true');
    const event=events.find(row=>String(row.id)===id);$('#event-period').textContent=event?`${Panel.date(event.starts_at)} — ${Panel.date(event.ends_at)}`:'';
    try {const fresh=await request(`/api/v1/analytics/events/${id}`);if(run!==revision)return;summary=fresh;render();Panel.error();Panel.connection('Обновлено '+new Date().toLocaleTimeString('ru-RU'),'online');}
    catch(problem){if(run!==revision)return;Panel.error(problem.message+(summary?' Показаны последние полученные данные.':''));Panel.connection('Ошибка обновления','error');$('#csv').disabled=true;$('#json').disabled=true;if(!summary)clear('Не удалось загрузить данные');}
    finally{if(run===revision){$('#analytics-data').setAttribute('aria-busy','false');timer=setTimeout(()=>{if(Panel.active)loadSummary();},10000);}}
  }
  async function connect() {
    if(loading)return;loading=true;
    try {const selected=$('#event-select').value;events=(await request('/api/v1/analytics/events')).events;$('#event-select').innerHTML=events.map(event=>`<option value="${event.id}">${esc(event.name)}${event.status==='active'?' · активное':''}</option>`).join('');if(events.some(event=>String(event.id)===selected))$('#event-select').value=selected;else if(events.some(event=>event.status==='active'))$('#event-select').value=events.find(event=>event.status==='active').id;if(events.length)await loadSummary(true);else{$('#event-select').innerHTML='<option value="">Нет мероприятий</option>';clear('Создайте первое мероприятие в панели управления');$('#analytics-data').setAttribute('aria-busy','false');}}
    catch(problem){Panel.error(problem.message);Panel.connection('Ошибка загрузки','error');clear('Не удалось загрузить мероприятия');$('#analytics-data').setAttribute('aria-busy','false');}
    finally{loading=false;}
  }
  $('#event-select').addEventListener('change',()=>loadSummary(true));$('#refresh').addEventListener('click',connect);
  $('#csv').addEventListener('click',async()=>{const id=$('#event-select').value;if(!summary||String(summary.event_id)!==id)return;$('#csv').disabled=true;try{const response=await request(`/api/v1/analytics/events/${id}/export.csv`,{raw:true});Panel.download(await response.text(),`analytics-${id}.csv`,'text/csv;charset=utf-8');}catch(problem){notify(problem.message,true);}finally{$('#csv').disabled=!summary;}});
  $('#json').addEventListener('click',()=>{if(summary)Panel.download(JSON.stringify(summary,null,2),`analytics-${summary.event_id}.json`,'application/json');});
  Panel.start(connect);
})();
