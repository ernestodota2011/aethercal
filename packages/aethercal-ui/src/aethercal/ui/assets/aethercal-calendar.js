function y(e){return String(e).padStart(2,"0")}function g(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,a,n,r,o,i,s]=t,d=Number(a),l=Number(n),u=Number(r),f=Number(o??"0"),D=Number(i??"0"),E=Number(s??"0");if(l<1||l>12||u<1||u>31||f>23||D>59||E>59)throw new Error(`out-of-range ISO datetime: ${e}`);let m=new Date(d,l-1,u,f,D,E);if(m.getFullYear()!==d||m.getMonth()!==l-1||m.getDate()!==u)throw new Error(`nonexistent calendar date: ${e}`);return m}function C(e){return`${e.getFullYear()}-${y(e.getMonth()+1)}-${y(e.getDate())}T${y(e.getHours())}:${y(e.getMinutes())}:${y(e.getSeconds())}`}function F(e){let t=g(e);return`${t.getFullYear()}-${y(t.getMonth()+1)}-${y(t.getDate())}`}function me(e,t){return(e.getDay()-t+7)%7}function J(e,t=1){let a=new Date(e.getFullYear(),e.getMonth(),e.getDate());return a.setDate(a.getDate()-me(a,t)),a}function ye(e,t){return Array.from({length:t},(a,n)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+n);return`${r.getFullYear()}-${y(r.getMonth()+1)}-${y(r.getDate())}`})}function I(e,t=1){let a=new Date(e.getFullYear(),e.getMonth(),1);return ye(J(a,t),42)}function De(e,t){let a=new Date(e.getFullYear(),e.getMonth(),e.getDate()),n=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((n.getTime()-a.getTime())/864e5)}function O(e,t){let a=g(e.start),n=g(e.end),r=g(t),o=De(a,r),i=new Date(a.getFullYear(),a.getMonth(),a.getDate()+o,a.getHours(),a.getMinutes(),a.getSeconds()),s=new Date(n.getFullYear(),n.getMonth(),n.getDate()+o,n.getHours(),n.getMinutes(),n.getSeconds()),d={id:e.id,start:C(i),end:C(s)};return e.revision!==void 0&&(d.revision=e.revision),d}var he=370;function q(e){return String(e).padStart(2,"0")}function Y(e){return`${e.getFullYear()}-${q(e.getMonth()+1)}-${q(e.getDate())}`}function Q(e){return new Date(e.getFullYear(),e.getMonth(),e.getDate())}function be(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function ve(e){let t=g(e.start),a=g(e.end),n=Q(t),r;a.getTime()<=t.getTime()?r=n:(r=Q(new Date(a.getTime()-1)),r.getTime()<n.getTime()&&(r=n));let o=[],i=n;for(let s=0;s<he&&i.getTime()<=r.getTime();s+=1)o.push(Y(i)),i=be(i,1);return{keys:o,startKey:Y(n),lastKey:Y(r)}}function V(e){let t=new Map;return e.forEach((a,n)=>{let{keys:r,startKey:o,lastKey:i}=ve(a),s=g(a.start).getTime(),d=g(a.end).getTime();for(let l of r){let u={entry:{event:a,isContinuation:l!==o,continuesAfter:l!==i},startMs:s,endMs:d,index:n},f=t.get(l);f?f.push(u):t.set(l,[u])}}),[...t.keys()].sort().map(a=>{let n=t.get(a);return n.sort((r,o)=>r.startMs-o.startMs||r.endMs-o.endMs||r.index-o.index),{date:a,entries:n.map(r=>r.entry)}})}var N={status:"idle"};function _(e){return e.status==="dragging"}function K(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return N}}import*as $ from"react";import*as W from"react";var z=new Date(2023,0,1);function Z(e,t){let a=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(n,r)=>{let o=(t+r)%7,i=new Date(z.getFullYear(),z.getMonth(),z.getDate()+o);return a.format(i)})}function j(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function ee(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(g(e))}function R(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(g(e))}function te(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(g(e))}import{jsx as b,jsxs as ne}from"react/jsx-runtime";function xe(...e){return e.filter(Boolean).join(" ")}function Ee(e,t,a){let{event:n,isContinuation:r,continuesAfter:o}=e;return n.allDay===!0?a.allDayLabel:r?o?a.continuesLabel:a.formatEndsLabel(R(n.end,t)):R(n.start,t)}function we({entry:e,locale:t,labels:a}){let{event:n,isContinuation:r,continuesAfter:o}=e,i=Ee(e,t,a),s=n.color?{"--ac-event-accent":n.color}:void 0;return ne("li",{className:xe("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":n.id,"aria-label":`${i} ${n.title}`,style:s,...n.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...o?{"data-continues-after":""}:{},children:[b("span",{className:"aethercal-agenda-event-time",children:i}),b("span",{className:"aethercal-agenda-event-title",children:n.title})]})}function ae({events:e,locale:t,allDayLabel:a,continuesLabel:n,formatEndsLabel:r,emptyLabel:o}){let i=W.useMemo(()=>V(e),[e]),s=W.useId(),d={allDayLabel:a,continuesLabel:n,formatEndsLabel:r};return i.length===0?b("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:b("p",{className:"aethercal-agenda-empty",children:o})}):b("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:i.map(l=>{let u=`${s}-${l.date}`;return ne("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":u,"data-date":l.date,children:[b("div",{className:"aethercal-agenda-day-title",id:u,children:te(l.date,t)}),b("ul",{className:"aethercal-agenda-day-events",role:"list",children:l.entries.map((f,D)=>b(we,{entry:f,locale:t,labels:d},`${f.event.id}-${D}`))})]},l.date)})})}import*as p from"react";import{jsx as re,jsxs as ke}from"react/jsx-runtime";function oe({event:e,timeLabel:t,onDragStart:a,onDragEnd:n}){let r=e.editable!==!1,o=e.color?{"--ac-event-accent":e.color}:void 0,i=t?`${t} ${e.title}`:e.title;return ke("div",{className:r?"aethercal-event":"aethercal-event is-locked",draggable:r,"data-event-id":e.id,"aria-label":i,title:e.title,style:o,onDragStart:s=>{s.dataTransfer.setData("text/plain",e.id),s.dataTransfer.effectAllowed="move",a(e.id)},onDragEnd:n,children:[t?re("time",{className:"aethercal-event-time",children:t}):null,re("span",{className:"aethercal-event-title",children:e.title})]})}import{jsx as x,jsxs as G}from"react/jsx-runtime";function ie(...e){return e.filter(Boolean).join(" ")}function Ae(e){let t=[];for(let a=0;a<e.length;a+=7)t.push(e.slice(a,a+7));return t}function Le(e){let t=new Map;for(let a of e){let n=F(a.start),r=t.get(n);r?r.push(a):t.set(n,[a])}return t}function se(e){let{events:t,anchor:a,locale:n,firstDayOfWeek:r,weekdayLabels:o,maxEventsPerDay:i,formatMore:s,onEventDrop:d}=e,l=p.useMemo(()=>I(a,r),[a,r]),u=p.useMemo(()=>Ae(l),[l]),f=p.useMemo(()=>o??Z(n,r),[o,n,r]),D=p.useMemo(()=>Le(t),[t]),E=a.getMonth(),m=F(C(new Date)),[w,k]=p.useReducer(K,N),[P,M]=p.useState(()=>new Set),ge=p.useCallback(v=>{M(h=>{let c=new Set(h);return c.add(v),c})},[]),ue=p.useCallback(v=>h=>{if(h.preventDefault(),!_(w)){k({type:"DROP"});return}let c=w.eventId,A=h.dataTransfer.getData("text/plain");if(k({type:"DROP"}),A&&A!==c||!d)return;let L=t.find(T=>T.id===c);!L||L.editable===!1||d(O(L,v))},[w,t,d]),U=!!d;return G("div",{className:ie("aethercal-calendar",_(w)&&"is-dragging"),role:"grid","aria-label":j(a,n),"data-view":"month",children:[x("div",{className:"aethercal-weekdays",role:"row",children:f.map((v,h)=>x("div",{role:"columnheader",className:"aethercal-weekday",children:v},h))}),u.map((v,h)=>x("div",{className:"aethercal-week",role:"row",children:v.map(c=>{let A=D.get(c)??[],L=P.has(c),T=L?A:A.slice(0,i),X=A.length-T.length,fe=new Date(`${c}T00:00:00`).getMonth()!==E;return G("div",{role:"gridcell",className:ie("aethercal-day",fe&&"is-outside",c===m&&"is-today"),"data-date":c,"aria-label":ee(c,n),onDragOver:U?S=>S.preventDefault():void 0,onDrop:U?ue(c):void 0,children:[x("div",{className:"aethercal-day-head",children:x("span",{className:"aethercal-day-number",children:Number(c.slice(-2))})}),G("div",{className:"aethercal-day-events",children:[T.map(S=>x(oe,{event:S,timeLabel:S.allDay?null:R(S.start,n),onDragStart:pe=>k({type:"DRAG_START",eventId:pe}),onDragEnd:()=>k({type:"DRAG_CANCEL"})},S.id)),X>0&&!L?x("button",{type:"button",className:"aethercal-more",onClick:()=>ge(c),children:s(X)}):null]})]},c)})},h))]})}var le="aethercal-calendar-styles",de=`
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
`;function H(){if(typeof document>"u"||document.getElementById(le))return;let e=document.createElement("style");e.id=le,e.textContent=de,document.head.appendChild(e)}import{jsx as B}from"react/jsx-runtime";function Se(e){return e instanceof Date?e:typeof e=="string"?g(e):new Date}var Ce=e=>`+${e} more`,Re=e=>`ends ${e}`;function ce(e){let{view:t="month",events:a,anchor:n,locale:r="en",firstDayOfWeek:o=1,maxEventsPerDay:i=3,weekdayLabels:s,formatMore:d=Ce,unavailableLabel:l="This view is not available yet.",allDayLabel:u="All day",continuesLabel:f="Continues",formatEndsLabel:D=Re,agendaEmptyLabel:E="No events",onEventDrop:m}=e;$.useEffect(()=>{H()},[]);let w=$.useMemo(()=>Se(n),[n]),k=Number.isInteger(o)&&o>=0&&o<=6?o:1,P=Number.isInteger(i)&&i>=0?i:3,M=s&&s.length===7?s:void 0;return t==="list"?B(ae,{events:a??[],locale:r,allDayLabel:u,continuesLabel:f,formatEndsLabel:D,emptyLabel:E}):t!=="month"?B("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,children:l}):B(se,{events:a??[],anchor:w,locale:r,firstDayOfWeek:k,maxEventsPerDay:P,formatMore:d,...M?{weekdayLabels:M}:{},...m?{onEventDrop:m}:{}})}var Me=ce;export{ce as AetherCalendar,de as CALENDAR_CSS,Me as default,H as ensureCalendarStyles};
