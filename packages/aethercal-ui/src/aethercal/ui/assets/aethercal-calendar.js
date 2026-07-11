function Z(e){return String(e).padStart(2,"0")}function g(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,n,a,r,i,o,c]=t,d=Number(n),s=Number(a),l=Number(r),m=Number(i??"0"),u=Number(o??"0"),D=Number(c??"0");if(s<1||s>12||l<1||l>31||m>23||u>59||D>59)throw new Error(`out-of-range ISO datetime: ${e}`);let f=new Date(d,s-1,l,m,u,D);if(f.getFullYear()!==d||f.getMonth()!==s-1||f.getDate()!==l)throw new Error(`nonexistent calendar date: ${e}`);return f}function k(e){return`${e.getFullYear()}-${Z(e.getMonth()+1)}-${Z(e.getDate())}T${Z(e.getHours())}:${Z(e.getMinutes())}:${Z(e.getSeconds())}`}function V(e){let t=g(e);return`${t.getFullYear()}-${Z(t.getMonth()+1)}-${Z(t.getDate())}`}function Rt(e,t){return(e.getDay()-t+7)%7}function we(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate());return n.setDate(n.getDate()-Rt(n,t)),n}function je(e,t){return Array.from({length:t},(n,a)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+a);return`${r.getFullYear()}-${Z(r.getMonth()+1)}-${Z(r.getDate())}`})}function Ce(e,t=1){return je(we(e,t),7)}function Se(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),1);return je(we(n,t),42)}function Ie(e,t){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()),a=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((a.getTime()-n.getTime())/864e5)}function de(e,t){let n=g(e.start),a=g(e.end),r=g(t),i=Ie(n,r),o=new Date(n.getFullYear(),n.getMonth(),n.getDate()+i,n.getHours(),n.getMinutes(),n.getSeconds()),c=new Date(a.getFullYear(),a.getMonth(),a.getDate()+i,a.getHours(),a.getMinutes(),a.getSeconds()),d={id:e.id,start:k(o),end:k(c)};return e.revision!==void 0&&(d.revision=e.revision),d}var Tt=370;function Ze(e){return String(e).padStart(2,"0")}function ke(e){return`${e.getFullYear()}-${Ze(e.getMonth()+1)}-${Ze(e.getDate())}`}function qe(e){return new Date(e.getFullYear(),e.getMonth(),e.getDate())}function Mt(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function wt(e){let t=g(e.start),n=g(e.end),a=qe(t),r;n.getTime()<=t.getTime()?r=a:(r=qe(new Date(n.getTime()-1)),r.getTime()<a.getTime()&&(r=a));let i=[],o=a;for(let c=0;c<Tt&&o.getTime()<=r.getTime();c+=1)i.push(ke(o)),o=Mt(o,1);return{keys:i,startKey:ke(a),lastKey:ke(r)}}function Le(e){let t=new Map;return e.forEach((n,a)=>{let{keys:r,startKey:i,lastKey:o}=wt(n),c=g(n.start).getTime(),d=g(n.end).getTime();for(let s of r){let l={entry:{event:n,isContinuation:s!==i,continuesAfter:s!==o},startMs:c,endMs:d,index:a},m=t.get(s);m?m.push(l):t.set(s,[l])}}),[...t.keys()].sort().map(n=>{let a=t.get(n);return a.sort((r,i)=>r.startMs-i.startMs||r.endMs-i.endMs||r.index-i.index),{date:n,entries:a.map(r=>r.entry)}})}var ge={status:"idle"};function pe(e){return e.status==="dragging"}function Pe(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return ge}}var me={status:"idle"};function Ae(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"RESIZE_START":return{status:"resizing",eventId:t.eventId,edge:t.edge};case"SELECT_START":return{status:"selecting",anchor:t.point,current:t.point};case"SELECT_MOVE":return e.status!=="selecting"?e:{status:"selecting",anchor:e.anchor,current:t.point};case"COMMIT":case"CANCEL":return me}}var Oe=60,et=6e4,le=15;function Qe(e,t,n){return Math.min(n,Math.max(t,e))}function Fe(e,t){let n=g(`${e}T00:00:00`);return new Date(n.getFullYear(),n.getMonth(),n.getDate(),0,t,0)}function ne(e,t,n=le){let a=t.dayStartHour*Oe,r=t.dayEndHour*Oe,i=a+Qe(e,0,1)*t.windowMinutes,o=n>0?n:le,c=a+Math.round((i-a)/o)*o;return Qe(c,a,r)}function Ne(e,t,n){if(n===null)return de(e,t);let a=g(e.start),r=g(e.end),i=g(`${t}T00:00:00`),o=Ie(a,i),c=a.getHours()*Oe+a.getMinutes(),d=n-c,s=m=>new Date(m.getFullYear(),m.getMonth(),m.getDate()+o,m.getHours(),m.getMinutes()+d,m.getSeconds()),l={id:e.id,start:k(s(a)),end:k(s(r))};return e.revision!==void 0&&(l.revision=e.revision),l}function _e(e,t,n,a,r={}){let i=(r.minDurationMinutes??le)*et,o=g(e.start),c=g(e.end),d=Fe(n,a),s=o,l=c;if(t==="end"){let u=o.getTime()+i;l=new Date(Math.max(d.getTime(),u))}else{let u=c.getTime()-i;s=new Date(Math.min(d.getTime(),u))}let m={id:e.id,start:k(s),end:k(l)};return e.revision!==void 0&&(m.revision=e.revision),m}function fe(e,t,n={}){let a=n.minDurationMinutes??le;if(e.minuteOfDay===null||t.minuteOfDay===null){let[s,l]=e.dateOnly<=t.dateOnly?[e.dateOnly,t.dateOnly]:[t.dateOnly,e.dateOnly],m=g(`${s}T00:00:00`),u=g(`${l}T00:00:00`),D=new Date(u.getFullYear(),u.getMonth(),u.getDate()+1);return{start:k(m),end:k(D),allDay:!0}}let i=Fe(e.dateOnly,e.minuteOfDay??0),o=Fe(t.dateOnly,t.minuteOfDay??0),c=i.getTime()<=o.getTime()?i:o,d=i.getTime()<=o.getTime()?o:i;return d.getTime()===c.getTime()&&(d=new Date(c.getTime()+a*et)),{start:k(c),end:k(d),allDay:!1}}var Ge={overrides:{},appliedRevision:{}};function Ct(e,t){let n={...e};return delete n[t],n}function ze(e,t){switch(t.type){case"SUBMIT":{let n=t.baseRevision??Number.NEGATIVE_INFINITY,a=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;return{overrides:{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"pending",start:t.start,end:t.end,...t.baseRevision!==void 0?{revision:t.baseRevision}:{}}},appliedRevision:{...e.appliedRevision,[t.id]:Math.max(a,n)}}}case"RESOLVE":{let n=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;if(t.revision<=n)return e;let a=e.overrides[t.id];return{overrides:a!==void 0&&a.clientMutationId===t.clientMutationId&&a.status==="pending"?{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"committed",start:t.start,end:t.end,revision:t.revision}}:e.overrides,appliedRevision:{...e.appliedRevision,[t.id]:t.revision}}}case"REJECT":case"TIMEOUT":{let n=e.overrides[t.id];return!n||n.clientMutationId!==t.clientMutationId||n.status!=="pending"?e:{...e,overrides:{...e.overrides,[t.id]:{...n,status:"rolledback"}}}}case"CLEAR":{let n=e.overrides[t.id];return!n||t.clientMutationId&&n.clientMutationId!==t.clientMutationId?e:{...e,overrides:Ct(e.overrides,t.id)}}}}function He(e,t){let n=new Set,a=new Set;return{events:e.map(i=>{let o=t.overrides[i.id];return o?o.status==="pending"?(n.add(i.id),{...i,start:o.start,end:o.end}):o.status==="rolledback"?(a.add(i.id),i):i.revision!==void 0&&o.revision!==void 0&&i.revision>=o.revision?i:{...i,start:o.start,end:o.end,...o.revision!==void 0?{revision:o.revision}:{}}:i}),pendingIds:n,rolledBackIds:a}}function $e(e,t){let n=new Map(e.map(r=>[r.id,r])),a=[];for(let[r,i]of Object.entries(t.overrides)){if(i.status!=="committed")continue;let o=n.get(r);o&&o.revision!==void 0&&i.revision!==void 0&&o.revision>=i.revision&&a.push(r)}return a}var q=60,St=24*q,It=864e5;function ye(e,t,n){return Math.min(n,Math.max(t,e))}function Ye(e={}){let t=e.dayStartHour,n=e.dayEndHour,a=Number.isFinite(t)&&t!==void 0?ye(Math.trunc(t),0,23):0,r=Number.isFinite(n)&&n!==void 0?ye(Math.trunc(n),1,24):24,[i,o]=r>a?[a,r]:[0,24];return{dayStartHour:i,dayEndHour:o,windowMinutes:(o-i)*q}}function nt(e){let t=[],n=[];for(let a of e)a.allDay===!0?t.push(a):n.push(a);return{allDay:t,timed:n}}function tt(e,t){let n=g(e),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()),r=Math.round((a.getTime()-t.getTime())/It),i=n.getHours()*q+n.getMinutes()+n.getSeconds()/60;return r*St+i}function kt(e,t){let n=g(e.start).getTime(),a=g(e.end).getTime(),r=g(t.start).getTime(),i=g(t.end).getTime();return n<i&&r<a}function ce(e,t,n){let a=g(`${t}T00:00:00`),r=n.dayStartHour*q,i=n.dayEndHour*q,o=[...e].sort((u,D)=>{let f=g(u.start).getTime(),h=g(D.start).getTime();return f!==h?f-h:g(D.end).getTime()-g(u.end).getTime()}),c=[],d=[],s=[],l=Number.NEGATIVE_INFINITY,m=()=>{let u=d.length;for(let D of s)c[D].laneCount=u;d=[],s=[],l=Number.NEGATIVE_INFINITY};for(let u of o){let D=tt(u.start,a),f=tt(u.end,a);if(f<=r||D>=i)continue;let h=g(u.start).getTime(),$=g(u.end).getTime();s.length>0&&h>=l&&m();let T=d.findIndex(P=>!kt(P,u));T===-1?(T=d.length,d.push(u)):d[T]=u;let C=ye(D,r,i),S=ye(f,C,i),x=(C-r)/n.windowMinutes,O=(S-C)/n.windowMinutes;s.push(c.length),c.push({event:u,lane:T,laneCount:1,topFraction:x,heightFraction:O}),l=Math.max(l,$)}return m(),c}function Lt(e){let t=[];for(let n=e.dayStartHour;n<e.dayEndHour;n+=1)t.push({hour:n,topFraction:(n-e.dayStartHour)*q/e.windowMinutes});return t}function Be(e,t,n={}){let a="windowMinutes"in n?n:Ye(n),{allDay:r,timed:i}=nt(t),o=i.map(d=>({event:d,startTs:g(d.start).getTime(),endTs:g(d.end).getTime()}));return{columns:e.map(d=>{let s=g(`${d}T00:00:00`),l=s.getTime(),m=new Date(s.getFullYear(),s.getMonth(),s.getDate()+1).getTime(),u=o.filter(f=>f.startTs>=m?!1:f.endTs>l?!0:f.startTs===f.endTs&&f.startTs>=l).map(f=>f.event),D=r.filter(f=>V(f.start)<=d&&d<=V(f.end));return{dateOnly:d,allDay:D,timed:ce(u,d,a)}}),hourMarks:Lt(a),config:a}}function Ue(e,t={}){let n="windowMinutes"in t?t:Ye(t),a=e.getHours()*q+e.getMinutes()+e.getSeconds()/60,r=n.dayStartHour*q,i=n.dayEndHour*q;return a<r||a>=i?null:(a-r)/n.windowMinutes}import*as ee from"react";import*as ve from"react";var Ve=new Date(2023,0,1);function at(e,t){let n=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(a,r)=>{let i=(t+r)%7,o=new Date(Ve.getFullYear(),Ve.getMonth(),Ve.getDate()+i);return n.format(o)})}function rt(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function it(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(g(e))}function ae(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(g(e))}function ot(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(g(e))}import{jsx as te,jsxs as dt}from"react/jsx-runtime";function Pt(...e){return e.filter(Boolean).join(" ")}function At(e,t,n){let{event:a,isContinuation:r,continuesAfter:i}=e;return a.allDay===!0?n.allDayLabel:r?i?n.continuesLabel:n.formatEndsLabel(ae(a.end,t)):ae(a.start,t)}function Ot({entry:e,locale:t,labels:n}){let{event:a,isContinuation:r,continuesAfter:i}=e,o=At(e,t,n),c=a.color?{"--ac-event-accent":a.color}:void 0;return dt("li",{className:Pt("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":a.id,"aria-label":`${o} ${a.title}`,style:c,...a.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...i?{"data-continues-after":""}:{},children:[te("span",{className:"aethercal-agenda-event-time",children:o}),te("span",{className:"aethercal-agenda-event-title",children:a.title})]})}function st({events:e,locale:t,allDayLabel:n,continuesLabel:a,formatEndsLabel:r,emptyLabel:i}){let o=ve.useMemo(()=>Le(e),[e]),c=ve.useId(),d={allDayLabel:n,continuesLabel:a,formatEndsLabel:r};return o.length===0?te("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:te("p",{className:"aethercal-agenda-empty",children:i})}):te("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:o.map(s=>{let l=`${c}-${s.date}`;return dt("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":l,"data-date":s.date,children:[te("div",{className:"aethercal-agenda-day-title",id:l,children:ot(s.date,t)}),te("ul",{className:"aethercal-agenda-day-events",role:"list",children:s.entries.map((m,u)=>te(Ot,{entry:m,locale:t,labels:d},`${m.event.id}-${u}`))})]},s.date)})})}import*as H from"react";import{jsx as lt,jsxs as Nt}from"react/jsx-runtime";function Ft(...e){return e.filter(Boolean).join(" ")}function he({event:e,timeLabel:t,onDragStart:n,onDragEnd:a,isPending:r,isRolledBack:i,onClick:o,onContextMenu:c}){let d=e.editable!==!1,s=e.color?{"--ac-event-accent":e.color}:void 0,l=t?`${t} ${e.title}`:e.title;return Nt("div",{className:Ft("aethercal-event",!d&&"is-locked",r&&"is-pending",i&&"is-rolledback"),draggable:d,"data-event-id":e.id,"aria-label":l,title:e.title,style:s,onDragStart:m=>{m.dataTransfer.setData("text/plain",e.id),m.dataTransfer.effectAllowed="move",n(e.id)},onDragEnd:a,onClick:o,onContextMenu:c?m=>{m.preventDefault(),m.stopPropagation(),c()}:void 0,children:[t?lt("time",{className:"aethercal-event-time",children:t}):null,lt("span",{className:"aethercal-event-title",children:e.title})]})}import{jsx as re,jsxs as We}from"react/jsx-runtime";var ct=new Set;function ut(...e){return e.filter(Boolean).join(" ")}function _t(e){let t=[];for(let n=0;n<e.length;n+=7)t.push(e.slice(n,n+7));return t}function Gt(e){let t=new Map;for(let n of e){let a=V(n.start),r=t.get(a);r?r.push(n):t.set(a,[n])}return t}function gt(e){let{events:t,anchor:n,locale:a,firstDayOfWeek:r,weekdayLabels:i,maxEventsPerDay:o,formatMore:c,onEventDrop:d,onEventClick:s,onContextMenu:l,pendingIds:m=ct,rolledBackIds:u=ct}=e,D=H.useMemo(()=>Se(n,r),[n,r]),f=H.useMemo(()=>_t(D),[D]),h=H.useMemo(()=>i??at(a,r),[i,a,r]),$=H.useMemo(()=>Gt(t),[t]),T=n.getMonth(),C=V(k(new Date)),[S,x]=H.useReducer(Pe,ge),[O,P]=H.useState(()=>new Set),R=H.useCallback(B=>{P(N=>{let M=new Set(N);return M.add(B),M})},[]),Y=H.useCallback(B=>N=>{if(N.preventDefault(),!pe(S)){x({type:"DROP"});return}let M=S.eventId,U=N.dataTransfer.getData("text/plain");if(x({type:"DROP"}),U&&U!==M||!d)return;let _=t.find(X=>X.id===M);!_||_.editable===!1||d(de(_,B))},[S,t,d]),W=!!d;return We("div",{className:ut("aethercal-calendar",pe(S)&&"is-dragging"),role:"grid","aria-label":rt(n,a),"data-view":"month",children:[re("div",{className:"aethercal-weekdays",role:"row",children:h.map((B,N)=>re("div",{role:"columnheader",className:"aethercal-weekday",children:B},N))}),f.map((B,N)=>re("div",{className:"aethercal-week",role:"row",children:B.map(M=>{let U=$.get(M)??[],_=O.has(M),X=_?U:U.slice(0,o),j=U.length-X.length,Re=new Date(`${M}T00:00:00`).getMonth()!==T;return We("div",{role:"gridcell",className:ut("aethercal-day",Re&&"is-outside",M===C&&"is-today"),"data-date":M,"aria-label":it(M,a),onDragOver:W?L=>L.preventDefault():void 0,onDrop:W?Y(M):void 0,onContextMenu:l?L=>{L.target.closest("[data-event-id], button")||(L.preventDefault(),l({start:`${M}T00:00:00`}))}:void 0,children:[re("div",{className:"aethercal-day-head",children:re("span",{className:"aethercal-day-number",children:Number(M.slice(-2))})}),We("div",{className:"aethercal-day-events",children:[X.map(L=>re(he,{event:L,timeLabel:L.allDay?null:ae(L.start,a),onDragStart:Me=>x({type:"DRAG_START",eventId:Me}),onDragEnd:()=>x({type:"DRAG_CANCEL"}),isPending:m.has(L.id),isRolledBack:u.has(L.id),...s?{onClick:()=>s({id:L.id})}:{},...l?{onContextMenu:()=>l({id:L.id})}:{}},L.id)),j>0&&!_?re("button",{type:"button",className:"aethercal-more",onClick:()=>R(M),children:c(j)}):null]})]},M)})},N))]})}var pt="aethercal-calendar-styles",mt=`
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
`;function ue(){if(typeof document>"u"||document.getElementById(pt))return;let e=document.createElement("style");e.id=pt,e.textContent=mt,document.head.appendChild(e)}import*as w from"react";var ft="All day";function yt(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(g(e))}function vt(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function ht(e,t){if(e.length===0)return"";let n=g(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(n);let a=g(e[e.length-1]),r=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${r.format(n)} \u2013 ${r.format(a)}`}var bt="aethercal-timegrid-styles",Dt=`
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
`;function Ke(){if(typeof document>"u"||document.getElementById(bt))return;let e=document.createElement("style");e.id=bt,e.textContent=Dt,document.head.appendChild(e)}import{Fragment as zt,jsx as A,jsxs as ie}from"react/jsx-runtime";function be(...e){return e.filter(Boolean).join(" ")}var Q=e=>`${e*100}%`,Et=new Set;function De(e,t){let n=t.getBoundingClientRect();return n.height>0?(e-n.top)/n.height:0}function Je(e){let{view:t,days:n,events:a,locale:r,config:i,now:o,allDayLabel:c=ft,onEventDrop:d,onEventResize:s,onRangeSelect:l,onEventClick:m,onContextMenu:u,pendingIds:D=Et,rolledBackIds:f=Et}=e;w.useEffect(()=>{ue(),Ke()},[]);let h=w.useMemo(()=>Be(n,a,i),[n,a,i]),$=w.useMemo(()=>Ue(o,i),[o,i]),T=w.useMemo(()=>V(k(o)),[o]),[C,S]=w.useReducer(Ae,me),x=w.useRef(null),[O,P]=w.useState(null),[R,Y]=w.useState(null),W=!!d,B=!!s,N=!!l,M=C.status==="dragging",U=w.useCallback((y,b)=>p=>{if(p.preventDefault(),C.status!=="dragging"){S({type:"COMMIT"});return}let I=C.eventId,G=p.dataTransfer.getData("text/plain");if(S({type:"COMMIT"}),G&&G!==I||!d)return;let E=a.find(K=>K.id===I);if(!E||E.editable===!1)return;let v=null;if(b&&E.allDay!==!0){let z=p.currentTarget.getBoundingClientRect();z.height>0&&Number.isFinite(p.clientY)&&(v=ne((p.clientY-z.top)/z.height,h.config))}d(Ne(E,y,v))},[C,a,d,h.config]),_=w.useCallback(y=>{x.current?.kind!=="resize"&&S({type:"DRAG_START",eventId:y})},[]),X=w.useCallback(()=>S({type:"CANCEL"}),[]),j=w.useCallback((y,b)=>p=>{if(!s||y.editable===!1||p.button!==0)return;let I=p.currentTarget.closest(".aethercal-tg-col");I?.dataset.date&&(p.preventDefault(),p.stopPropagation(),x.current={kind:"resize",pointerId:p.pointerId,eventId:y.id,edge:b,dateOnly:I.dataset.date,colEl:I,payload:null},p.currentTarget.setPointerCapture?.(p.pointerId),S({type:"RESIZE_START",eventId:y.id,edge:b}))},[s]),Re=w.useCallback(y=>b=>{if(!l||b.button!==0||b.target.closest("[data-event-id], button"))return;let p=b.currentTarget,I=ne(De(b.clientY,p),h.config);x.current={kind:"select",pointerId:b.pointerId,anchorDate:y,anchorCol:p,anchorMinute:I,currentDate:y,currentCol:p,currentMinute:I},p.setPointerCapture?.(b.pointerId),S({type:"SELECT_START",point:{dateOnly:y,minuteOfDay:I}})},[l,h.config]),Te=C.status==="resizing"||C.status==="selecting";w.useEffect(()=>{if(!Te)return;let y=E=>{let v=x.current;if(!(!v||E.pointerId!==v.pointerId))if(v.kind==="resize"){let K=document.elementFromPoint(E.clientX,E.clientY)?.closest(".aethercal-tg-col"),z=K?.dataset.date?K:v.colEl,oe=ne(De(E.clientY,z),h.config),J=a.find(xt=>xt.id===v.eventId);if(!J)return;let se=_e(J,v.edge,z.dataset.date??v.dateOnly,oe);v.payload=se,P(se)}else{let K=document.elementFromPoint(E.clientX,E.clientY)?.closest(".aethercal-tg-col"),z=K?.dataset.date?K:v.currentCol;v.currentCol=z,v.currentDate=z.dataset.date??v.anchorDate,v.currentMinute=ne(De(E.clientY,z),h.config);let oe=fe({dateOnly:v.anchorDate,minuteOfDay:v.anchorMinute},{dateOnly:v.currentDate,minuteOfDay:v.currentMinute}),se=(v.currentDate===v.anchorDate?ce([{id:"__sel",title:"",start:oe.start,end:oe.end}],v.anchorDate,h.config):[])[0];Y(se?{dateOnly:v.anchorDate,topFraction:se.topFraction,heightFraction:se.heightFraction}:null)}},b=E=>{let v=x.current;x.current=null,P(null),Y(null),E&&v&&(v.kind==="resize"&&v.payload&&s&&s(v.payload),v.kind==="select"&&l&&(v.currentDate!==v.anchorDate||v.currentMinute!==v.anchorMinute)&&l(fe({dateOnly:v.anchorDate,minuteOfDay:v.anchorMinute},{dateOnly:v.currentDate,minuteOfDay:v.currentMinute}))),S({type:E?"COMMIT":"CANCEL"})},p=E=>{x.current&&E.pointerId!==x.current.pointerId||b(!0)},I=E=>{x.current&&E.pointerId!==x.current.pointerId||b(!1)},G=E=>{E.key==="Escape"&&b(!1)};return window.addEventListener("pointermove",y),window.addEventListener("pointerup",p),window.addEventListener("pointercancel",I),window.addEventListener("keydown",G),()=>{window.removeEventListener("pointermove",y),window.removeEventListener("pointerup",p),window.removeEventListener("pointercancel",I),window.removeEventListener("keydown",G)}},[Te,a,h.config,s,l]);let L=w.useCallback((y,b)=>p=>{if(!u||p.target.closest("[data-event-id], button"))return;if(p.preventDefault(),!b){u({start:`${y}T00:00:00`});return}let I=ne(De(p.clientY,p.currentTarget),h.config),G=g(`${y}T00:00:00`),E=new Date(G.getFullYear(),G.getMonth(),G.getDate(),0,I,0);u({start:k(E)})},[u,h.config]),Me={"--ac-tg-cols":h.columns.length,"--ac-tg-hours":h.config.dayEndHour-h.config.dayStartHour};return ie("div",{className:be("aethercal-calendar","aethercal-timegrid",M&&"is-dragging",C.status==="resizing"&&"is-resizing",C.status==="selecting"&&"is-selecting"),role:"grid","aria-label":ht(n,r),"data-view":t,style:Me,children:[ie("div",{className:"aethercal-tg-head",role:"row",children:[A("div",{className:"aethercal-tg-corner"}),h.columns.map(y=>A("div",{role:"columnheader",className:be("aethercal-tg-colhead",y.dateOnly===T&&"is-today"),"data-date":y.dateOnly,children:A("span",{className:"aethercal-tg-colhead-date",children:yt(y.dateOnly,r)})},y.dateOnly))]}),ie("div",{className:"aethercal-tg-allday",role:"row",children:[A("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:c}),h.columns.map(y=>A("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":y.dateOnly,onDragOver:W?b=>b.preventDefault():void 0,onDrop:W?U(y.dateOnly,!1):void 0,onContextMenu:u?L(y.dateOnly,!1):void 0,children:y.allDay.map(b=>A(he,{event:b,timeLabel:null,onDragStart:_,onDragEnd:X,isPending:D.has(b.id),isRolledBack:f.has(b.id),...m?{onClick:()=>m({id:b.id})}:{},...u?{onContextMenu:()=>u({id:b.id})}:{}},b.id))},y.dateOnly))]}),ie("div",{className:"aethercal-tg-body",role:"row",children:[A("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:h.hourMarks.map(y=>A("div",{className:"aethercal-tg-hour",style:{top:Q(y.topFraction)},children:vt(y.hour,r)},y.hour))}),h.columns.map(y=>ie("div",{role:"gridcell",className:be("aethercal-tg-col",y.dateOnly===T&&"is-today"),"data-date":y.dateOnly,onDragOver:W?b=>b.preventDefault():void 0,onDrop:W?U(y.dateOnly,!0):void 0,onPointerDown:N?Re(y.dateOnly):void 0,onContextMenu:u?L(y.dateOnly,!0):void 0,children:[h.hourMarks.map(b=>A("div",{className:"aethercal-tg-line",style:{top:Q(b.topFraction)},"aria-hidden":"true"},b.hour)),R&&R.dateOnly===y.dateOnly?A("div",{className:"aethercal-tg-select-band",style:{top:Q(R.topFraction),height:Q(R.heightFraction)},"aria-hidden":"true"}):null,y.timed.map(b=>{let{event:p}=b,I=p.editable!==!1,G=ae(p.start,r),E=O?.id===p.id?O:null,v=E?ce([{...p,start:E.start,end:E.end}],y.dateOnly,h.config)[0]:void 0,K=v?v.topFraction:b.topFraction,z=v?v.heightFraction:b.heightFraction,oe={top:Q(K),height:Q(z),left:Q(b.lane/b.laneCount),width:Q(1/b.laneCount),...p.color?{"--ac-tg-event-accent":p.color}:{}};return ie("div",{className:be("aethercal-tg-event",!I&&"is-locked",D.has(p.id)&&"is-pending",f.has(p.id)&&"is-rolledback",!!E&&"is-resizing"),draggable:I,"data-event-id":p.id,"data-lane":b.lane,"data-lane-count":b.laneCount,"aria-label":`${G} ${p.title}`,title:p.title,style:oe,onDragStart:J=>{if(x.current?.kind==="resize"){J.preventDefault();return}J.dataTransfer.setData("text/plain",p.id),J.dataTransfer.effectAllowed="move",_(p.id)},onDragEnd:X,onClick:m?()=>m({id:p.id}):void 0,onContextMenu:u?J=>{J.preventDefault(),J.stopPropagation(),u({id:p.id})}:void 0,children:[A("time",{className:"aethercal-tg-event-time",children:G}),A("span",{className:"aethercal-tg-event-title",children:p.title}),B&&I?ie(zt,{children:[A("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:j(p,"start")}),A("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:j(p,"end")})]}):null]},p.id)}),$!==null&&y.dateOnly===T?A("div",{className:"aethercal-now-indicator",style:{top:Q($)},"aria-hidden":"true"}):null]},y.dateOnly))]})]})}import{jsx as Ee}from"react/jsx-runtime";function Ht(e){return e instanceof Date?e:typeof e=="string"?g(e):new Date}function $t(e){return e instanceof Date?e:typeof e=="string"?g(e):new Date}var Yt=e=>`+${e} more`,Bt=e=>`ends ${e}`;function xe(e){let{view:t="month",events:n,anchor:a,locale:r="en",firstDayOfWeek:i=1,maxEventsPerDay:o=3,weekdayLabels:c,formatMore:d=Yt,unavailableLabel:s="This view is not available yet.",dayStartHour:l,dayEndHour:m,allDayLabel:u="All day",now:D,continuesLabel:f="Continues",formatEndsLabel:h=Bt,agendaEmptyLabel:$="No events",onEventDrop:T,onEventResize:C,onRangeSelect:S,onEventClick:x,onContextMenu:O,pendingIds:P,rolledBackIds:R}=e;ee.useEffect(()=>{ue()},[]);let Y=ee.useMemo(()=>Ht(a),[a]),[W,B]=ee.useState(()=>new Date);ee.useEffect(()=>{if(D!==void 0||t!=="week"&&t!=="day")return;let j=setInterval(()=>B(new Date),6e4);return()=>clearInterval(j)},[D,t]);let N=ee.useMemo(()=>D!==void 0?$t(D):W,[D,W]),M=Number.isInteger(i)&&i>=0&&i<=6?i:1,U=Number.isInteger(o)&&o>=0?o:3,_=c&&c.length===7?c:void 0,X=ee.useMemo(()=>({...l!==void 0?{dayStartHour:l}:{},...m!==void 0?{dayEndHour:m}:{}}),[l,m]);if(t==="list")return Ee(st,{events:n??[],locale:r,allDayLabel:u,continuesLabel:f,formatEndsLabel:h,emptyLabel:$});if(t==="month")return Ee(gt,{events:n??[],anchor:Y,locale:r,firstDayOfWeek:M,maxEventsPerDay:U,formatMore:d,..._?{weekdayLabels:_}:{},...T?{onEventDrop:T}:{},...x?{onEventClick:x}:{},...O?{onContextMenu:O}:{},...P?{pendingIds:P}:{},...R?{rolledBackIds:R}:{}});if(t==="week"||t==="day"){let j=t==="week"?Ce(Y,M):[V(k(Y))];return Ee(Je,{view:t,days:j,events:n??[],locale:r,config:X,now:N,allDayLabel:u,...T?{onEventDrop:T}:{},...C?{onEventResize:C}:{},...S?{onRangeSelect:S}:{},...x?{onEventClick:x}:{},...O?{onContextMenu:O}:{},...P?{pendingIds:P}:{},...R?{rolledBackIds:R}:{}})}return Ee("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,children:s})}var Ut=xe;import*as F from"react";function Vt(){return typeof crypto<"u"&&typeof crypto.randomUUID=="function"?crypto.randomUUID():`cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}var Wt=8e3,Kt=900;function Xe(e){let{events:t,mutate:n,timeoutMs:a=Wt,rollbackFlashMs:r=Kt,generateId:i=Vt}=e,[o,c]=F.useReducer(ze,Ge),d=F.useRef(t);d.current=t;let s=F.useRef(!0),l=F.useRef(new Map);F.useEffect(()=>{s.current=!0;let D=l.current;return()=>{s.current=!1;for(let f of D.values())clearTimeout(f);D.clear()}},[]),F.useEffect(()=>{for(let D of $e(t,o)){let f=o.overrides[D];c({type:"CLEAR",id:D,...f?{clientMutationId:f.clientMutationId}:{}})}},[t,o]);let m=F.useCallback((D,f)=>{let h=i(),$=d.current.find(R=>R.id===f.id),T=l.current,C=R=>{let Y=T.get(R);Y!==void 0&&(clearTimeout(Y),T.delete(R))},S=()=>{T.set(`fl:${h}`,setTimeout(()=>{T.delete(`fl:${h}`),s.current&&c({type:"CLEAR",id:f.id,clientMutationId:h})},r))};c({type:"SUBMIT",id:f.id,clientMutationId:h,start:f.start,end:f.end,...$?.revision!==void 0?{baseRevision:$.revision}:{}}),T.set(`to:${h}`,setTimeout(()=>{T.delete(`to:${h}`),s.current&&(c({type:"TIMEOUT",id:f.id,clientMutationId:h}),S())},a));let x=()=>{C(`to:${h}`),s.current&&(c({type:"REJECT",id:f.id,clientMutationId:h}),S())},O={kind:D,clientMutationId:h,payload:{...f,client_mutation_id:h}},P;try{P=n(O)}catch(R){P=Promise.reject(R instanceof Error?R:new Error(String(R)))}P.then(R=>{if(R.id!==f.id){x();return}C(`to:${h}`),s.current&&c({type:"RESOLVE",id:R.id,clientMutationId:h,start:R.start,end:R.end,revision:R.revision})}).catch(x)},[n,a,r,i]),u=F.useMemo(()=>He(t,o),[t,o]);return{events:u.events,pendingIds:u.pendingIds,rolledBackIds:u.rolledBackIds,submit:m}}import{jsx as Xt}from"react/jsx-runtime";function Jt({events:e,mutate:t,timeoutMs:n,rollbackFlashMs:a,generateId:r,...i}){let{events:o,pendingIds:c,rolledBackIds:d,submit:s}=Xe({events:e,mutate:t,...n!==void 0?{timeoutMs:n}:{},...a!==void 0?{rollbackFlashMs:a}:{},...r?{generateId:r}:{}});return Xt(xe,{...i,events:o,pendingIds:c,rolledBackIds:d,onEventDrop:l=>s("drop",l),onEventResize:l=>s("resize",l)})}export{xe as AetherCalendar,mt as CALENDAR_CSS,Jt as OptimisticCalendar,Dt as TIME_GRID_CSS,Je as TimeGridView,Ut as default,ue as ensureCalendarStyles,Ke as ensureTimeGridStyles,Xe as useOptimisticEvents};
