'use strict';
// Admin token never enters localStorage, IndexedDB, cookies, URL or any backup.
(()=>{
 let token='';const $=id=>document.getElementById(id);
 function setStatus(msg,isError=false){$('status').textContent=msg;$('status').className='status'+(isError?' error':'');}
 async function call(path,opts={}){
  const headers={'X-Admin-Token':token,...(opts.body instanceof FormData?{}:{'Content-Type':'application/json'})};
  const response=await fetch('/admin/api'+path,{...opts,headers,cache:'no-store',credentials:'omit'});
  const ct=response.headers.get('content-type')||'';
  const data=ct.includes('json')?await response.json():{};
  if(!response.ok)throw Error(data.error||'HTTP '+response.status);
  return data;
 }
 function button(label,callback,style='secondary'){
  const b=document.createElement('button');b.type='button';b.className='button '+style;b.textContent=label;b.addEventListener('click',callback);return b;
 }
 async function guarded(operation){try{await operation()}catch(error){setStatus(error.message,true);alert(error.message)}}
 async function refresh(){
  const result=await call('/apps');$('apps').replaceChildren();
  if(!result.apps.length){const e=document.createElement('div');e.className='empty';e.textContent='Keine Spiele registriert.';$('apps').append(e)}
  for(const app of result.apps){
   const card=document.createElement('article');card.className='app-row';
   const head=document.createElement('div');head.className='row-between';
   const title=document.createElement('div');const h=document.createElement('h3');h.textContent=app.display_name||app.app_id;
   const detail=document.createElement('p');detail.className='small';detail.textContent=app.app_id+' · '+(app.frontend_installed?'Frontend installiert':'Kein Frontend')+' · '+(app.has_data?'Spieldaten vorhanden':'Keine Spieldaten')+' · Revision '+app.revision;
   const pill=document.createElement('span');pill.className='pill '+app.state;pill.textContent=app.state==='active'?'Aktiv':app.state==='disabled'?'Deaktiviert':'Gelöscht';
   title.append(h,detail);head.append(title,pill);card.append(head);
   const actions=document.createElement('div');actions.className='actions';
   if(app.frontend_installed&&app.state==='active'){
    const link=document.createElement('a');link.href='/apps/'+encodeURIComponent(app.app_id)+'/';link.className='button secondary';link.textContent='Öffnen';actions.append(link);
   }
   if(app.state!=='deleted'){
    actions.append(button(app.state==='disabled'?'Aktivieren':'Deaktivieren',()=>guarded(async()=>{
     const next=app.state==='disabled'?'active':'disabled';
     if(!confirm('Spiel '+app.app_id+' '+(next==='active'?'aktivieren':'deaktivieren')+'? Alte PWAs erhalten bei Deaktivierung HTTP 410.'))return;
     await call('/apps/'+app.app_id+'/toggle',{method:'POST',body:JSON.stringify({state:next})});await refresh();
    })));
   }
   if(app.frontend_installed){actions.append(button('Frontend-ZIP sichern',()=>guarded(async()=>{await download('/apps/'+encodeURIComponent(app.app_id)+'/download',app.app_id+'-frontend.zip')}),'secondary'));actions.append(button('Nur Frontend entfernen',()=>guarded(async()=>{
    const phrase='ENTFERNEN '+app.app_id;
    if(prompt('Frontend entfernen und Spieldaten behalten? Das Spiel wird deaktiviert.\nZum Bestätigen eingeben: '+phrase)!==phrase)return;
    const r=await call('/apps/'+app.app_id+'/uninstall',{method:'POST',body:JSON.stringify({confirm:phrase})});
    setStatus('Frontend entfernt. Vollständiges SQLite-Backup: '+r.backup);await refresh();await loadBackups();
   }),'secondary'))}
   if(app.has_data||app.frontend_installed||app.state==='disabled'){
    actions.append(button('Spiel und Daten löschen',()=>guarded(async()=>{
     const phrase='LÖSCHEN '+app.app_id;
     if(prompt('ACHTUNG: App-Dokument und Frontend werden entfernt, API dauerhaft gesperrt. Vorher SQLite-Backup.\nZum Bestätigen eingeben: '+phrase)!==phrase)return;
     if(!confirm('Wirklich die Daten von '+app.app_id+' löschen? Andere Spiele und gemeinsame Profile bleiben erhalten.'))return;
     const r=await call('/apps/'+app.app_id+'/delete',{method:'POST',body:JSON.stringify({confirm:phrase})});
     setStatus('Spiel gelöscht und gesperrt. Backup: '+r.backup);await refresh();await loadBackups();
    }),'danger'))
   }
   card.append(actions);$('apps').append(card);
  }
 }
 async function download(path,name){
  const response=await fetch('/admin/api'+path,{headers:{'X-Admin-Token':token},cache:'no-store',credentials:'omit'});
  if(!response.ok)throw Error('Download: HTTP '+response.status);
  const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
 }
 async function loadBackups(){
  const result=await call('/backups');$('backups').replaceChildren();
  if(!result.backups.length){$('backups').textContent='Noch keine Backups.';return}
  for(const item of result.backups){
   const row=document.createElement('div');row.className='backup-row';
   const label=document.createElement('span');label.textContent=item.name+' · '+(item.sizeBytes/1024).toFixed(1)+' KB';
   row.append(label,button('Herunterladen',()=>guarded(async()=>{
    await download(item.kind==='frontend'?'/frontends/backups/'+encodeURIComponent(item.name):'/backups/'+encodeURIComponent(item.name),item.name);
   })));
   $('backups').append(row);
  }
 }
 $('connect').addEventListener('click',()=>guarded(async()=>{
  token=$('adminToken').value.trim();$('adminToken').value='';
  if(!token)throw Error('Administrator-Token fehlt');
  await refresh();await loadBackups();$('refresh').disabled=false;$('newBackup').disabled=false;
  setStatus('Angemeldet. Admin-Token ist nur in dieser geöffneten Seite im Arbeitsspeicher.');
 }));
 $('disconnect').addEventListener('click',()=>{token='';$('refresh').disabled=true;$('newBackup').disabled=true;setStatus('Getrennt. Browserseite neu laden, um die Sitzung zu verlassen.');$('apps').textContent='Nicht angemeldet.';$('backups').textContent='Nicht angemeldet.'});
 $('refresh').addEventListener('click',()=>guarded(async()=>{await refresh();await loadBackups();setStatus('Aktualisiert')}));
 $('newBackup').addEventListener('click',()=>guarded(async()=>{const r=await call('/backup',{method:'POST',body:'{}'});setStatus('SQLite-Sicherung: '+r.backup);await loadBackups()}));
 $('installForm').addEventListener('submit',e=>{e.preventDefault();guarded(async()=>{
  if(!token)throw Error('Bitte zuerst Administrator-Token eingeben');
  const appId=$('appId').value.trim(),file=$('zip').files[0];if(!file)throw Error('ZIP-Datei fehlt');
  if(!confirm('Frontend-Paket '+file.name+' als '+appId+' installieren? Nur vertrauenswürdige Dateien verwenden.'))return;
  const body=new FormData($('installForm'));
  const r=await call('/apps/install',{method:'POST',body});
  setStatus('Installiert: '+appId+' · DB-Backup '+r.backup+' · LAN-Anpassungen: '+(r.lan_adapter_changes||[]).join('; '));
  await refresh();await loadBackups();
 })});
})();
