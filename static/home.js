'use strict';
const container=document.getElementById('apps');
const styles={yatzy:'🎲',flip7:'🃏'};
fetch('/api/apps',{cache:'no-store'}).then(async r=>{if(!r.ok)throw Error('HTTP '+r.status);return r.json()}).then(data=>{
 container.replaceChildren();
 const apps=data.apps||[];
 if(!apps.length){let p=document.createElement('div');p.className='empty';p.textContent='Noch keine Frontends installiert. Importiere Yatzy oder Flip 7 über Verwaltung → Spiel-ZIP installieren.';container.append(p);return;}
 for(const app of apps){const a=document.createElement('a');a.className='game-tile';a.href=app.url;
 const icon=document.createElement('span');icon.className='game-icon';icon.textContent=styles[app.id]||'🎮';
 const label=document.createElement('div');const h=document.createElement('h2');h.textContent=app.name||app.id;
 const p=document.createElement('p');p.textContent='Spiel öffnen';label.append(h,p);
 const arrow=document.createElement('span');arrow.className='arrow';arrow.textContent='›';a.append(icon,label,arrow);container.append(a)}
}).catch(error=>{container.textContent='Spiele konnten nicht geladen werden: '+error.message});
