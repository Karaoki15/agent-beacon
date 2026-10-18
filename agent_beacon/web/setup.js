(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  let token = '', control = null, latest = null, plan = null, busy = false, qrTimer = null;
  const notice = (text, kind='') => { byId('notice').textContent=text; byId('notice').className='notice '+kind; };
  async function request(path, body) {
    const response = await fetch(path, {cache:'no-store', headers:{Authorization:'Bearer '+token, ...(body === undefined ? {} : {'Content-Type':'application/json'})},
      ...(body === undefined ? {} : {method:'POST', body:JSON.stringify(body)}), signal:AbortSignal.timeout(path.endsWith('firewall')?100000:15000)});
    if (!response.ok) throw new Error(response.status===409 ? 'Налаштування змінилися або дію не завершено. Перегляньте зміни ще раз; для допомоги скористайтеся промптом агента.' : 'Не вдалося виконати дію. Перевірте, чи працює Beacon, і повторіть.');
    return response.json();
  }
  function invalidate() { plan=null;byId('planBox').hidden=true; }
  function button(text, action) { const b=document.createElement('button');b.type='button';b.textContent=text;b.addEventListener('click',action);return b; }
  function cards(status) {
    const box=byId('agentCards');box.replaceChildren();
    for (const provider of status.providers) {
      const card=document.createElement('div');card.className='agent';
      const label=document.createElement('label');const input=document.createElement('input');input.type='checkbox';input.value=provider.id;input.checked=provider.enabled;input.name='provider';input.addEventListener('change',invalidate);
      label.append(input,document.createTextNode(provider.name));card.append(label);
      const found=document.createElement('small');found.textContent=provider.detected?'Знайдено локальні налаштування або команду.':'Автоматично не знайдено. Можна вибрати вручну.';card.append(found);
      if (provider.id==='antigravity') {const tag=document.createElement('small');tag.className='tag';tag.textContent='Експериментально · потрібна перевірка у вашій версії';card.append(tag);}
      const help=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Допомогти підключити';help.append(summary);
      const description=document.createElement('p');description.textContent='Скопіюйте цей промпт у Codex або Claude Code. Агент спочатку покаже зміни, а потім допоможе їх застосувати.';help.append(description);
      const prompt=document.createElement('textarea');prompt.value=provider.prompt;prompt.readOnly=true;prompt.setAttribute('aria-label','Промпт підключення '+provider.name);help.append(prompt);
      help.append(button('Скопіювати промпт',async()=>{try{await navigator.clipboard.writeText(prompt.value);notice('Промпт скопійовано. Вставте його в чат агента.','success');}catch(_){prompt.focus();prompt.select();notice('Виділено промпт: натисніть Ctrl+C, щоб скопіювати.');}}));card.append(help);box.append(card);
    }
  }
  function checks(status) {
    byId('checks').replaceChildren();
    for(const provider of status.providers) {
      const row=document.createElement('div');row.className='check';const name=document.createElement('strong');name.textContent=provider.name;
      const text=document.createElement('span');text.textContent=!provider.enabled?'Не вибрано':provider.signal_received?'Отримано подію від агента':provider.configured?'Хуки записано · чекаємо першої події':'Потрібно налаштувати хуки';
      if(provider.enabled&&provider.signal_received)text.className='ok';row.append(name,text);byId('checks').append(row);
    }
    const phone=document.createElement('div');phone.className='check';const name=document.createElement('strong');name.textContent='Android';const detail=document.createElement('span');detail.textContent=status.paired_devices?'Підключених пристроїв: '+status.paired_devices:'Ще не підключено';phone.append(name,detail);byId('checks').append(phone);
  }
  function paintQr(text) {
    const code=qrcode(0,'M');code.addData(text);code.make();const canvas=byId('qr'),count=code.getModuleCount(),cell=5;canvas.width=canvas.height=(count+8)*cell;const ctx=canvas.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.fillStyle='#000';
    for(let row=0;row<count;row++)for(let col=0;col<count;col++)if(code.isDark(row,col))ctx.fillRect((col+4)*cell,(row+4)*cell,cell,cell);
    byId('qrBox').hidden=false;
  }
  function addresses() {
    const select=byId('address'),old=select.value;select.replaceChildren();
    for(const address of control.lan_addresses||[]) {
      const parts=address.split('.').map(Number);
      if(parts.length!==4||parts.some(p=>!Number.isInteger(p)||p<0||p>255)||!(parts[0]===10||parts[0]===192&&parts[1]===168||parts[0]===172&&parts[1]>=16&&parts[1]<=31))continue;
      const option=document.createElement('option');option.value=address;option.textContent=address;select.append(option);
    }
    if([...select.options].some(o=>o.value===old))select.value=old;
    byId('apkQr').disabled=byId('pairQr').disabled=!select.value;
  }
  async function refresh(initial=false) {
    const response=await fetch('/api/control',{cache:'no-store',signal:AbortSignal.timeout(5000)});
    if(!response.ok)throw new Error('Майстер доступний лише на комп’ютері, де працює Beacon.');
    control=await response.json();token=control.viewer_token;
    if(!control.setup_available)throw new Error('Ця копія запущена зі старого скрипта. Відкрийте Windows EXE Agent Beacon для майстра підключення.');
    latest=await request('/api/setup');
    if(initial){cards(latest);byId('lan').checked=latest.lan;byId('migration').checked=Boolean(latest.migration);}
    byId('migrationBox').hidden=!latest.migration;
    byId('migrationText').textContent=latest.migration?'Знайдено підключень: '+latest.migration.count+'. Буде скопійовано лише локальний реєстр доступу, без повторного QR. Попередню копію потім закрийте через її значок у треї.':'';
    checks(latest);addresses();byId('plan').disabled=false;byId('version').textContent='Agent Beacon '+latest.version;
    byId('dataPath').textContent='Дані програми: '+latest.data_directory;
    byId('startup').disabled=!latest.packaged;byId('startup').checked=latest.startup;byId('firewall').disabled=!latest.packaged;
    byId('phoneControls').hidden=!latest.lan;
    byId('phoneState').textContent=latest.lan ? 'Приватна мережа ввімкнена. '+(!latest.discovery?'Інша копія Beacon може займати пошук у мережі; перед підключенням телефона залиште одну копію.':'Після pairing телефон зможе знаходити цей комп’ютер автоматично.') : 'Увімкніть доступ для телефона й застосуйте налаштування вище.';
  }
  async function perform(action) {if(busy)return;busy=true;try{await action();}catch(error){notice(error.message,'error');}finally{busy=false;byId('plan').disabled=false;byId('apply').disabled=false;}}
  byId('plan').addEventListener('click',()=>perform(async()=>{
    byId('plan').disabled=true;invalidate();const providers=[...document.querySelectorAll('input[name=provider]:checked')].map(e=>e.value);
    plan=await request('/api/setup/plan',{providers,lan:byId('lan').checked,import_pairing:byId('migration').checked});byId('changeList').replaceChildren();byId('technicalChanges').replaceChildren();
    const summary=providers.map(id=>'Підключити '+(latest.providers.find(p=>p.id===id)?.name||id)+' для локальних проєктів.');
    if(!providers.length)summary.push('Залишити відстеження агентів вимкненим.');
    summary.push(byId('lan').checked?'Увімкнути доступ для телефона в локальній мережі.':'Залишити доступ тільки на цьому комп’ютері.');
    if(byId('migration').checked)summary.push('Зберегти вже підключені телефони зі старої копії Beacon.');
    for(const line of summary){const li=document.createElement('li');li.textContent=line;byId('changeList').append(li);}
    for(const change of plan.changes.length?plan.changes:['Файли хуків уже відповідають цій копії Beacon. Буде збережено вибір агентів і мережі.']) {const li=document.createElement('li');li.textContent=change.replace(/^Would update /,'Оновити / створити: ');byId('technicalChanges').append(li);}
    byId('planBox').hidden=false;notice('Перегляньте список і натисніть «Підключити», щоб застосувати.');
  }));
  byId('apply').addEventListener('click',()=>perform(async()=>{
    if(!plan)return;byId('apply').disabled=true;const result=await request('/api/setup/apply',{plan_id:plan.plan_id});invalidate();
    if(result.restart){notice('Налаштування збережено. Beacon перепідключається; через кілька секунд натисніть «Перевірити підключення».','success');}
    else{await refresh(true);notice('Налаштування збережено. Відкрийте новий чат у вибраному агенті для перевірки сигналу.','success');}
  }));
  byId('lan').addEventListener('change',invalidate);
  byId('migration').addEventListener('change',invalidate);
  byId('refresh').addEventListener('click',()=>perform(async()=>{await refresh(true);invalidate();notice('Перевірено. Хуки й реальні події показано окремо.','success');}));
  byId('apkQr').addEventListener('click',()=>{if(!control||!latest?.lan||!byId('address').value)return;clearTimeout(qrTimer);const url='http://'+byId('address').value+':'+latest.port+'/downloads/AgentBeacon-debug.apk';paintQr(url);byId('qrCaption').textContent='Відскануйте звичайною камерою телефона. Встановіть APK, потім поверніться до кроку 2 — QR підключення.';byId('apkLink').href=url;byId('apkLink').hidden=false;});
  byId('pairQr').addEventListener('click',()=>perform(async()=>{if(!latest?.lan||!byId('address').value)return;clearTimeout(qrTimer);const start=performance.now();const result=await request('/api/pairing',{});const life=Math.max(0,result.expires_in_seconds*1000-(performance.now()-start));if(!/^[A-Za-z0-9_-]{43}$/.test(result.code)||life<=0)throw new Error('Створіть новий QR підключення.');paintQr('agentbeacon://pair?host='+byId('address').value+'&port='+latest.port+'#code='+result.code);byId('apkLink').hidden=true;byId('qrCaption').textContent='Відкрийте Agent Beacon на Android → Scan QR → Connect. Одноразовий код діє 2 хвилини.';qrTimer=setTimeout(()=>{byId('qrBox').hidden=true;byId('qr').width=0;notice('Час QR підключення минув. Натисніть кнопку, щоб створити новий.');},life);}));
  byId('address').addEventListener('change',()=>{byId('qrBox').hidden=true;clearTimeout(qrTimer);});
  byId('startup').addEventListener('change',()=>perform(async()=>{await request('/api/setup/startup',{enabled:byId('startup').checked});notice('Автозапуск оновлено.','success');}));
  byId('firewall').addEventListener('click',()=>perform(async()=>{byId('firewallResult').textContent='Очікуємо вашої відповіді у запиті Windows…';const result=await request('/api/setup/firewall',{});byId('firewallResult').textContent=result.requested?'Дозвіл для Beacon додано. Спробуйте підключити телефон.':'Дію не завершено. Перевірте приватний профіль мережі й дозвіл адміністратора.';}));
  document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(qrTimer);byId('qrBox').hidden=true;byId('qr').width=0;}});
  refresh(true).then(()=>notice('Виберіть агентів, з якими хочете працювати. Налаштування можна змінити пізніше.')).catch(error=>notice(error.message,'error'));
})();
