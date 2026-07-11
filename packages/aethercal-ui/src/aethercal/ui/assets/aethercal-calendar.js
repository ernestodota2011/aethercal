function Z(e){return String(e).padStart(2,"0")}function g(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,n,a,r,i,o,l]=t,s=Number(n),d=Number(a),c=Number(r),b=Number(i??"0"),u=Number(o??"0"),D=Number(l??"0");if(d<1||d>12||c<1||c>31||b>23||u>59||D>59)throw new Error(`out-of-range ISO datetime: ${e}`);let m=new Date(s,d-1,c,b,u,D);if(m.getFullYear()!==s||m.getMonth()!==d-1||m.getDate()!==c)throw new Error(`nonexistent calendar date: ${e}`);return m}function k(e){return`${e.getFullYear()}-${Z(e.getMonth()+1)}-${Z(e.getDate())}T${Z(e.getHours())}:${Z(e.getMinutes())}:${Z(e.getSeconds())}`}function V(e){let t=g(e);return`${t.getFullYear()}-${Z(t.getMonth()+1)}-${Z(t.getDate())}`}function Et(e,t){return(e.getDay()-t+7)%7}function Ce(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate());return n.setDate(n.getDate()-Et(n,t)),n}function Je(e,t){return Array.from({length:t},(n,a)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+a);return`${r.getFullYear()}-${Z(r.getMonth()+1)}-${Z(r.getDate())}`})}function Se(e,t=1){return Je(Ce(e,t),7)}function Ie(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),1);return Je(Ce(n,t),42)}function xt(e,t){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()),a=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((a.getTime()-n.getTime())/864e5)}function se(e,t){let n=g(e.start),a=g(e.end),r=g(t),i=xt(n,r),o=new Date(n.getFullYear(),n.getMonth(),n.getDate()+i,n.getHours(),n.getMinutes(),n.getSeconds()),l=new Date(a.getFullYear(),a.getMonth(),a.getDate()+i,a.getHours(),a.getMinutes(),a.getSeconds()),s={id:e.id,start:k(o),end:k(l)};return e.revision!==void 0&&(s.revision=e.revision),s}var Rt=370;function Xe(e){return String(e).padStart(2,"0")}function ke(e){return`${e.getFullYear()}-${Xe(e.getMonth()+1)}-${Xe(e.getDate())}`}function je(e){return new Date(e.getFullYear(),e.getMonth(),e.getDate())}function Tt(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function Mt(e){let t=g(e.start),n=g(e.end),a=je(t),r;n.getTime()<=t.getTime()?r=a:(r=je(new Date(n.getTime()-1)),r.getTime()<a.getTime()&&(r=a));let i=[],o=a;for(let l=0;l<Rt&&o.getTime()<=r.getTime();l+=1)i.push(ke(o)),o=Tt(o,1);return{keys:i,startKey:ke(a),lastKey:ke(r)}}function Le(e){let t=new Map;return e.forEach((n,a)=>{let{keys:r,startKey:i,lastKey:o}=Mt(n),l=g(n.start).getTime(),s=g(n.end).getTime();for(let d of r){let c={entry:{event:n,isContinuation:d!==i,continuesAfter:d!==o},startMs:l,endMs:s,index:a},b=t.get(d);b?b.push(c):t.set(d,[c])}}),[...t.keys()].sort().map(n=>{let a=t.get(n);return a.sort((r,i)=>r.startMs-i.startMs||r.endMs-i.endMs||r.index-i.index),{date:n,entries:a.map(r=>r.entry)}})}var ue={status:"idle"};function ge(e){return e.status==="dragging"}function Pe(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return ue}}var pe={status:"idle"};function Ae(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"RESIZE_START":return{status:"resizing",eventId:t.eventId,edge:t.edge};case"SELECT_START":return{status:"selecting",anchor:t.point,current:t.point};case"SELECT_MOVE":return e.status!=="selecting"?e:{status:"selecting",anchor:e.anchor,current:t.point};case"COMMIT":case"CANCEL":return pe}}var Ze=60,Qe=6e4,de=15;function qe(e,t,n){return Math.min(n,Math.max(t,e))}function me(e,t){let n=g(`${e}T00:00:00`);return new Date(n.getFullYear(),n.getMonth(),n.getDate(),0,t,0)}function ae(e,t,n=de){let a=t.dayStartHour*Ze,r=t.dayEndHour*Ze,i=a+qe(e,0,1)*t.windowMinutes,o=n>0?n:de,l=Math.round(i/o)*o;return qe(l,a,r)}function Oe(e,t,n){if(n===null)return se(e,t);let a=g(e.start),i=g(e.end).getTime()-a.getTime(),o=me(t,n),l=new Date(o.getTime()+i),s={id:e.id,start:k(o),end:k(l)};return e.revision!==void 0&&(s.revision=e.revision),s}function Fe(e,t,n,a,r={}){let i=(r.minDurationMinutes??de)*Qe,o=g(e.start),l=g(e.end),s=me(n,a),d=o,c=l;if(t==="end"){let u=o.getTime()+i;c=new Date(Math.max(s.getTime(),u))}else{let u=l.getTime()-i;d=new Date(Math.min(s.getTime(),u))}let b={id:e.id,start:k(d),end:k(c)};return e.revision!==void 0&&(b.revision=e.revision),b}function fe(e,t,n={}){let a=n.minDurationMinutes??de;if(e.minuteOfDay===null||t.minuteOfDay===null){let[d,c]=e.dateOnly<=t.dateOnly?[e.dateOnly,t.dateOnly]:[t.dateOnly,e.dateOnly],b=g(`${d}T00:00:00`),u=g(`${c}T00:00:00`),D=new Date(u.getFullYear(),u.getMonth(),u.getDate()+1);return{start:k(b),end:k(D),allDay:!0}}let i=me(e.dateOnly,e.minuteOfDay??0),o=me(t.dateOnly,t.minuteOfDay??0),l=i.getTime()<=o.getTime()?i:o,s=i.getTime()<=o.getTime()?o:i;return s.getTime()===l.getTime()&&(s=new Date(l.getTime()+a*Qe)),{start:k(l),end:k(s),allDay:!1}}var Ne={overrides:{},appliedRevision:{}};function wt(e,t){let n={...e};return delete n[t],n}function _e(e,t){switch(t.type){case"SUBMIT":{let n=t.baseRevision??Number.NEGATIVE_INFINITY,a=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;return{overrides:{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"pending",start:t.start,end:t.end,...t.baseRevision!==void 0?{revision:t.baseRevision}:{}}},appliedRevision:{...e.appliedRevision,[t.id]:Math.max(a,n)}}}case"RESOLVE":{let n=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;if(t.revision<=n)return e;let a=e.overrides[t.id];return{overrides:a!==void 0&&a.clientMutationId===t.clientMutationId&&a.status==="pending"?{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"committed",start:t.start,end:t.end,revision:t.revision}}:e.overrides,appliedRevision:{...e.appliedRevision,[t.id]:t.revision}}}case"REJECT":case"TIMEOUT":{let n=e.overrides[t.id];return!n||n.clientMutationId!==t.clientMutationId||n.status!=="pending"?e:{...e,overrides:{...e.overrides,[t.id]:{...n,status:"rolledback"}}}}case"CLEAR":{let n=e.overrides[t.id];return!n||t.clientMutationId&&n.clientMutationId!==t.clientMutationId?e:{...e,overrides:wt(e.overrides,t.id)}}}}function Ge(e,t){let n=new Set,a=new Set;return{events:e.map(i=>{let o=t.overrides[i.id];return o?o.status==="pending"?(n.add(i.id),{...i,start:o.start,end:o.end}):o.status==="rolledback"?(a.add(i.id),i):i.revision!==void 0&&o.revision!==void 0&&i.revision>=o.revision?i:{...i,start:o.start,end:o.end,...o.revision!==void 0?{revision:o.revision}:{}}:i}),pendingIds:n,rolledBackIds:a}}function ze(e,t){let n=new Map(e.map(r=>[r.id,r])),a=[];for(let[r,i]of Object.entries(t.overrides)){if(i.status!=="committed")continue;let o=n.get(r);o&&o.revision!==void 0&&i.revision!==void 0&&o.revision>=i.revision&&a.push(r)}return a}var q=60,Ct=24*q,St=864e5;function ye(e,t,n){return Math.min(n,Math.max(t,e))}function He(e={}){let t=e.dayStartHour,n=e.dayEndHour,a=Number.isFinite(t)&&t!==void 0?ye(Math.trunc(t),0,23):0,r=Number.isFinite(n)&&n!==void 0?ye(Math.trunc(n),1,24):24,[i,o]=r>a?[a,r]:[0,24];return{dayStartHour:i,dayEndHour:o,windowMinutes:(o-i)*q}}function tt(e){let t=[],n=[];for(let a of e)a.allDay===!0?t.push(a):n.push(a);return{allDay:t,timed:n}}function et(e,t){let n=g(e),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()),r=Math.round((a.getTime()-t.getTime())/St),i=n.getHours()*q+n.getMinutes()+n.getSeconds()/60;return r*Ct+i}function It(e,t){let n=g(e.start).getTime(),a=g(e.end).getTime(),r=g(t.start).getTime(),i=g(t.end).getTime();return n<i&&r<a}function le(e,t,n){let a=g(`${t}T00:00:00`),r=n.dayStartHour*q,i=n.dayEndHour*q,o=[...e].sort((u,D)=>{let m=g(u.start).getTime(),v=g(D.start).getTime();return m!==v?m-v:g(D.end).getTime()-g(u.end).getTime()}),l=[],s=[],d=[],c=Number.NEGATIVE_INFINITY,b=()=>{let u=s.length;for(let D of d)l[D].laneCount=u;s=[],d=[],c=Number.NEGATIVE_INFINITY};for(let u of o){let D=et(u.start,a),m=et(u.end,a);if(m<=r||D>=i)continue;let v=g(u.start).getTime(),$=g(u.end).getTime();d.length>0&&v>=c&&b();let T=s.findIndex(P=>!It(P,u));T===-1?(T=s.length,s.push(u)):s[T]=u;let C=ye(D,r,i),S=ye(m,C,i),E=(C-r)/n.windowMinutes,O=(S-C)/n.windowMinutes;d.push(l.length),l.push({event:u,lane:T,laneCount:1,topFraction:E,heightFraction:O}),c=Math.max(c,$)}return b(),l}function kt(e){let t=[];for(let n=e.dayStartHour;n<e.dayEndHour;n+=1)t.push({hour:n,topFraction:(n-e.dayStartHour)*q/e.windowMinutes});return t}function $e(e,t,n={}){let a="windowMinutes"in n?n:He(n),{allDay:r,timed:i}=tt(t),o=i.map(s=>({event:s,startTs:g(s.start).getTime(),endTs:g(s.end).getTime()}));return{columns:e.map(s=>{let d=g(`${s}T00:00:00`),c=d.getTime(),b=new Date(d.getFullYear(),d.getMonth(),d.getDate()+1).getTime(),u=o.filter(m=>m.startTs>=b?!1:m.endTs>c?!0:m.startTs===m.endTs&&m.startTs>=c).map(m=>m.event),D=r.filter(m=>V(m.start)<=s&&s<=V(m.end));return{dateOnly:s,allDay:D,timed:le(u,s,a)}}),hourMarks:kt(a),config:a}}function Be(e,t={}){let n="windowMinutes"in t?t:He(t),a=e.getHours()*q+e.getMinutes()+e.getSeconds()/60,r=n.dayStartHour*q,i=n.dayEndHour*q;return a<r||a>=i?null:(a-r)/n.windowMinutes}import*as ee from"react";import*as ve from"react";var Ye=new Date(2023,0,1);function nt(e,t){let n=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(a,r)=>{let i=(t+r)%7,o=new Date(Ye.getFullYear(),Ye.getMonth(),Ye.getDate()+i);return n.format(o)})}function at(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function rt(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(g(e))}function re(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(g(e))}function it(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(g(e))}import{jsx as te,jsxs as st}from"react/jsx-runtime";function Lt(...e){return e.filter(Boolean).join(" ")}function Pt(e,t,n){let{event:a,isContinuation:r,continuesAfter:i}=e;return a.allDay===!0?n.allDayLabel:r?i?n.continuesLabel:n.formatEndsLabel(re(a.end,t)):re(a.start,t)}function At({entry:e,locale:t,labels:n}){let{event:a,isContinuation:r,continuesAfter:i}=e,o=Pt(e,t,n),l=a.color?{"--ac-event-accent":a.color}:void 0;return st("li",{className:Lt("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":a.id,"aria-label":`${o} ${a.title}`,style:l,...a.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...i?{"data-continues-after":""}:{},children:[te("span",{className:"aethercal-agenda-event-time",children:o}),te("span",{className:"aethercal-agenda-event-title",children:a.title})]})}function ot({events:e,locale:t,allDayLabel:n,continuesLabel:a,formatEndsLabel:r,emptyLabel:i}){let o=ve.useMemo(()=>Le(e),[e]),l=ve.useId(),s={allDayLabel:n,continuesLabel:a,formatEndsLabel:r};return o.length===0?te("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:te("p",{className:"aethercal-agenda-empty",children:i})}):te("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:o.map(d=>{let c=`${l}-${d.date}`;return st("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":c,"data-date":d.date,children:[te("div",{className:"aethercal-agenda-day-title",id:c,children:it(d.date,t)}),te("ul",{className:"aethercal-agenda-day-events",role:"list",children:d.entries.map((b,u)=>te(At,{entry:b,locale:t,labels:s},`${b.event.id}-${u}`))})]},d.date)})})}import*as H from"react";import{jsx as dt,jsxs as Ft}from"react/jsx-runtime";function Ot(...e){return e.filter(Boolean).join(" ")}function he({event:e,timeLabel:t,onDragStart:n,onDragEnd:a,isPending:r,isRolledBack:i,onClick:o,onContextMenu:l}){let s=e.editable!==!1,d=e.color?{"--ac-event-accent":e.color}:void 0,c=t?`${t} ${e.title}`:e.title;return Ft("div",{className:Ot("aethercal-event",!s&&"is-locked",r&&"is-pending",i&&"is-rolledback"),draggable:s,"data-event-id":e.id,"aria-label":c,title:e.title,style:d,onDragStart:b=>{b.dataTransfer.setData("text/plain",e.id),b.dataTransfer.effectAllowed="move",n(e.id)},onDragEnd:a,onClick:o,onContextMenu:l?b=>{b.preventDefault(),b.stopPropagation(),l()}:void 0,children:[t?dt("time",{className:"aethercal-event-time",children:t}):null,dt("span",{className:"aethercal-event-title",children:e.title})]})}import{jsx as ie,jsxs as Ue}from"react/jsx-runtime";var lt=new Set;function ct(...e){return e.filter(Boolean).join(" ")}function Nt(e){let t=[];for(let n=0;n<e.length;n+=7)t.push(e.slice(n,n+7));return t}function _t(e){let t=new Map;for(let n of e){let a=V(n.start),r=t.get(a);r?r.push(n):t.set(a,[n])}return t}function ut(e){let{events:t,anchor:n,locale:a,firstDayOfWeek:r,weekdayLabels:i,maxEventsPerDay:o,formatMore:l,onEventDrop:s,onEventClick:d,onContextMenu:c,pendingIds:b=lt,rolledBackIds:u=lt}=e,D=H.useMemo(()=>Ie(n,r),[n,r]),m=H.useMemo(()=>Nt(D),[D]),v=H.useMemo(()=>i??nt(a,r),[i,a,r]),$=H.useMemo(()=>_t(t),[t]),T=n.getMonth(),C=V(k(new Date)),[S,E]=H.useReducer(Pe,ue),[O,P]=H.useState(()=>new Set),x=H.useCallback(Y=>{P(N=>{let M=new Set(N);return M.add(Y),M})},[]),B=H.useCallback(Y=>N=>{if(N.preventDefault(),!ge(S)){E({type:"DROP"});return}let M=S.eventId,U=N.dataTransfer.getData("text/plain");if(E({type:"DROP"}),U&&U!==M||!s)return;let _=t.find(K=>K.id===M);!_||_.editable===!1||s(se(_,Y))},[S,t,s]),W=!!s;return Ue("div",{className:ct("aethercal-calendar",ge(S)&&"is-dragging"),role:"grid","aria-label":at(n,a),"data-view":"month",children:[ie("div",{className:"aethercal-weekdays",role:"row",children:v.map((Y,N)=>ie("div",{role:"columnheader",className:"aethercal-weekday",children:Y},N))}),m.map((Y,N)=>ie("div",{className:"aethercal-week",role:"row",children:Y.map(M=>{let U=$.get(M)??[],_=O.has(M),K=_?U:U.slice(0,o),J=U.length-K.length,Re=new Date(`${M}T00:00:00`).getMonth()!==T;return Ue("div",{role:"gridcell",className:ct("aethercal-day",Re&&"is-outside",M===C&&"is-today"),"data-date":M,"aria-label":rt(M,a),onDragOver:W?L=>L.preventDefault():void 0,onDrop:W?B(M):void 0,onContextMenu:c?L=>{L.target===L.currentTarget&&(L.preventDefault(),c({start:`${M}T00:00:00`}))}:void 0,children:[ie("div",{className:"aethercal-day-head",children:ie("span",{className:"aethercal-day-number",children:Number(M.slice(-2))})}),Ue("div",{className:"aethercal-day-events",children:[K.map(L=>ie(he,{event:L,timeLabel:L.allDay?null:re(L.start,a),onDragStart:Me=>E({type:"DRAG_START",eventId:Me}),onDragEnd:()=>E({type:"DRAG_CANCEL"}),isPending:b.has(L.id),isRolledBack:u.has(L.id),...d?{onClick:()=>d({id:L.id})}:{},...c?{onContextMenu:()=>c({id:L.id})}:{}},L.id)),J>0&&!_?ie("button",{type:"button",className:"aethercal-more",onClick:()=>x(M),children:l(J)}):null]})]},M)})},N))]})}var gt="aethercal-calendar-styles",pt=`
:where(.aethercal-calendar) {
  --ac-font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --ac-fg: #1f2328;
  --ac-muted: #6b7280;
  --ac-faint: #9ca3af;
  --ac-bg: #ffffff;
  --ac-header-fg: #4b5563;
  --ac-border: #e5e7eb;
  --ac-cell-bg: #ffffff;
  --ac-cell-bg-outside: #fafafa;
  --ac-today-marker-bg: #111827;
  --ac-today-marker-fg: #ffffff;
  --ac-event-bg: #eef1f4;
  --ac-event-fg: #1f2328;
  --ac-event-accent: #64748b;
  --ac-more-fg: #4b5563;
  --ac-focus: #2563eb;
  --ac-rollback: #b91c1c;
  --ac-radius: 8px;
  --ac-cell-min-height: 96px;
}
.aethercal-calendar {
  font-family: var(--ac-font);
  color: var(--ac-fg);
  background: var(--ac-bg);
  border: 1px solid var(--ac-border);
  border-radius: var(--ac-radius);
  overflow: hidden;
  box-sizing: border-box;
}
.aethercal-calendar *,
.aethercal-calendar *::before,
.aethercal-calendar *::after { box-sizing: border-box; }
.aethercal-weekdays {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  border-bottom: 1px solid var(--ac-border);
}
.aethercal-weekday {
  padding: 8px 8px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.01em;
  color: var(--ac-header-fg);
  text-align: right;
}
.aethercal-weeks { display: grid; }
.aethercal-week {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
}
.aethercal-day {
  min-height: var(--ac-cell-min-height);
  border-right: 1px solid var(--ac-border);
  border-bottom: 1px solid var(--ac-border);
  padding: 4px 6px 6px;
  background: var(--ac-cell-bg);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.aethercal-week:last-child .aethercal-day { border-bottom: none; }
.aethercal-day:last-child { border-right: none; }
.aethercal-day.is-outside { background: var(--ac-cell-bg-outside); }
.aethercal-day.is-outside .aethercal-day-number { color: var(--ac-faint); }
.aethercal-day.is-drop-target { outline: 2px dashed var(--ac-focus); outline-offset: -2px; }
.aethercal-day-head { display: flex; justify-content: flex-end; }
.aethercal-day-number { font-size: 12px; color: var(--ac-muted); line-height: 22px; }
.aethercal-day.is-today .aethercal-day-number {
  min-width: 22px;
  height: 22px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--ac-today-marker-bg);
  color: var(--ac-today-marker-fg);
  text-align: center;
  font-weight: 600;
}
.aethercal-day-events { display: flex; flex-direction: column; gap: 2px; margin-top: 2px; min-height: 0; }
.aethercal-event {
  display: flex;
  gap: 6px;
  align-items: baseline;
  background: var(--ac-event-bg);
  color: var(--ac-event-fg);
  border-left: 3px solid var(--ac-event-accent);
  border-radius: calc(var(--ac-radius) - 3px);
  padding: 2px 6px;
  font-size: 12px;
  line-height: 1.4;
  cursor: grab;
  text-align: left;
  width: 100%;
  border-top: none;
  border-right: none;
  border-bottom: none;
}
.aethercal-event.is-locked { cursor: default; opacity: 0.75; }
.aethercal-event-time { color: var(--ac-muted); font-size: 11px; font-variant-numeric: tabular-nums; flex: none; }
.aethercal-event-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.aethercal-more {
  border: none;
  background: transparent;
  color: var(--ac-more-fg);
  font: inherit;
  font-size: 11px;
  text-align: left;
  padding: 1px 6px;
  cursor: pointer;
}
.aethercal-more:hover { text-decoration: underline; }
.aethercal-more:focus-visible { outline: 2px solid var(--ac-focus); outline-offset: 1px; border-radius: 3px; }
.aethercal-unavailable { padding: 24px; color: var(--ac-muted); font-family: var(--ac-font); }

.aethercal-agenda { display: block; }
.aethercal-agenda-empty {
  margin: 0;
  padding: 24px;
  text-align: center;
  color: var(--ac-muted);
  font-family: var(--ac-font);
}
.aethercal-agenda-day { border-bottom: 1px solid var(--ac-border); }
.aethercal-agenda-day:last-child { border-bottom: none; }
.aethercal-agenda-day-title {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 8px 12px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.01em;
  color: var(--ac-header-fg);
  background: var(--ac-bg);
  border-bottom: 1px solid var(--ac-border);
}
.aethercal-agenda-day-events { list-style: none; margin: 0; padding: 4px 0; display: flex; flex-direction: column; }
.aethercal-agenda-event {
  display: flex;
  gap: 10px;
  align-items: baseline;
  padding: 6px 12px;
  border-left: 3px solid var(--ac-event-accent);
  color: var(--ac-event-fg);
}
.aethercal-agenda-event.is-continuation { opacity: 0.8; }
.aethercal-agenda-event-time {
  flex: none;
  min-width: 76px;
  color: var(--ac-muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.aethercal-agenda-event-title {
  color: var(--ac-fg);
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Optimistic reconciliation affordances (F2-D, RF-21), shared by month chips & time-grid blocks.
   pending = an in-flight mutation (soft pulse); rolledback = a just-reverted mutation (brief flash).
   Both degrade to a static, motion-free cue under prefers-reduced-motion. */
@keyframes aethercal-pending-pulse {
  0%, 100% { opacity: 0.55; }
  50% { opacity: 0.85; }
}
@keyframes aethercal-rollback-flash {
  0% { box-shadow: 0 0 0 2px var(--ac-rollback); }
  100% { box-shadow: 0 0 0 0 transparent; }
}
.aethercal-event.is-pending,
.aethercal-tg-event.is-pending {
  animation: aethercal-pending-pulse 1.1s ease-in-out infinite;
  cursor: progress;
}
.aethercal-event.is-rolledback,
.aethercal-tg-event.is-rolledback {
  animation: aethercal-rollback-flash 0.5s ease-out;
  outline: 1px solid var(--ac-rollback);
  outline-offset: -1px;
}
@media (prefers-reduced-motion: reduce) {
  .aethercal-event.is-pending,
  .aethercal-tg-event.is-pending {
    animation: none;
    opacity: 0.6;
  }
  .aethercal-event.is-rolledback,
  .aethercal-tg-event.is-rolledback {
    animation: none;
  }
}
`;function ce(){if(typeof document>"u"||document.getElementById(gt))return;let e=document.createElement("style");e.id=gt,e.textContent=pt,document.head.appendChild(e)}import*as w from"react";var mt="All day";function ft(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(g(e))}function yt(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function vt(e,t){if(e.length===0)return"";let n=g(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(n);let a=g(e[e.length-1]),r=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${r.format(n)} \u2013 ${r.format(a)}`}var ht="aethercal-timegrid-styles",bt=`
:where(.aethercal-timegrid) {
  --ac-tg-gutter: 56px;
  --ac-tg-body-height: 640px;
  --ac-tg-hour-min-height: 44px;
  --ac-tg-line: var(--ac-border);
  --ac-tg-now: #dc2626;
  --ac-tg-event-bg: var(--ac-event-bg);
  --ac-tg-event-fg: var(--ac-event-fg);
  --ac-tg-event-accent: var(--ac-event-accent);
}
.aethercal-timegrid { display: flex; flex-direction: column; }
.aethercal-tg-head,
.aethercal-tg-allday,
.aethercal-tg-body {
  display: grid;
  grid-template-columns: var(--ac-tg-gutter) repeat(var(--ac-tg-cols, 7), minmax(0, 1fr));
}
.aethercal-tg-head { border-bottom: 1px solid var(--ac-border); }
.aethercal-tg-corner { border-right: 1px solid var(--ac-border); }
.aethercal-tg-colhead {
  padding: 8px 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ac-header-fg);
  text-align: center;
  border-right: 1px solid var(--ac-border);
}
.aethercal-tg-colhead:last-child { border-right: none; }
.aethercal-tg-colhead.is-today { color: var(--ac-fg); }
.aethercal-tg-colhead.is-today .aethercal-tg-colhead-date {
  display: inline-block;
  min-width: 22px;
  height: 22px;
  line-height: 22px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--ac-today-marker-bg);
  color: var(--ac-today-marker-fg);
}
.aethercal-tg-allday { border-bottom: 1px solid var(--ac-border); min-height: 28px; }
.aethercal-tg-rowhead {
  padding: 4px 6px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--ac-faint);
  border-right: 1px solid var(--ac-border);
  display: flex;
  align-items: center;
  justify-content: flex-end;
}
.aethercal-tg-allday-cell {
  padding: 3px 4px;
  border-right: 1px solid var(--ac-border);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.aethercal-tg-allday-cell:last-child { border-right: none; }
.aethercal-tg-body {
  position: relative;
  height: var(--ac-tg-body-height);
  overflow-y: auto;
}
.aethercal-tg-gutter { position: relative; border-right: 1px solid var(--ac-border); }
.aethercal-tg-hour {
  position: absolute;
  right: 6px;
  transform: translateY(-50%);
  font-size: 10px;
  color: var(--ac-faint);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.aethercal-tg-col {
  position: relative;
  border-right: 1px solid var(--ac-border);
  min-height: calc(var(--ac-tg-hours, 24) * var(--ac-tg-hour-min-height));
}
.aethercal-tg-col:last-child { border-right: none; }
.aethercal-tg-col.is-today { background: color-mix(in srgb, var(--ac-today-marker-bg) 4%, transparent); }
.aethercal-tg-col.is-drop-target { outline: 2px dashed var(--ac-focus); outline-offset: -2px; }
.aethercal-tg-line {
  position: absolute;
  left: 0;
  right: 0;
  border-top: 1px solid var(--ac-tg-line);
  pointer-events: none;
}
.aethercal-tg-event {
  position: absolute;
  overflow: hidden;
  box-sizing: border-box;
  padding: 2px 6px;
  border-radius: calc(var(--ac-radius) - 4px);
  border-left: 3px solid var(--ac-tg-event-accent);
  background: var(--ac-tg-event-bg);
  color: var(--ac-tg-event-fg);
  font-size: 11px;
  line-height: 1.3;
  cursor: grab;
  min-height: 14px;
}
.aethercal-tg-event.is-locked { cursor: default; opacity: 0.75; }
.aethercal-tg-event-time { color: var(--ac-muted); font-size: 10px; font-variant-numeric: tabular-nums; }
.aethercal-tg-event-title {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.aethercal-now-indicator {
  position: absolute;
  left: 0;
  right: 0;
  height: 0;
  border-top: 2px solid var(--ac-tg-now);
  pointer-events: none;
  z-index: 2;
}
.aethercal-now-indicator::before {
  content: "";
  position: absolute;
  left: -3px;
  top: -4px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--ac-tg-now);
}
/* Resize handles (F2-D): thin grab strips on the block's top/bottom edges, only rendered when the
   event is editable and an onEventResize handler is wired (no dishonest affordance otherwise). */
.aethercal-tg-resize-handle {
  position: absolute;
  left: 0;
  right: 0;
  height: 7px;
  cursor: ns-resize;
  touch-action: none;
  z-index: 3;
}
.aethercal-tg-resize-handle-start { top: -3px; }
.aethercal-tg-resize-handle-end { bottom: -3px; }
.aethercal-tg-event.is-resizing { outline: 1px dashed var(--ac-focus); outline-offset: -1px; }
/* Live band drawn while drag-selecting empty space to create a new event (F2-D). */
.aethercal-tg-select-band {
  position: absolute;
  left: 2px;
  right: 2px;
  min-height: 4px;
  background: color-mix(in srgb, var(--ac-focus) 16%, transparent);
  border: 1px solid var(--ac-focus);
  border-radius: 4px;
  pointer-events: none;
  z-index: 1;
}
`;function Ve(){if(typeof document>"u"||document.getElementById(ht))return;let e=document.createElement("style");e.id=ht,e.textContent=bt,document.head.appendChild(e)}import{Fragment as Gt,jsx as A,jsxs as oe}from"react/jsx-runtime";function be(...e){return e.filter(Boolean).join(" ")}var Q=e=>`${e*100}%`,Dt=new Set;function De(e,t){let n=t.getBoundingClientRect();return n.height>0?(e-n.top)/n.height:0}function We(e){let{view:t,days:n,events:a,locale:r,config:i,now:o,allDayLabel:l=mt,onEventDrop:s,onEventResize:d,onRangeSelect:c,onEventClick:b,onContextMenu:u,pendingIds:D=Dt,rolledBackIds:m=Dt}=e;w.useEffect(()=>{ce(),Ve()},[]);let v=w.useMemo(()=>$e(n,a,i),[n,a,i]),$=w.useMemo(()=>Be(o,i),[o,i]),T=w.useMemo(()=>V(k(o)),[o]),[C,S]=w.useReducer(Ae,pe),E=w.useRef(null),[O,P]=w.useState(null),[x,B]=w.useState(null),W=!!s,Y=!!d,N=!!c,M=C.status==="dragging",U=w.useCallback((f,h)=>p=>{if(p.preventDefault(),C.status!=="dragging"){S({type:"COMMIT"});return}let I=C.eventId,G=p.dataTransfer.getData("text/plain");if(S({type:"COMMIT"}),G&&G!==I||!s)return;let R=a.find(X=>X.id===I);if(!R||R.editable===!1)return;let y=null;if(h&&R.allDay!==!0){let z=p.currentTarget.getBoundingClientRect();z.height>0&&Number.isFinite(p.clientY)&&(y=ae((p.clientY-z.top)/z.height,v.config))}s(Oe(R,f,y))},[C,a,s,v.config]),_=w.useCallback(f=>{E.current?.kind!=="resize"&&S({type:"DRAG_START",eventId:f})},[]),K=w.useCallback(()=>S({type:"CANCEL"}),[]),J=w.useCallback((f,h)=>p=>{if(!d||f.editable===!1||p.button!==0)return;let I=p.currentTarget.closest(".aethercal-tg-col");I?.dataset.date&&(p.preventDefault(),p.stopPropagation(),E.current={kind:"resize",pointerId:p.pointerId,eventId:f.id,edge:h,dateOnly:I.dataset.date,colEl:I,payload:null},p.currentTarget.setPointerCapture?.(p.pointerId),S({type:"RESIZE_START",eventId:f.id,edge:h}))},[d]),Re=w.useCallback(f=>h=>{if(!c||h.button!==0||h.target!==h.currentTarget)return;let p=h.currentTarget,I=ae(De(h.clientY,p),v.config);E.current={kind:"select",pointerId:h.pointerId,anchorDate:f,anchorCol:p,anchorMinute:I,currentDate:f,currentCol:p,currentMinute:I},S({type:"SELECT_START",point:{dateOnly:f,minuteOfDay:I}})},[c,v.config]),Te=C.status==="resizing"||C.status==="selecting";w.useEffect(()=>{if(!Te)return;let f=R=>{let y=E.current;if(!(!y||R.pointerId!==y.pointerId))if(y.kind==="resize"){let X=ae(De(R.clientY,y.colEl),v.config),z=a.find(j=>j.id===y.eventId);if(!z)return;let ne=Fe(z,y.edge,y.dateOnly,X);y.payload=ne,P(ne)}else{let X=document.elementFromPoint(R.clientX,R.clientY)?.closest(".aethercal-tg-col"),z=X?.dataset.date?X:y.currentCol;y.currentCol=z,y.currentDate=z.dataset.date??y.anchorDate,y.currentMinute=ae(De(R.clientY,z),v.config);let ne=fe({dateOnly:y.anchorDate,minuteOfDay:y.anchorMinute},{dateOnly:y.currentDate,minuteOfDay:y.currentMinute}),we=(y.currentDate===y.anchorDate?le([{id:"__sel",title:"",start:ne.start,end:ne.end}],y.anchorDate,v.config):[])[0];B(we?{dateOnly:y.anchorDate,topFraction:we.topFraction,heightFraction:we.heightFraction}:null)}},h=R=>{let y=E.current;E.current=null,P(null),B(null),R&&y&&(y.kind==="resize"&&y.payload&&d&&d(y.payload),y.kind==="select"&&c&&(y.currentDate!==y.anchorDate||y.currentMinute!==y.anchorMinute)&&c(fe({dateOnly:y.anchorDate,minuteOfDay:y.anchorMinute},{dateOnly:y.currentDate,minuteOfDay:y.currentMinute}))),S({type:R?"COMMIT":"CANCEL"})},p=R=>{E.current&&R.pointerId!==E.current.pointerId||h(!0)},I=R=>{E.current&&R.pointerId!==E.current.pointerId||h(!1)},G=R=>{R.key==="Escape"&&h(!1)};return window.addEventListener("pointermove",f),window.addEventListener("pointerup",p),window.addEventListener("pointercancel",I),window.addEventListener("keydown",G),()=>{window.removeEventListener("pointermove",f),window.removeEventListener("pointerup",p),window.removeEventListener("pointercancel",I),window.removeEventListener("keydown",G)}},[Te,a,v.config,d,c]);let L=w.useCallback((f,h)=>p=>{if(!u||p.target!==p.currentTarget)return;if(p.preventDefault(),!h){u({start:`${f}T00:00:00`});return}let I=ae(De(p.clientY,p.currentTarget),v.config),G=g(`${f}T00:00:00`),R=new Date(G.getFullYear(),G.getMonth(),G.getDate(),0,I,0);u({start:k(R)})},[u,v.config]),Me={"--ac-tg-cols":v.columns.length,"--ac-tg-hours":v.config.dayEndHour-v.config.dayStartHour};return oe("div",{className:be("aethercal-calendar","aethercal-timegrid",M&&"is-dragging",C.status==="resizing"&&"is-resizing",C.status==="selecting"&&"is-selecting"),role:"grid","aria-label":vt(n,r),"data-view":t,style:Me,children:[oe("div",{className:"aethercal-tg-head",role:"row",children:[A("div",{className:"aethercal-tg-corner"}),v.columns.map(f=>A("div",{role:"columnheader",className:be("aethercal-tg-colhead",f.dateOnly===T&&"is-today"),"data-date":f.dateOnly,children:A("span",{className:"aethercal-tg-colhead-date",children:ft(f.dateOnly,r)})},f.dateOnly))]}),oe("div",{className:"aethercal-tg-allday",role:"row",children:[A("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:l}),v.columns.map(f=>A("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":f.dateOnly,onDragOver:W?h=>h.preventDefault():void 0,onDrop:W?U(f.dateOnly,!1):void 0,onContextMenu:u?L(f.dateOnly,!1):void 0,children:f.allDay.map(h=>A(he,{event:h,timeLabel:null,onDragStart:_,onDragEnd:K,isPending:D.has(h.id),isRolledBack:m.has(h.id),...b?{onClick:()=>b({id:h.id})}:{},...u?{onContextMenu:()=>u({id:h.id})}:{}},h.id))},f.dateOnly))]}),oe("div",{className:"aethercal-tg-body",role:"row",children:[A("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:v.hourMarks.map(f=>A("div",{className:"aethercal-tg-hour",style:{top:Q(f.topFraction)},children:yt(f.hour,r)},f.hour))}),v.columns.map(f=>oe("div",{role:"gridcell",className:be("aethercal-tg-col",f.dateOnly===T&&"is-today"),"data-date":f.dateOnly,onDragOver:W?h=>h.preventDefault():void 0,onDrop:W?U(f.dateOnly,!0):void 0,onPointerDown:N?Re(f.dateOnly):void 0,onContextMenu:u?L(f.dateOnly,!0):void 0,children:[v.hourMarks.map(h=>A("div",{className:"aethercal-tg-line",style:{top:Q(h.topFraction)},"aria-hidden":"true"},h.hour)),x&&x.dateOnly===f.dateOnly?A("div",{className:"aethercal-tg-select-band",style:{top:Q(x.topFraction),height:Q(x.heightFraction)},"aria-hidden":"true"}):null,f.timed.map(h=>{let{event:p}=h,I=p.editable!==!1,G=re(p.start,r),R=O?.id===p.id?O:null,y=R?le([{...p,start:R.start,end:R.end}],f.dateOnly,v.config)[0]:void 0,X=y?y.topFraction:h.topFraction,z=y?y.heightFraction:h.heightFraction,ne={top:Q(X),height:Q(z),left:Q(h.lane/h.laneCount),width:Q(1/h.laneCount),...p.color?{"--ac-tg-event-accent":p.color}:{}};return oe("div",{className:be("aethercal-tg-event",!I&&"is-locked",D.has(p.id)&&"is-pending",m.has(p.id)&&"is-rolledback",!!R&&"is-resizing"),draggable:I,"data-event-id":p.id,"data-lane":h.lane,"data-lane-count":h.laneCount,"aria-label":`${G} ${p.title}`,title:p.title,style:ne,onDragStart:j=>{if(E.current?.kind==="resize"){j.preventDefault();return}j.dataTransfer.setData("text/plain",p.id),j.dataTransfer.effectAllowed="move",_(p.id)},onDragEnd:K,onClick:b?()=>b({id:p.id}):void 0,onContextMenu:u?j=>{j.preventDefault(),j.stopPropagation(),u({id:p.id})}:void 0,children:[A("time",{className:"aethercal-tg-event-time",children:G}),A("span",{className:"aethercal-tg-event-title",children:p.title}),Y&&I?oe(Gt,{children:[A("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:J(p,"start")}),A("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:J(p,"end")})]}):null]},p.id)}),$!==null&&f.dateOnly===T?A("div",{className:"aethercal-now-indicator",style:{top:Q($)},"aria-hidden":"true"}):null]},f.dateOnly))]})]})}import{jsx as Ee}from"react/jsx-runtime";function zt(e){return e instanceof Date?e:typeof e=="string"?g(e):new Date}function Ht(e){return e instanceof Date?e:typeof e=="string"?g(e):new Date}var $t=e=>`+${e} more`,Bt=e=>`ends ${e}`;function xe(e){let{view:t="month",events:n,anchor:a,locale:r="en",firstDayOfWeek:i=1,maxEventsPerDay:o=3,weekdayLabels:l,formatMore:s=$t,unavailableLabel:d="This view is not available yet.",dayStartHour:c,dayEndHour:b,allDayLabel:u="All day",now:D,continuesLabel:m="Continues",formatEndsLabel:v=Bt,agendaEmptyLabel:$="No events",onEventDrop:T,onEventResize:C,onRangeSelect:S,onEventClick:E,onContextMenu:O,pendingIds:P,rolledBackIds:x}=e;ee.useEffect(()=>{ce()},[]);let B=ee.useMemo(()=>zt(a),[a]),[W,Y]=ee.useState(()=>new Date);ee.useEffect(()=>{if(D!==void 0||t!=="week"&&t!=="day")return;let J=setInterval(()=>Y(new Date),6e4);return()=>clearInterval(J)},[D,t]);let N=ee.useMemo(()=>D!==void 0?Ht(D):W,[D,W]),M=Number.isInteger(i)&&i>=0&&i<=6?i:1,U=Number.isInteger(o)&&o>=0?o:3,_=l&&l.length===7?l:void 0,K=ee.useMemo(()=>({...c!==void 0?{dayStartHour:c}:{},...b!==void 0?{dayEndHour:b}:{}}),[c,b]);if(t==="list")return Ee(ot,{events:n??[],locale:r,allDayLabel:u,continuesLabel:m,formatEndsLabel:v,emptyLabel:$});if(t==="month")return Ee(ut,{events:n??[],anchor:B,locale:r,firstDayOfWeek:M,maxEventsPerDay:U,formatMore:s,..._?{weekdayLabels:_}:{},...T?{onEventDrop:T}:{},...E?{onEventClick:E}:{},...O?{onContextMenu:O}:{},...P?{pendingIds:P}:{},...x?{rolledBackIds:x}:{}});if(t==="week"||t==="day"){let J=t==="week"?Se(B,M):[V(k(B))];return Ee(We,{view:t,days:J,events:n??[],locale:r,config:K,now:N,allDayLabel:u,...T?{onEventDrop:T}:{},...C?{onEventResize:C}:{},...S?{onRangeSelect:S}:{},...E?{onEventClick:E}:{},...O?{onContextMenu:O}:{},...P?{pendingIds:P}:{},...x?{rolledBackIds:x}:{}})}return Ee("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,children:d})}var Yt=xe;import*as F from"react";function Ut(){return typeof crypto<"u"&&typeof crypto.randomUUID=="function"?crypto.randomUUID():`cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}var Vt=8e3,Wt=900;function Ke(e){let{events:t,mutate:n,timeoutMs:a=Vt,rollbackFlashMs:r=Wt,generateId:i=Ut}=e,[o,l]=F.useReducer(_e,Ne),s=F.useRef(t);s.current=t;let d=F.useRef(!0),c=F.useRef(new Map);F.useEffect(()=>{d.current=!0;let D=c.current;return()=>{d.current=!1;for(let m of D.values())clearTimeout(m);D.clear()}},[]),F.useEffect(()=>{for(let D of ze(t,o)){let m=o.overrides[D];l({type:"CLEAR",id:D,...m?{clientMutationId:m.clientMutationId}:{}})}},[t,o]);let b=F.useCallback((D,m)=>{let v=i(),$=s.current.find(x=>x.id===m.id),T=c.current,C=x=>{let B=T.get(x);B!==void 0&&(clearTimeout(B),T.delete(x))},S=()=>{T.set(`fl:${v}`,setTimeout(()=>{T.delete(`fl:${v}`),d.current&&l({type:"CLEAR",id:m.id,clientMutationId:v})},r))};l({type:"SUBMIT",id:m.id,clientMutationId:v,start:m.start,end:m.end,...$?.revision!==void 0?{baseRevision:$.revision}:{}}),T.set(`to:${v}`,setTimeout(()=>{T.delete(`to:${v}`),d.current&&(l({type:"TIMEOUT",id:m.id,clientMutationId:v}),S())},a));let E=()=>{C(`to:${v}`),d.current&&(l({type:"REJECT",id:m.id,clientMutationId:v}),S())},O={kind:D,clientMutationId:v,payload:{...m,client_mutation_id:v}},P;try{P=n(O)}catch(x){P=Promise.reject(x instanceof Error?x:new Error(String(x)))}P.then(x=>{if(x.id!==m.id){E();return}C(`to:${v}`),d.current&&l({type:"RESOLVE",id:x.id,clientMutationId:v,start:x.start,end:x.end,revision:x.revision})}).catch(E)},[n,a,r,i]),u=F.useMemo(()=>Ge(t,o),[t,o]);return{events:u.events,pendingIds:u.pendingIds,rolledBackIds:u.rolledBackIds,submit:b}}import{jsx as Jt}from"react/jsx-runtime";function Kt({events:e,mutate:t,timeoutMs:n,rollbackFlashMs:a,generateId:r,...i}){let{events:o,pendingIds:l,rolledBackIds:s,submit:d}=Ke({events:e,mutate:t,...n!==void 0?{timeoutMs:n}:{},...a!==void 0?{rollbackFlashMs:a}:{},...r?{generateId:r}:{}});return Jt(xe,{...i,events:o,pendingIds:l,rolledBackIds:s,onEventDrop:c=>d("drop",c),onEventResize:c=>d("resize",c)})}export{xe as AetherCalendar,pt as CALENDAR_CSS,Kt as OptimisticCalendar,bt as TIME_GRID_CSS,We as TimeGridView,Yt as default,ce as ensureCalendarStyles,Ve as ensureTimeGridStyles,Ke as useOptimisticEvents};
