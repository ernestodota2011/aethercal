function X(e){return String(e).padStart(2,"0")}function u(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,n,a,r,i,o,c]=t,s=Number(n),l=Number(a),d=Number(r),v=Number(i??"0"),g=Number(o??"0"),E=Number(c??"0");if(l<1||l>12||d<1||d>31||v>23||g>59||E>59)throw new Error(`out-of-range ISO datetime: ${e}`);let h=new Date(s,l-1,d,v,g,E);if(h.getFullYear()!==s||h.getMonth()!==l-1||h.getDate()!==d)throw new Error(`nonexistent calendar date: ${e}`);return h}function k(e){return`${e.getFullYear()}-${X(e.getMonth()+1)}-${X(e.getDate())}T${X(e.getHours())}:${X(e.getMinutes())}:${X(e.getSeconds())}`}function V(e){let t=u(e);return`${t.getFullYear()}-${X(t.getMonth()+1)}-${X(t.getDate())}`}function Et(e,t){return(e.getDay()-t+7)%7}function Me(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate());return n.setDate(n.getDate()-Et(n,t)),n}function Ke(e,t){return Array.from({length:t},(n,a)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+a);return`${r.getFullYear()}-${X(r.getMonth()+1)}-${X(r.getDate())}`})}function we(e,t=1){return Ke(Me(e,t),7)}function Ce(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),1);return Ke(Me(n,t),42)}function Dt(e,t){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()),a=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((a.getTime()-n.getTime())/864e5)}function se(e,t){let n=u(e.start),a=u(e.end),r=u(t),i=Dt(n,r),o=new Date(n.getFullYear(),n.getMonth(),n.getDate()+i,n.getHours(),n.getMinutes(),n.getSeconds()),c=new Date(a.getFullYear(),a.getMonth(),a.getDate()+i,a.getHours(),a.getMinutes(),a.getSeconds()),s={id:e.id,start:k(o),end:k(c)};return e.revision!==void 0&&(s.revision=e.revision),s}var xt=370;function Je(e){return String(e).padStart(2,"0")}function Se(e){return`${e.getFullYear()}-${Je(e.getMonth()+1)}-${Je(e.getDate())}`}function Xe(e){return new Date(e.getFullYear(),e.getMonth(),e.getDate())}function Rt(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function Tt(e){let t=u(e.start),n=u(e.end),a=Xe(t),r;n.getTime()<=t.getTime()?r=a:(r=Xe(new Date(n.getTime()-1)),r.getTime()<a.getTime()&&(r=a));let i=[],o=a;for(let c=0;c<xt&&o.getTime()<=r.getTime();c+=1)i.push(Se(o)),o=Rt(o,1);return{keys:i,startKey:Se(a),lastKey:Se(r)}}function ke(e){let t=new Map;return e.forEach((n,a)=>{let{keys:r,startKey:i,lastKey:o}=Tt(n),c=u(n.start).getTime(),s=u(n.end).getTime();for(let l of r){let d={entry:{event:n,isContinuation:l!==i,continuesAfter:l!==o},startMs:c,endMs:s,index:a},v=t.get(l);v?v.push(d):t.set(l,[d])}}),[...t.keys()].sort().map(n=>{let a=t.get(n);return a.sort((r,i)=>r.startMs-i.startMs||r.endMs-i.endMs||r.index-i.index),{date:n,entries:a.map(r=>r.entry)}})}var ue={status:"idle"};function ge(e){return e.status==="dragging"}function Ie(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return ue}}var pe={status:"idle"};function Ae(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"RESIZE_START":return{status:"resizing",eventId:t.eventId,edge:t.edge};case"SELECT_START":return{status:"selecting",anchor:t.point,current:t.point};case"SELECT_MOVE":return e.status!=="selecting"?e:{status:"selecting",anchor:e.anchor,current:t.point};case"COMMIT":case"CANCEL":return pe}}var je=60,qe=6e4,de=15;function Ze(e,t,n){return Math.min(n,Math.max(t,e))}function me(e,t){let n=u(`${e}T00:00:00`);return new Date(n.getFullYear(),n.getMonth(),n.getDate(),0,t,0)}function ie(e,t,n=de){let a=t.dayStartHour*je,r=t.dayEndHour*je,i=a+Ze(e,0,1)*t.windowMinutes,o=n>0?n:de,c=Math.round(i/o)*o;return Ze(c,a,r)}function Le(e,t,n){if(n===null)return se(e,t);let a=u(e.start),i=u(e.end).getTime()-a.getTime(),o=me(t,n),c=new Date(o.getTime()+i),s={id:e.id,start:k(o),end:k(c)};return e.revision!==void 0&&(s.revision=e.revision),s}function Pe(e,t,n,a,r={}){let i=(r.minDurationMinutes??de)*qe,o=u(e.start),c=u(e.end),s=me(n,a),l=o,d=c;if(t==="end"){let g=o.getTime()+i;d=new Date(Math.max(s.getTime(),g))}else{let g=c.getTime()-i;l=new Date(Math.min(s.getTime(),g))}let v={id:e.id,start:k(l),end:k(d)};return e.revision!==void 0&&(v.revision=e.revision),v}function fe(e,t,n={}){let a=n.minDurationMinutes??de;if(e.minuteOfDay===null||t.minuteOfDay===null){let[l,d]=e.dateOnly<=t.dateOnly?[e.dateOnly,t.dateOnly]:[t.dateOnly,e.dateOnly],v=u(`${l}T00:00:00`),g=u(`${d}T00:00:00`),E=new Date(g.getFullYear(),g.getMonth(),g.getDate()+1);return{start:k(v),end:k(E),allDay:!0}}let i=me(e.dateOnly,e.minuteOfDay??0),o=me(t.dateOnly,t.minuteOfDay??0),c=i.getTime()<=o.getTime()?i:o,s=i.getTime()<=o.getTime()?o:i;return s.getTime()===c.getTime()&&(s=new Date(c.getTime()+a*qe)),{start:k(c),end:k(s),allDay:!1}}var Oe={overrides:{},appliedRevision:{}};function Mt(e,t){let n={...e};return delete n[t],n}function Fe(e,t){switch(t.type){case"SUBMIT":{let n=t.baseRevision??Number.NEGATIVE_INFINITY,a=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;return{overrides:{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"pending",start:t.start,end:t.end,...t.baseRevision!==void 0?{revision:t.baseRevision}:{}}},appliedRevision:{...e.appliedRevision,[t.id]:Math.max(a,n)}}}case"RESOLVE":{let n=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;if(t.revision<=n)return e;let a=e.overrides[t.id];return{overrides:a&&a.clientMutationId===t.clientMutationId?{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"committed",start:t.start,end:t.end,revision:t.revision}}:e.overrides,appliedRevision:{...e.appliedRevision,[t.id]:t.revision}}}case"REJECT":case"TIMEOUT":{let n=e.overrides[t.id];return!n||n.clientMutationId!==t.clientMutationId||n.status!=="pending"?e:{...e,overrides:{...e.overrides,[t.id]:{...n,status:"rolledback"}}}}case"CLEAR":{let n=e.overrides[t.id];return!n||t.clientMutationId&&n.clientMutationId!==t.clientMutationId?e:{...e,overrides:Mt(e.overrides,t.id)}}}}function Ne(e,t){let n=new Set,a=new Set;return{events:e.map(i=>{let o=t.overrides[i.id];return o?o.status==="pending"?(n.add(i.id),{...i,start:o.start,end:o.end}):o.status==="rolledback"?(a.add(i.id),i):i.revision!==void 0&&o.revision!==void 0&&i.revision>=o.revision?i:{...i,start:o.start,end:o.end,...o.revision!==void 0?{revision:o.revision}:{}}:i}),pendingIds:n,rolledBackIds:a}}function _e(e,t){let n=new Map(e.map(r=>[r.id,r])),a=[];for(let[r,i]of Object.entries(t.overrides)){if(i.status!=="committed")continue;let o=n.get(r);o&&o.revision!==void 0&&i.revision!==void 0&&o.revision>=i.revision&&a.push(r)}return a}var j=60,wt=24*j,Ct=864e5;function ye(e,t,n){return Math.min(n,Math.max(t,e))}function Ge(e={}){let t=e.dayStartHour,n=e.dayEndHour,a=Number.isFinite(t)&&t!==void 0?ye(Math.trunc(t),0,23):0,r=Number.isFinite(n)&&n!==void 0?ye(Math.trunc(n),1,24):24,[i,o]=r>a?[a,r]:[0,24];return{dayStartHour:i,dayEndHour:o,windowMinutes:(o-i)*j}}function et(e){let t=[],n=[];for(let a of e)a.allDay===!0?t.push(a):n.push(a);return{allDay:t,timed:n}}function Qe(e,t){let n=u(e),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()),r=Math.round((a.getTime()-t.getTime())/Ct),i=n.getHours()*j+n.getMinutes()+n.getSeconds()/60;return r*wt+i}function St(e,t){let n=u(e.start).getTime(),a=u(e.end).getTime(),r=u(t.start).getTime(),i=u(t.end).getTime();return n<i&&r<a}function le(e,t,n){let a=u(`${t}T00:00:00`),r=n.dayStartHour*j,i=n.dayEndHour*j,o=[...e].sort((g,E)=>{let h=u(g.start).getTime(),f=u(E.start).getTime();return h!==f?h-f:u(E.end).getTime()-u(g.end).getTime()}),c=[],s=[],l=[],d=Number.NEGATIVE_INFINITY,v=()=>{let g=s.length;for(let E of l)c[E].laneCount=g;s=[],l=[],d=Number.NEGATIVE_INFINITY};for(let g of o){let E=Qe(g.start,a),h=Qe(g.end,a);if(h<=r||E>=i)continue;let f=u(g.start).getTime(),x=u(g.end).getTime();l.length>0&&f>=d&&v();let I=s.findIndex(M=>!St(M,g));I===-1?(I=s.length,s.push(g)):s[I]=g;let D=ye(E,r,i),C=ye(h,D,i),R=(D-r)/n.windowMinutes,N=(C-D)/n.windowMinutes;l.push(c.length),c.push({event:g,lane:I,laneCount:1,topFraction:R,heightFraction:N}),d=Math.max(d,x)}return v(),c}function kt(e){let t=[];for(let n=e.dayStartHour;n<e.dayEndHour;n+=1)t.push({hour:n,topFraction:(n-e.dayStartHour)*j/e.windowMinutes});return t}function ze(e,t,n={}){let a="windowMinutes"in n?n:Ge(n),{allDay:r,timed:i}=et(t),o=i.map(s=>({event:s,startTs:u(s.start).getTime(),endTs:u(s.end).getTime()}));return{columns:e.map(s=>{let l=u(`${s}T00:00:00`),d=l.getTime(),v=new Date(l.getFullYear(),l.getMonth(),l.getDate()+1).getTime(),g=o.filter(h=>h.startTs>=v?!1:h.endTs>d?!0:h.startTs===h.endTs&&h.startTs>=d).map(h=>h.event),E=r.filter(h=>V(h.start)<=s&&s<=V(h.end));return{dateOnly:s,allDay:E,timed:le(g,s,a)}}),hourMarks:kt(a),config:a}}function $e(e,t={}){let n="windowMinutes"in t?t:Ge(t),a=e.getHours()*j+e.getMinutes()+e.getSeconds()/60,r=n.dayStartHour*j,i=n.dayEndHour*j;return a<r||a>=i?null:(a-r)/n.windowMinutes}import*as q from"react";import*as ve from"react";var He=new Date(2023,0,1);function tt(e,t){let n=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(a,r)=>{let i=(t+r)%7,o=new Date(He.getFullYear(),He.getMonth(),He.getDate()+i);return n.format(o)})}function nt(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function at(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(u(e))}function ne(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(u(e))}function rt(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(u(e))}import{jsx as te,jsxs as ot}from"react/jsx-runtime";function It(...e){return e.filter(Boolean).join(" ")}function At(e,t,n){let{event:a,isContinuation:r,continuesAfter:i}=e;return a.allDay===!0?n.allDayLabel:r?i?n.continuesLabel:n.formatEndsLabel(ne(a.end,t)):ne(a.start,t)}function Lt({entry:e,locale:t,labels:n}){let{event:a,isContinuation:r,continuesAfter:i}=e,o=At(e,t,n),c=a.color?{"--ac-event-accent":a.color}:void 0;return ot("li",{className:It("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":a.id,"aria-label":`${o} ${a.title}`,style:c,...a.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...i?{"data-continues-after":""}:{},children:[te("span",{className:"aethercal-agenda-event-time",children:o}),te("span",{className:"aethercal-agenda-event-title",children:a.title})]})}function it({events:e,locale:t,allDayLabel:n,continuesLabel:a,formatEndsLabel:r,emptyLabel:i}){let o=ve.useMemo(()=>ke(e),[e]),c=ve.useId(),s={allDayLabel:n,continuesLabel:a,formatEndsLabel:r};return o.length===0?te("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:te("p",{className:"aethercal-agenda-empty",children:i})}):te("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:o.map(l=>{let d=`${c}-${l.date}`;return ot("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":d,"data-date":l.date,children:[te("div",{className:"aethercal-agenda-day-title",id:d,children:rt(l.date,t)}),te("ul",{className:"aethercal-agenda-day-events",role:"list",children:l.entries.map((v,g)=>te(Lt,{entry:v,locale:t,labels:s},`${v.event.id}-${g}`))})]},l.date)})})}import*as H from"react";import{jsx as st,jsxs as Ot}from"react/jsx-runtime";function Pt(...e){return e.filter(Boolean).join(" ")}function he({event:e,timeLabel:t,onDragStart:n,onDragEnd:a,isPending:r,isRolledBack:i,onClick:o,onContextMenu:c}){let s=e.editable!==!1,l=e.color?{"--ac-event-accent":e.color}:void 0,d=t?`${t} ${e.title}`:e.title;return Ot("div",{className:Pt("aethercal-event",!s&&"is-locked",r&&"is-pending",i&&"is-rolledback"),draggable:s,"data-event-id":e.id,"aria-label":d,title:e.title,style:l,onDragStart:v=>{v.dataTransfer.setData("text/plain",e.id),v.dataTransfer.effectAllowed="move",n(e.id)},onDragEnd:a,onClick:o,onContextMenu:c?v=>{v.preventDefault(),v.stopPropagation(),c()}:void 0,children:[t?st("time",{className:"aethercal-event-time",children:t}):null,st("span",{className:"aethercal-event-title",children:e.title})]})}import{jsx as ae,jsxs as Be}from"react/jsx-runtime";var dt=new Set;function lt(...e){return e.filter(Boolean).join(" ")}function Ft(e){let t=[];for(let n=0;n<e.length;n+=7)t.push(e.slice(n,n+7));return t}function Nt(e){let t=new Map;for(let n of e){let a=V(n.start),r=t.get(a);r?r.push(n):t.set(a,[n])}return t}function ct(e){let{events:t,anchor:n,locale:a,firstDayOfWeek:r,weekdayLabels:i,maxEventsPerDay:o,formatMore:c,onEventDrop:s,onEventClick:l,onContextMenu:d,pendingIds:v=dt,rolledBackIds:g=dt}=e,E=H.useMemo(()=>Ce(n,r),[n,r]),h=H.useMemo(()=>Ft(E),[E]),f=H.useMemo(()=>i??tt(a,r),[i,a,r]),x=H.useMemo(()=>Nt(t),[t]),I=n.getMonth(),D=V(k(new Date)),[C,R]=H.useReducer(Ie,ue),[N,M]=H.useState(()=>new Set),O=H.useCallback(B=>{M(_=>{let T=new Set(_);return T.add(B),T})},[]),Q=H.useCallback(B=>_=>{if(_.preventDefault(),!ge(C)){R({type:"DROP"});return}let T=C.eventId,Y=_.dataTransfer.getData("text/plain");if(R({type:"DROP"}),Y&&Y!==T||!s)return;let G=t.find(K=>K.id===T);!G||G.editable===!1||s(se(G,B))},[C,t,s]),W=!!s;return Be("div",{className:lt("aethercal-calendar",ge(C)&&"is-dragging"),role:"grid","aria-label":nt(n,a),"data-view":"month",children:[ae("div",{className:"aethercal-weekdays",role:"row",children:f.map((B,_)=>ae("div",{role:"columnheader",className:"aethercal-weekday",children:B},_))}),h.map((B,_)=>ae("div",{className:"aethercal-week",role:"row",children:B.map(T=>{let Y=x.get(T)??[],G=N.has(T),K=G?Y:Y.slice(0,o),J=Y.length-K.length,xe=new Date(`${T}T00:00:00`).getMonth()!==I;return Be("div",{role:"gridcell",className:lt("aethercal-day",xe&&"is-outside",T===D&&"is-today"),"data-date":T,"aria-label":at(T,a),onDragOver:W?L=>L.preventDefault():void 0,onDrop:W?Q(T):void 0,onContextMenu:d?L=>{L.target===L.currentTarget&&(L.preventDefault(),d({start:`${T}T00:00:00`}))}:void 0,children:[ae("div",{className:"aethercal-day-head",children:ae("span",{className:"aethercal-day-number",children:Number(T.slice(-2))})}),Be("div",{className:"aethercal-day-events",children:[K.map(L=>ae(he,{event:L,timeLabel:L.allDay?null:ne(L.start,a),onDragStart:Te=>R({type:"DRAG_START",eventId:Te}),onDragEnd:()=>R({type:"DRAG_CANCEL"}),isPending:v.has(L.id),isRolledBack:g.has(L.id),...l?{onClick:()=>l({id:L.id})}:{},...d?{onContextMenu:()=>d({id:L.id})}:{}},L.id)),J>0&&!G?ae("button",{type:"button",className:"aethercal-more",onClick:()=>O(T),children:c(J)}):null]})]},T)})},_))]})}var ut="aethercal-calendar-styles",gt=`
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
`;function ce(){if(typeof document>"u"||document.getElementById(ut))return;let e=document.createElement("style");e.id=ut,e.textContent=gt,document.head.appendChild(e)}import*as w from"react";var pt="All day";function mt(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(u(e))}function ft(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function yt(e,t){if(e.length===0)return"";let n=u(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(n);let a=u(e[e.length-1]),r=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${r.format(n)} \u2013 ${r.format(a)}`}var vt="aethercal-timegrid-styles",ht=`
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
`;function Ye(){if(typeof document>"u"||document.getElementById(vt))return;let e=document.createElement("style");e.id=vt,e.textContent=ht,document.head.appendChild(e)}import{Fragment as _t,jsx as P,jsxs as re}from"react/jsx-runtime";function be(...e){return e.filter(Boolean).join(" ")}var Z=e=>`${e*100}%`,bt=new Set;function Ue(e,t){let n=t.getBoundingClientRect();return n.height>0?(e-n.top)/n.height:0}function Ve(e){let{view:t,days:n,events:a,locale:r,config:i,now:o,allDayLabel:c=pt,onEventDrop:s,onEventResize:l,onRangeSelect:d,onEventClick:v,onContextMenu:g,pendingIds:E=bt,rolledBackIds:h=bt}=e;w.useEffect(()=>{ce(),Ye()},[]);let f=w.useMemo(()=>ze(n,a,i),[n,a,i]),x=w.useMemo(()=>$e(o,i),[o,i]),I=w.useMemo(()=>V(k(o)),[o]),[D,C]=w.useReducer(Ae,pe),R=w.useRef(null),[N,M]=w.useState(null),[O,Q]=w.useState(null),W=!!s,B=!!l,_=!!d,T=D.status==="dragging",Y=w.useCallback((m,y)=>p=>{if(p.preventDefault(),D.status!=="dragging"){C({type:"COMMIT"});return}let S=D.eventId,z=p.dataTransfer.getData("text/plain");if(C({type:"COMMIT"}),z&&z!==S||!s)return;let A=a.find(ee=>ee.id===S);if(!A||A.editable===!1)return;let b=null;if(y&&A.allDay!==!0){let U=p.currentTarget.getBoundingClientRect();U.height>0&&Number.isFinite(p.clientY)&&(b=ie((p.clientY-U.top)/U.height,f.config))}s(Le(A,m,b))},[D,a,s,f.config]),G=w.useCallback(m=>{R.current?.kind!=="resize"&&C({type:"DRAG_START",eventId:m})},[]),K=w.useCallback(()=>C({type:"CANCEL"}),[]),J=w.useCallback((m,y)=>p=>{if(!l||m.editable===!1||p.button!==0)return;let S=p.currentTarget.closest(".aethercal-tg-col");S?.dataset.date&&(p.preventDefault(),p.stopPropagation(),R.current={kind:"resize",eventId:m.id,edge:y,dateOnly:S.dataset.date,colEl:S,payload:null},p.currentTarget.setPointerCapture?.(p.pointerId),C({type:"RESIZE_START",eventId:m.id,edge:y}))},[l]),xe=w.useCallback(m=>y=>{if(!d||y.button!==0||y.target!==y.currentTarget)return;let p=y.currentTarget,S=ie(Ue(y.clientY,p),f.config);R.current={kind:"select",dateOnly:m,colEl:p,anchorMinute:S,currentMinute:S},C({type:"SELECT_START",point:{dateOnly:m,minuteOfDay:S}})},[d,f.config]),Re=D.status==="resizing"||D.status==="selecting";w.useEffect(()=>{if(!Re)return;let m=A=>{let b=R.current;if(!b)return;let ee=ie(Ue(A.clientY,b.colEl),f.config);if(b.kind==="resize"){let U=a.find($=>$.id===b.eventId);if(!U)return;let oe=Pe(U,b.edge,b.dateOnly,ee);b.payload=oe,M(oe)}else{b.currentMinute=ee;let U=fe({dateOnly:b.dateOnly,minuteOfDay:b.anchorMinute},{dateOnly:b.dateOnly,minuteOfDay:ee}),$=le([{id:"__sel",title:"",start:U.start,end:U.end}],b.dateOnly,f.config)[0];Q($?{dateOnly:b.dateOnly,topFraction:$.topFraction,heightFraction:$.heightFraction}:null)}},y=A=>{let b=R.current;R.current=null,M(null),Q(null),A&&b&&(b.kind==="resize"&&b.payload&&l&&l(b.payload),b.kind==="select"&&b.currentMinute!==b.anchorMinute&&d&&d(fe({dateOnly:b.dateOnly,minuteOfDay:b.anchorMinute},{dateOnly:b.dateOnly,minuteOfDay:b.currentMinute}))),C({type:A?"COMMIT":"CANCEL"})},p=()=>y(!0),S=()=>y(!1),z=A=>{A.key==="Escape"&&y(!1)};return window.addEventListener("pointermove",m),window.addEventListener("pointerup",p),window.addEventListener("pointercancel",S),window.addEventListener("keydown",z),()=>{window.removeEventListener("pointermove",m),window.removeEventListener("pointerup",p),window.removeEventListener("pointercancel",S),window.removeEventListener("keydown",z)}},[Re,a,f.config,l,d]);let L=w.useCallback((m,y)=>p=>{if(!g||p.target!==p.currentTarget)return;if(p.preventDefault(),!y){g({start:`${m}T00:00:00`});return}let S=ie(Ue(p.clientY,p.currentTarget),f.config),z=u(`${m}T00:00:00`),A=new Date(z.getFullYear(),z.getMonth(),z.getDate(),0,S,0);g({start:k(A)})},[g,f.config]),Te={"--ac-tg-cols":f.columns.length,"--ac-tg-hours":f.config.dayEndHour-f.config.dayStartHour};return re("div",{className:be("aethercal-calendar","aethercal-timegrid",T&&"is-dragging",D.status==="resizing"&&"is-resizing",D.status==="selecting"&&"is-selecting"),role:"grid","aria-label":yt(n,r),"data-view":t,style:Te,children:[re("div",{className:"aethercal-tg-head",role:"row",children:[P("div",{className:"aethercal-tg-corner"}),f.columns.map(m=>P("div",{role:"columnheader",className:be("aethercal-tg-colhead",m.dateOnly===I&&"is-today"),"data-date":m.dateOnly,children:P("span",{className:"aethercal-tg-colhead-date",children:mt(m.dateOnly,r)})},m.dateOnly))]}),re("div",{className:"aethercal-tg-allday",role:"row",children:[P("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:c}),f.columns.map(m=>P("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":m.dateOnly,onDragOver:W?y=>y.preventDefault():void 0,onDrop:W?Y(m.dateOnly,!1):void 0,onContextMenu:g?L(m.dateOnly,!1):void 0,children:m.allDay.map(y=>P(he,{event:y,timeLabel:null,onDragStart:G,onDragEnd:K,isPending:E.has(y.id),isRolledBack:h.has(y.id),...v?{onClick:()=>v({id:y.id})}:{},...g?{onContextMenu:()=>g({id:y.id})}:{}},y.id))},m.dateOnly))]}),re("div",{className:"aethercal-tg-body",role:"row",children:[P("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:f.hourMarks.map(m=>P("div",{className:"aethercal-tg-hour",style:{top:Z(m.topFraction)},children:ft(m.hour,r)},m.hour))}),f.columns.map(m=>re("div",{role:"gridcell",className:be("aethercal-tg-col",m.dateOnly===I&&"is-today"),"data-date":m.dateOnly,onDragOver:W?y=>y.preventDefault():void 0,onDrop:W?Y(m.dateOnly,!0):void 0,onPointerDown:_?xe(m.dateOnly):void 0,onContextMenu:g?L(m.dateOnly,!0):void 0,children:[f.hourMarks.map(y=>P("div",{className:"aethercal-tg-line",style:{top:Z(y.topFraction)},"aria-hidden":"true"},y.hour)),O&&O.dateOnly===m.dateOnly?P("div",{className:"aethercal-tg-select-band",style:{top:Z(O.topFraction),height:Z(O.heightFraction)},"aria-hidden":"true"}):null,m.timed.map(y=>{let{event:p}=y,S=p.editable!==!1,z=ne(p.start,r),A=N?.id===p.id?N:null,b=A?le([{...p,start:A.start,end:A.end}],m.dateOnly,f.config)[0]:void 0,ee=b?b.topFraction:y.topFraction,U=b?b.heightFraction:y.heightFraction,oe={top:Z(ee),height:Z(U),left:Z(y.lane/y.laneCount),width:Z(1/y.laneCount),...p.color?{"--ac-tg-event-accent":p.color}:{}};return re("div",{className:be("aethercal-tg-event",!S&&"is-locked",E.has(p.id)&&"is-pending",h.has(p.id)&&"is-rolledback",!!A&&"is-resizing"),draggable:S,"data-event-id":p.id,"data-lane":y.lane,"data-lane-count":y.laneCount,"aria-label":`${z} ${p.title}`,title:p.title,style:oe,onDragStart:$=>{if(R.current?.kind==="resize"){$.preventDefault();return}$.dataTransfer.setData("text/plain",p.id),$.dataTransfer.effectAllowed="move",G(p.id)},onDragEnd:K,onClick:v?()=>v({id:p.id}):void 0,onContextMenu:g?$=>{$.preventDefault(),$.stopPropagation(),g({id:p.id})}:void 0,children:[P("time",{className:"aethercal-tg-event-time",children:z}),P("span",{className:"aethercal-tg-event-title",children:p.title}),B&&S?re(_t,{children:[P("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:J(p,"start")}),P("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:J(p,"end")})]}):null]},p.id)}),x!==null&&m.dateOnly===I?P("div",{className:"aethercal-now-indicator",style:{top:Z(x)},"aria-hidden":"true"}):null]},m.dateOnly))]})]})}import{jsx as Ee}from"react/jsx-runtime";function Gt(e){return e instanceof Date?e:typeof e=="string"?u(e):new Date}function zt(e){return e instanceof Date?e:typeof e=="string"?u(e):new Date}var $t=e=>`+${e} more`,Ht=e=>`ends ${e}`;function De(e){let{view:t="month",events:n,anchor:a,locale:r="en",firstDayOfWeek:i=1,maxEventsPerDay:o=3,weekdayLabels:c,formatMore:s=$t,unavailableLabel:l="This view is not available yet.",dayStartHour:d,dayEndHour:v,allDayLabel:g="All day",now:E,continuesLabel:h="Continues",formatEndsLabel:f=Ht,agendaEmptyLabel:x="No events",onEventDrop:I,onEventResize:D,onRangeSelect:C,onEventClick:R,onContextMenu:N,pendingIds:M,rolledBackIds:O}=e;q.useEffect(()=>{ce()},[]);let Q=q.useMemo(()=>Gt(a),[a]),[W,B]=q.useState(()=>new Date);q.useEffect(()=>{if(E!==void 0||t!=="week"&&t!=="day")return;let J=setInterval(()=>B(new Date),6e4);return()=>clearInterval(J)},[E,t]);let _=q.useMemo(()=>E!==void 0?zt(E):W,[E,W]),T=Number.isInteger(i)&&i>=0&&i<=6?i:1,Y=Number.isInteger(o)&&o>=0?o:3,G=c&&c.length===7?c:void 0,K=q.useMemo(()=>({...d!==void 0?{dayStartHour:d}:{},...v!==void 0?{dayEndHour:v}:{}}),[d,v]);if(t==="list")return Ee(it,{events:n??[],locale:r,allDayLabel:g,continuesLabel:h,formatEndsLabel:f,emptyLabel:x});if(t==="month")return Ee(ct,{events:n??[],anchor:Q,locale:r,firstDayOfWeek:T,maxEventsPerDay:Y,formatMore:s,...G?{weekdayLabels:G}:{},...I?{onEventDrop:I}:{},...R?{onEventClick:R}:{},...N?{onContextMenu:N}:{},...M?{pendingIds:M}:{},...O?{rolledBackIds:O}:{}});if(t==="week"||t==="day"){let J=t==="week"?we(Q,T):[V(k(Q))];return Ee(Ve,{view:t,days:J,events:n??[],locale:r,config:K,now:_,allDayLabel:g,...I?{onEventDrop:I}:{},...D?{onEventResize:D}:{},...C?{onRangeSelect:C}:{},...R?{onEventClick:R}:{},...N?{onContextMenu:N}:{},...M?{pendingIds:M}:{},...O?{rolledBackIds:O}:{}})}return Ee("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,children:l})}var Bt=De;import*as F from"react";function Yt(){return typeof crypto<"u"&&typeof crypto.randomUUID=="function"?crypto.randomUUID():`cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}var Ut=8e3,Vt=900;function We(e){let{events:t,mutate:n,timeoutMs:a=Ut,rollbackFlashMs:r=Vt,generateId:i=Yt}=e,[o,c]=F.useReducer(Fe,Oe),s=F.useRef(o);s.current=o;let l=F.useRef(t);l.current=t;let d=F.useRef(!0),v=F.useRef(new Map);F.useEffect(()=>{let h=v.current;return()=>{d.current=!1;for(let f of h.values())clearTimeout(f);h.clear()}},[]),F.useEffect(()=>{for(let h of _e(t,s.current)){let f=s.current.overrides[h];c({type:"CLEAR",id:h,...f?{clientMutationId:f.clientMutationId}:{}})}},[t]);let g=F.useCallback((h,f)=>{let x=i(),I=l.current.find(M=>M.id===f.id),D=v.current,C=M=>{let O=D.get(M);O!==void 0&&(clearTimeout(O),D.delete(M))},R=()=>{D.set(`fl:${x}`,setTimeout(()=>{D.delete(`fl:${x}`),d.current&&c({type:"CLEAR",id:f.id,clientMutationId:x})},r))};c({type:"SUBMIT",id:f.id,clientMutationId:x,start:f.start,end:f.end,...I?.revision!==void 0?{baseRevision:I.revision}:{}}),D.set(`to:${x}`,setTimeout(()=>{D.delete(`to:${x}`),d.current&&(c({type:"TIMEOUT",id:f.id,clientMutationId:x}),R())},a));let N={kind:h,clientMutationId:x,payload:{...f,client_mutation_id:x}};n(N).then(M=>{C(`to:${x}`),d.current&&c({type:"RESOLVE",id:M.id,clientMutationId:x,start:M.start,end:M.end,revision:M.revision})}).catch(()=>{C(`to:${x}`),d.current&&(c({type:"REJECT",id:f.id,clientMutationId:x}),R())})},[n,a,r,i]),E=F.useMemo(()=>Ne(t,o),[t,o]);return{events:E.events,pendingIds:E.pendingIds,rolledBackIds:E.rolledBackIds,submit:g}}import{jsx as Kt}from"react/jsx-runtime";function Wt({events:e,mutate:t,timeoutMs:n,rollbackFlashMs:a,generateId:r,...i}){let{events:o,pendingIds:c,rolledBackIds:s,submit:l}=We({events:e,mutate:t,...n!==void 0?{timeoutMs:n}:{},...a!==void 0?{rollbackFlashMs:a}:{},...r?{generateId:r}:{}});return Kt(De,{...i,events:o,pendingIds:c,rolledBackIds:s,onEventDrop:d=>l("drop",d),onEventResize:d=>l("resize",d)})}export{De as AetherCalendar,gt as CALENDAR_CSS,Wt as OptimisticCalendar,ht as TIME_GRID_CSS,Ve as TimeGridView,Bt as default,ce as ensureCalendarStyles,Ye as ensureTimeGridStyles,We as useOptimisticEvents};
