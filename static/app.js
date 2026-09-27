const app = document.querySelector('#app');
const statusPill = document.querySelector('#statusPill');
const cardTemplate = document.querySelector('#cardTemplate');
const SESSION_KEY = 'prerelease-night-session-v1';

let state = {
  session: loadSession(),
  event: null,
  kit: null,
  deck: { main_copy_ids: [], basics: { Plains: 0, Island: 0, Swamp: 0, Mountain: 0, Forest: 0 } },
  opened: new Set(),
  screen: 'home',
  filter: 'ALL',
  ws: null,
};

function loadSession() {
  try { return JSON.parse(localStorage.getItem(SESSION_KEY) || 'null'); } catch { return null; }
}
function saveSession(session) {
  state.session = session;
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}
function clearSession() {
  localStorage.removeItem(SESSION_KEY);
  if (state.ws) state.ws.close();
  state = { ...state, session: null, event: null, kit: null, opened: new Set(), screen: 'home', ws: null };
  renderHome();
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  let data = null;
  try { data = await res.json(); } catch {}
  if (!res.ok) throw new Error(data?.detail || `${res.status} ${res.statusText}`);
  return data;
}

function escapeHtml(text='') {
  return String(text).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}
function showError(container, err) {
  const old = container.querySelector('.error'); if (old) old.remove();
  const box = document.createElement('div'); box.className='error'; box.textContent=err.message || String(err); container.prepend(box);
}
function setBusy(button, busy, label='Working…') {
  if (!button) return;
  if (busy) { button.dataset.label=button.textContent; button.textContent=label; button.disabled=true; }
  else { button.textContent=button.dataset.label || button.textContent; button.disabled=false; }
}

function renderHome() {
  state.screen='home'; statusPill.textContent='Prerelease Sealed';
  const incoming = new URLSearchParams(location.search).get('event') || '';
  app.innerHTML = `
    <section class="hero">
      <div class="eyebrow">Six packs. One promo. One night.</div>
      <h1>Recreate prerelease night with your friends.</h1>
      <p>Open private simulated prerelease kits, build 40-card sealed decks, then take them into matches. This first build handles the event, packs and deck construction.</p>
    </section>
    <section class="grid-2">
      <form id="hostForm" class="panel">
        <h2>Host an event</h2>
        <p class="small">Reality Fracture is the default current test set. Use DEMO if you want to test the UI without the MTGJSON data download.</p>
        <div class="field"><label>Your name</label><input name="name" required maxlength="40" placeholder="Dave" /></div>
        <div class="field"><label>Set code</label><input name="set" value="FRA" required maxlength="8" autocapitalize="characters" /></div>
        <button class="btn btn-primary" type="submit">Create prerelease</button>
      </form>
      <form id="joinForm" class="panel">
        <h2>Join friends</h2>
        <p class="small">Enter the five-character code from the host.</p>
        <div class="field"><label>Your name</label><input name="name" required maxlength="40" placeholder="Player name" /></div>
        <div class="field"><label>Event code</label><input name="code" value="${escapeHtml(incoming)}" required maxlength="5" autocapitalize="characters" /></div>
        <button class="btn" type="submit">Join prerelease</button>
      </form>
    </section>`;

  document.querySelector('#hostForm').addEventListener('submit', async e => {
    e.preventDefault(); const form=e.currentTarget; const btn=form.querySelector('button'); setBusy(btn,true,'Creating…');
    try {
      let setCode=form.set.value.trim().toUpperCase();
      if (setCode === 'DEMO') {
        // Demo provider is selected server-side, so this is mainly a visual hint.
      }
      const data=await api('/api/events',{method:'POST',body:JSON.stringify({host_name:form.name.value.trim(),set_code:setCode})});
      saveSession({eventCode:data.event.code,playerId:data.player_id,token:data.token,name:form.name.value.trim()});
      state.event=data.event; connectWs(); renderLobby();
    } catch(err){ showError(form,err); } finally { setBusy(btn,false); }
  });
  document.querySelector('#joinForm').addEventListener('submit', async e => {
    e.preventDefault(); const form=e.currentTarget; const btn=form.querySelector('button'); setBusy(btn,true,'Joining…');
    try {
      const code=form.code.value.trim().toUpperCase();
      const data=await api(`/api/events/${encodeURIComponent(code)}/join`,{method:'POST',body:JSON.stringify({player_name:form.name.value.trim()})});
      saveSession({eventCode:data.event.code,playerId:data.player_id,token:data.token,name:form.name.value.trim()});
      state.event=data.event; connectWs(); renderLobby();
    } catch(err){ showError(form,err); } finally { setBusy(btn,false); }
  });
}

function isHost(){ return state.event && state.session && state.event.host_player_id===state.session.playerId; }

function renderLobby() {
  state.screen='lobby'; if (!state.event) return renderHome();
  statusPill.textContent=`${state.event.set_name} · ${state.event.status}`;
  const share=`${location.origin}${location.pathname}?event=${state.event.code}`;
  app.innerHTML=`
    <section class="panel">
      <div class="lobby-head"><div><div class="eyebrow">Event lobby</div><h1 style="font-size:44px;margin-bottom:6px">${escapeHtml(state.event.set_name)}</h1><div class="muted">${escapeHtml(state.event.set_code)} · ${escapeHtml(state.event.booster_type)} boosters</div></div><button id="leaveBtn" class="btn">Leave</button></div>
      <div class="muted small">Share this code</div><div class="event-code">${escapeHtml(state.event.code)}</div>
      <div class="actions"><button id="copyBtn" class="btn">Copy invite link</button>${isHost()?'<button id="startBtn" class="btn btn-primary">Generate prerelease kits</button>':''}</div>
      <div class="section-title"><h2>Players</h2><span class="badge">${state.event.players.length}</span></div>
      <div class="player-list">${state.event.players.map(p=>`<div class="player-row"><div><span class="dot"></span><strong>${escapeHtml(p.name)}</strong>${p.id===state.event.host_player_id?' <span class="muted small">Host</span>':''}</div><span class="muted small">${p.deck_count?`${p.deck_count} cards`:p.has_kit?'Kit ready':'Waiting'}</span></div>`).join('')}</div>
      ${state.event.status!=='lobby'?'<div class="notice">Kits are generated. Your sealed pool is private to your player session.</div><div style="margin-top:14px"><button id="kitBtn" class="btn btn-primary">Open my prerelease kit</button></div>':''}
    </section>`;
  document.querySelector('#leaveBtn').onclick=clearSession;
  document.querySelector('#copyBtn').onclick=async()=>{ await navigator.clipboard.writeText(share); document.querySelector('#copyBtn').textContent='Copied'; };
  document.querySelector('#startBtn')?.addEventListener('click',startEvent);
  document.querySelector('#kitBtn')?.addEventListener('click',loadKit);
}

async function startEvent(e){
  const btn=e.currentTarget; setBusy(btn,true,'Building kits…');
  try {
    state.event=await api(`/api/events/${state.event.code}/start`,{method:'POST',body:JSON.stringify({player_id:state.session.playerId,token:state.session.token})});
    renderLobby();
  } catch(err){ showError(app.querySelector('.panel'),err); setBusy(btn,false); }
}

async function loadKit(){
  try {
    const data=await api(`/api/events/${state.session.eventCode}/players/${state.session.playerId}/kit?token=${encodeURIComponent(state.session.token)}`);
    state.kit=data.kit; state.deck=data.deck; renderKit();
  } catch(err){ showError(app,err); }
}

function cardNode(card,{selected=false,onClick=null}={}){
  const node=cardTemplate.content.firstElementChild.cloneNode(true);
  if(selected) node.classList.add('selected'); if(card.foil) node.classList.add('foil');
  const img=node.querySelector('.card-art');
  if(card.image_url){ img.src=card.image_url; img.alt=card.name; img.onerror=()=>img.removeAttribute('src'); }
  node.querySelector('.card-placeholder-name').textContent=card.name;
  node.querySelector('.card-placeholder-type').textContent=card.type_line || card.rarity;
  node.querySelector('.card-name').textContent=card.name;
  node.querySelector('.card-cost').textContent=card.mana_cost || '';
  node.querySelector('.card-type').textContent=card.type_line || card.rarity;
  node.title=`${card.name}\n${card.type_line || ''}\n${card.oracle_text || ''}`;
  if(onClick) node.addEventListener('click',()=>onClick(card)); else node.disabled=true;
  return node;
}

function renderKit(){
  state.screen='kit'; statusPill.textContent=`${state.kit.set_code} · Your kit`;
  const openedCount=state.opened.size;
  app.innerHTML=`
    <section class="kit-header"><div><div class="eyebrow">Your prerelease kit</div><h1 style="font-size:48px">Crack the packs.</h1><p>These cards were generated once for your player and stored on the event server. There is no reroll button.</p></div><button id="lobbyBtn" class="btn">Lobby</button></section>
    <section class="panel">
      <div class="promo-title">PRERELEASE PROMO</div><div id="promo" class="promo-wrap"></div>
      <div class="pack-grid">${state.kit.packs.map(p=>`<button class="pack ${state.opened.has(p.number)?'opened':''}" data-pack="${p.number}">${state.opened.has(p.number)?`PACK ${p.number} OPENED`:`OPEN PACK ${p.number}`}</button>`).join('')}</div>
      <div id="reveal" class="reveal"></div>
      <div class="actions" style="margin-top:18px"><button id="buildBtn" class="btn btn-primary" ${openedCount<6?'disabled':''}>Build my sealed deck</button><span class="muted small">${openedCount}/6 packs opened</span></div>
    </section>`;
  document.querySelector('#promo').appendChild(cardNode(state.kit.promo));
  document.querySelector('#lobbyBtn').onclick=renderLobby;
  document.querySelectorAll('.pack').forEach(btn=>btn.onclick=()=>openPack(Number(btn.dataset.pack)));
  document.querySelector('#buildBtn').onclick=renderBuilder;
  if(openedCount){ const last=[...state.opened].at(-1); revealPack(last); }
}
function openPack(n){ state.opened.add(n); renderKit(); revealPack(n); }
function revealPack(n){
  const pack=state.kit.packs.find(p=>p.number===n); if(!pack)return;
  const reveal=document.querySelector('#reveal'); if(!reveal)return;
  reveal.innerHTML=`<div class="section-title"><h2>Pack ${n}</h2><span class="badge">${pack.cards.length} cards</span></div><div class="cards-grid"></div>`;
  const grid=reveal.querySelector('.cards-grid');
  [...pack.cards].sort((a,b)=>rarityRank(b.rarity)-rarityRank(a.rarity)).forEach(c=>grid.appendChild(cardNode(c)));
}
function rarityRank(r){ return ({mythic:4,'mythic rare':4,rare:3,uncommon:2,common:1}[String(r).toLowerCase()]||0); }

function allPoolCards(){ return [state.kit.promo,...state.kit.packs.flatMap(p=>p.cards)]; }
function selectedIds(){ return new Set(state.deck.main_copy_ids || []); }
function colorBucket(card){
  const c=card.colors || [];
  if((card.type_line||'').includes('Land')) return 'LAND';
  if(c.length===0) return 'C'; if(c.length>1) return 'M'; return c[0];
}
function filteredPool(){
  const selected=selectedIds();
  return allPoolCards().filter(c=>!selected.has(c.copy_id)).filter(c=>state.filter==='ALL'||colorBucket(c)===state.filter);
}
function deckCards(){ const ids=selectedIds(); return allPoolCards().filter(c=>ids.has(c.copy_id)); }
function totalDeckCount(){ return (state.deck.main_copy_ids?.length||0)+Object.values(state.deck.basics||{}).reduce((a,b)=>a+Number(b||0),0); }

function renderBuilder(){
  state.screen='builder'; statusPill.textContent=`${state.kit.set_code} · Deckbuilding`;
  const filters=['ALL','W','U','B','R','G','M','C','LAND'];
  app.innerHTML=`
    <div class="deck-layout">
      <section>
        <div class="kit-header"><div><div class="eyebrow">Sealed deck construction</div><h1 style="font-size:48px">Build 40+</h1><p>Tap a card to move it between your pool and main deck. Your unopened cards and basic lands remain available as your sideboard/pool.</p></div><button id="backKit" class="btn">Packs</button></div>
        <div class="filterbar">${filters.map(f=>`<button class="filter ${state.filter===f?'active':''}" data-filter="${f}">${f}</button>`).join('')}</div>
        <div class="section-title"><h2>Available pool</h2><span class="badge" id="poolCount"></span></div>
        <div id="poolGrid" class="cards-grid"></div>
        <div class="section-title"><h2>Main deck cards</h2><span class="badge" id="mainCount"></span></div>
        <div id="mainGrid" class="cards-grid"></div>
      </section>
      <aside class="panel deck-sidebar">
        <div class="eyebrow">Main deck</div><div id="deckCount" class="deck-count">0 / 40</div><p class="small">Prerelease sealed decks have a 40-card minimum. Basic lands are unlimited.</p>
        <h3>Basic lands</h3><div id="basics"></div>
        <div class="actions" style="margin-top:18px"><button id="saveDeck" class="btn btn-primary">Save deck</button></div><div id="saveStatus" class="muted small" style="margin-top:10px"></div>
      </aside>
    </div>`;
  document.querySelector('#backKit').onclick=renderKit;
  document.querySelectorAll('.filter').forEach(b=>b.onclick=()=>{state.filter=b.dataset.filter; renderBuilder();});
  renderDeckContents();
  document.querySelector('#saveDeck').onclick=saveDeck;
}

function renderDeckContents(){
  const pool=filteredPool(), main=deckCards();
  document.querySelector('#poolCount').textContent=`${pool.length} cards`;
  document.querySelector('#mainCount').textContent=`${main.length} nonlands`;
  const poolGrid=document.querySelector('#poolGrid'), mainGrid=document.querySelector('#mainGrid');
  poolGrid.innerHTML=''; mainGrid.innerHTML='';
  [...pool].sort(cardSort).forEach(c=>poolGrid.appendChild(cardNode(c,{onClick:addToDeck})));
  [...main].sort(cardSort).forEach(c=>mainGrid.appendChild(cardNode(c,{selected:true,onClick:removeFromDeck})));
  renderBasics(); updateCount();
}
function cardSort(a,b){ return colorBucket(a).localeCompare(colorBucket(b)) || (a.mana_value??99)-(b.mana_value??99) || a.name.localeCompare(b.name); }
function addToDeck(card){ if(!state.deck.main_copy_ids.includes(card.copy_id))state.deck.main_copy_ids.push(card.copy_id); renderDeckContents(); }
function removeFromDeck(card){ state.deck.main_copy_ids=state.deck.main_copy_ids.filter(id=>id!==card.copy_id); renderDeckContents(); }
function renderBasics(){
  const box=document.querySelector('#basics'); box.innerHTML='';
  for(const name of ['Plains','Island','Swamp','Mountain','Forest']){
    const row=document.createElement('div'); row.className='basic-row';
    row.innerHTML=`<strong>${name}</strong><button class="basic-step minus">−</button><span>${state.deck.basics[name]||0}</span><button class="basic-step plus">+</button>`;
    row.querySelector('.minus').onclick=()=>{state.deck.basics[name]=Math.max(0,(state.deck.basics[name]||0)-1);renderDeckContents();};
    row.querySelector('.plus').onclick=()=>{state.deck.basics[name]=(state.deck.basics[name]||0)+1;renderDeckContents();};
    box.appendChild(row);
  }
}
function updateCount(){ const n=totalDeckCount(), el=document.querySelector('#deckCount'); el.textContent=`${n} / 40`; el.classList.toggle('good',n>=40); }
async function saveDeck(){
  const btn=document.querySelector('#saveDeck'), status=document.querySelector('#saveStatus'); setBusy(btn,true,'Saving…');
  try{
    const result=await api(`/api/events/${state.session.eventCode}/deck`,{method:'PUT',body:JSON.stringify({player_id:state.session.playerId,token:state.session.token,main_copy_ids:state.deck.main_copy_ids,basics:state.deck.basics})});
    state.deck=result.deck; status.textContent=`Saved ${result.card_count} cards.${result.card_count<40?' Add more before playing.':' Ready for a match.'}`;
  }catch(err){status.textContent=err.message;status.style.color='var(--danger)';}finally{setBusy(btn,false);}
}

function connectWs(){
  if(!state.session)return; if(state.ws)state.ws.close();
  const protocol=location.protocol==='https:'?'wss':'ws';
  const ws=new WebSocket(`${protocol}://${location.host}/ws/events/${state.session.eventCode}`); state.ws=ws;
  ws.onmessage=e=>{
    try{
      const msg=JSON.parse(e.data); if(msg.type==='event'){
        state.event=msg.event;
        if(state.screen==='lobby')renderLobby();
      }
    }catch{}
  };
  ws.onopen=()=>{statusPill.textContent=state.event?`${state.event.set_name} · live`:'Connected'; setInterval(()=>{if(ws.readyState===1)ws.send('ping');},25000);};
}

async function restore(){
  if(!state.session)return renderHome();
  try{
    state.event=await api(`/api/events/${state.session.eventCode}`); connectWs();
    if(state.event.status!=='lobby'){
      try{const data=await api(`/api/events/${state.session.eventCode}/players/${state.session.playerId}/kit?token=${encodeURIComponent(state.session.token)}`);state.kit=data.kit;state.deck=data.deck;}catch{}
    }
    renderLobby();
  }catch{clearSession();}
}

document.querySelector('#brand').onclick=()=>state.session?renderLobby():renderHome();
restore();
