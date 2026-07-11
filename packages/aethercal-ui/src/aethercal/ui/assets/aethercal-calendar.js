function m(e){return String(e).padStart(2,"0")}function g(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,a,r,n,o,s,l]=t,d=Number(a),f=Number(r),u=Number(n),v=Number(o??"0"),b=Number(s??"0"),x=Number(l??"0");if(f<1||f>12||u<1||u>31||v>23||b>59||x>59)throw new Error(`out-of-range ISO datetime: ${e}`);let p=new Date(d,f-1,u,v,b,x);if(p.getFullYear()!==d||p.getMonth()!==f-1||p.getDate()!==u)throw new Error(`nonexistent calendar date: ${e}`);return p}function S(e){return`${e.getFullYear()}-${m(e.getMonth()+1)}-${m(e.getDate())}T${m(e.getHours())}:${m(e.getMinutes())}:${m(e.getSeconds())}`}function T(e){let t=g(e);return`${t.getFullYear()}-${m(t.getMonth()+1)}-${m(t.getDate())}`}function ie(e,t){return(e.getDay()-t+7)%7}function G(e,t=1){let a=new Date(e.getFullYear(),e.getMonth(),e.getDate());return a.setDate(a.getDate()-ie(a,t)),a}function se(e,t){return Array.from({length:t},(a,r)=>{let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()+r);return`${n.getFullYear()}-${m(n.getMonth()+1)}-${m(n.getDate())}`})}function _(e,t=1){let a=new Date(e.getFullYear(),e.getMonth(),1);return se(G(a,t),42)}function le(e,t){let a=new Date(e.getFullYear(),e.getMonth(),e.getDate()),r=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((r.getTime()-a.getTime())/864e5)}function N(e,t){let a=g(e.start),r=g(e.end),n=g(t),o=le(a,n),s=new Date(a.getFullYear(),a.getMonth(),a.getDate()+o,a.getHours(),a.getMinutes(),a.getSeconds()),l=new Date(r.getFullYear(),r.getMonth(),r.getDate()+o,r.getHours(),r.getMinutes(),r.getSeconds()),d={id:e.id,start:S(s),end:S(l)};return e.revision!==void 0&&(d.revision=e.revision),d}var A={status:"idle"};function F(e){return e.status==="dragging"}function W(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return A}}import*as L from"react";import*as c from"react";import{jsx as V,jsxs as de}from"react/jsx-runtime";function z({event:e,timeLabel:t,onDragStart:a,onDragEnd:r}){let n=e.editable!==!1,o=e.color?{"--ac-event-accent":e.color}:void 0,s=t?`${t} ${e.title}`:e.title;return de("div",{className:n?"aethercal-event":"aethercal-event is-locked",draggable:n,"data-event-id":e.id,"aria-label":s,title:e.title,style:o,onDragStart:l=>{l.dataTransfer.setData("text/plain",e.id),l.dataTransfer.effectAllowed="move",a(e.id)},onDragEnd:r,children:[t?V("time",{className:"aethercal-event-time",children:t}):null,V("span",{className:"aethercal-event-title",children:e.title})]})}var P=new Date(2023,0,1);function U(e,t){let a=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(r,n)=>{let o=(t+n)%7,s=new Date(P.getFullYear(),P.getMonth(),P.getDate()+o);return a.format(s)})}function B(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function H(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(g(e))}function K(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(g(e))}import{jsx as h,jsxs as O}from"react/jsx-runtime";function J(...e){return e.filter(Boolean).join(" ")}function ce(e){let t=[];for(let a=0;a<e.length;a+=7)t.push(e.slice(a,a+7));return t}function ge(e){let t=new Map;for(let a of e){let r=T(a.start),n=t.get(r);n?n.push(a):t.set(r,[a])}return t}function X(e){let{events:t,anchor:a,locale:r,firstDayOfWeek:n,weekdayLabels:o,maxEventsPerDay:s,formatMore:l,onEventDrop:d}=e,f=c.useMemo(()=>_(a,n),[a,n]),u=c.useMemo(()=>ce(f),[f]),v=c.useMemo(()=>o??U(r,n),[o,r,n]),b=c.useMemo(()=>ge(t),[t]),x=a.getMonth(),p=T(S(new Date)),[C,R]=c.useReducer(W,A),[ee,te]=c.useState(()=>new Set),ae=c.useCallback(D=>{te(y=>{let i=new Set(y);return i.add(D),i})},[]),re=c.useCallback(D=>y=>{if(y.preventDefault(),!F(C)){R({type:"DROP"});return}let i=C.eventId,E=y.dataTransfer.getData("text/plain");if(R({type:"DROP"}),E&&E!==i||!d)return;let w=t.find(M=>M.id===i);!w||w.editable===!1||d(N(w,D))},[C,t,d]),$=!!d;return O("div",{className:J("aethercal-calendar",F(C)&&"is-dragging"),role:"grid","aria-label":B(a,r),"data-view":"month",children:[h("div",{className:"aethercal-weekdays",role:"row",children:v.map((D,y)=>h("div",{role:"columnheader",className:"aethercal-weekday",children:D},y))}),u.map((D,y)=>h("div",{className:"aethercal-week",role:"row",children:D.map(i=>{let E=b.get(i)??[],w=ee.has(i),M=w?E:E.slice(0,s),Y=E.length-M.length,ne=new Date(`${i}T00:00:00`).getMonth()!==x;return O("div",{role:"gridcell",className:J("aethercal-day",ne&&"is-outside",i===p&&"is-today"),"data-date":i,"aria-label":H(i,r),onDragOver:$?k=>k.preventDefault():void 0,onDrop:$?re(i):void 0,children:[h("div",{className:"aethercal-day-head",children:h("span",{className:"aethercal-day-number",children:Number(i.slice(-2))})}),O("div",{className:"aethercal-day-events",children:[M.map(k=>h(z,{event:k,timeLabel:k.allDay?null:K(k.start,r),onDragStart:oe=>R({type:"DRAG_START",eventId:oe}),onDragEnd:()=>R({type:"DRAG_CANCEL"})},k.id)),Y>0&&!w?h("button",{type:"button",className:"aethercal-more",onClick:()=>ae(i),children:l(Y)}):null]})]},i)})},y))]})}var q="aethercal-calendar-styles",Q=`
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
`;function I(){if(typeof document>"u"||document.getElementById(q))return;let e=document.createElement("style");e.id=q,e.textContent=Q,document.head.appendChild(e)}import{jsx as Z}from"react/jsx-runtime";function fe(e){return e instanceof Date?e:typeof e=="string"?g(e):new Date}var ue=e=>`+${e} more`;function j(e){let{view:t="month",events:a,anchor:r,locale:n="en",firstDayOfWeek:o=1,maxEventsPerDay:s=3,weekdayLabels:l,formatMore:d=ue,unavailableLabel:f="This view is not available yet.",onEventDrop:u}=e;L.useEffect(()=>{I()},[]);let v=L.useMemo(()=>fe(r),[r]),b=Number.isInteger(o)&&o>=0&&o<=6?o:1,x=Number.isInteger(s)&&s>=0?s:3,p=l&&l.length===7?l:void 0;return t!=="month"?Z("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,children:f}):Z(X,{events:a??[],anchor:v,locale:n,firstDayOfWeek:b,maxEventsPerDay:x,formatMore:d,...p?{weekdayLabels:p}:{},...u?{onEventDrop:u}:{}})}var pe=j;export{j as AetherCalendar,Q as CALENDAR_CSS,pe as default,I as ensureCalendarStyles};
