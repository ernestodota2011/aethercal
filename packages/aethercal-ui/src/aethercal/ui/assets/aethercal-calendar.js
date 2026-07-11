function L(e){return String(e).padStart(2,"0")}function d(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,a,r,n,o,l,c]=t,s=Number(a),i=Number(r),p=Number(n),y=Number(o??"0"),u=Number(l??"0"),f=Number(c??"0");if(i<1||i>12||p<1||p>31||y>23||u>59||f>59)throw new Error(`out-of-range ISO datetime: ${e}`);let m=new Date(s,i-1,p,y,u,f);if(m.getFullYear()!==s||m.getMonth()!==i-1||m.getDate()!==p)throw new Error(`nonexistent calendar date: ${e}`);return m}function N(e){return`${e.getFullYear()}-${L(e.getMonth()+1)}-${L(e.getDate())}T${L(e.getHours())}:${L(e.getMinutes())}:${L(e.getSeconds())}`}function S(e){let t=d(e);return`${t.getFullYear()}-${L(t.getMonth()+1)}-${L(t.getDate())}`}function He(e,t){return(e.getDay()-t+7)%7}function Z(e,t=1){let a=new Date(e.getFullYear(),e.getMonth(),e.getDate());return a.setDate(a.getDate()-He(a,t)),a}function ue(e,t){return Array.from({length:t},(a,r)=>{let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()+r);return`${n.getFullYear()}-${L(n.getMonth()+1)}-${L(n.getDate())}`})}function ee(e,t=1){return ue(Z(e,t),7)}function te(e,t=1){let a=new Date(e.getFullYear(),e.getMonth(),1);return ue(Z(a,t),42)}function Oe(e,t){let a=new Date(e.getFullYear(),e.getMonth(),e.getDate()),r=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((r.getTime()-a.getTime())/864e5)}function B(e,t){let a=d(e.start),r=d(e.end),n=d(t),o=Oe(a,n),l=new Date(a.getFullYear(),a.getMonth(),a.getDate()+o,a.getHours(),a.getMinutes(),a.getSeconds()),c=new Date(r.getFullYear(),r.getMonth(),r.getDate()+o,r.getHours(),r.getMinutes(),r.getSeconds()),s={id:e.id,start:N(l),end:N(c)};return e.revision!==void 0&&(s.revision=e.revision),s}var $e=370;function me(e){return String(e).padStart(2,"0")}function ae(e){return`${e.getFullYear()}-${me(e.getMonth()+1)}-${me(e.getDate())}`}function pe(e){return new Date(e.getFullYear(),e.getMonth(),e.getDate())}function We(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function Ye(e){let t=d(e.start),a=d(e.end),r=pe(t),n;a.getTime()<=t.getTime()?n=r:(n=pe(new Date(a.getTime()-1)),n.getTime()<r.getTime()&&(n=r));let o=[],l=r;for(let c=0;c<$e&&l.getTime()<=n.getTime();c+=1)o.push(ae(l)),l=We(l,1);return{keys:o,startKey:ae(r),lastKey:ae(n)}}function re(e){let t=new Map;return e.forEach((a,r)=>{let{keys:n,startKey:o,lastKey:l}=Ye(a),c=d(a.start).getTime(),s=d(a.end).getTime();for(let i of n){let p={entry:{event:a,isContinuation:i!==o,continuesAfter:i!==l},startMs:c,endMs:s,index:r},y=t.get(i);y?y.push(p):t.set(i,[p])}}),[...t.keys()].sort().map(a=>{let r=t.get(a);return r.sort((n,o)=>n.startMs-o.startMs||n.endMs-o.endMs||n.index-o.index),{date:a,entries:r.map(n=>n.entry)}})}var Y={status:"idle"};function P(e){return e.status==="dragging"}function z(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return Y}}var F=60,Ve=24*F,Be=864e5;function X(e,t,a){return Math.min(a,Math.max(t,e))}function ne(e={}){let t=e.dayStartHour,a=e.dayEndHour,r=Number.isFinite(t)&&t!==void 0?X(Math.trunc(t),0,23):0,n=Number.isFinite(a)&&a!==void 0?X(Math.trunc(a),1,24):24,[o,l]=n>r?[r,n]:[0,24];return{dayStartHour:o,dayEndHour:l,windowMinutes:(l-o)*F}}function ye(e){let t=[],a=[];for(let r of e)r.allDay===!0?t.push(r):a.push(r);return{allDay:t,timed:a}}function fe(e,t){let a=d(e),r=new Date(a.getFullYear(),a.getMonth(),a.getDate()),n=Math.round((r.getTime()-t.getTime())/Be),o=a.getHours()*F+a.getMinutes()+a.getSeconds()/60;return n*Ve+o}function ze(e,t){let a=d(e.start).getTime(),r=d(e.end).getTime(),n=d(t.start).getTime(),o=d(t.end).getTime();return a<o&&n<r}function he(e,t,a){let r=d(`${t}T00:00:00`),n=a.dayStartHour*F,o=a.dayEndHour*F,l=[...e].sort((u,f)=>{let m=d(u.start).getTime(),b=d(f.start).getTime();return m!==b?m-b:d(f.end).getTime()-d(u.end).getTime()}),c=[],s=[],i=[],p=Number.NEGATIVE_INFINITY,y=()=>{let u=s.length;for(let f of i)c[f].laneCount=u;s=[],i=[],p=Number.NEGATIVE_INFINITY};for(let u of l){let f=fe(u.start,r),m=fe(u.end,r);if(m<=n||f>=o)continue;let b=d(u.start).getTime(),k=d(u.end).getTime();i.length>0&&b>=p&&y();let x=s.findIndex(D=>!ze(D,u));x===-1?(x=s.length,s.push(u)):s[x]=u;let M=X(f,n,o),g=X(m,M,o),h=(M-n)/a.windowMinutes,A=(g-M)/a.windowMinutes;i.push(c.length),c.push({event:u,lane:x,laneCount:1,topFraction:h,heightFraction:A}),p=Math.max(p,k)}return y(),c}function Ke(e){let t=[];for(let a=e.dayStartHour;a<e.dayEndHour;a+=1)t.push({hour:a,topFraction:(a-e.dayStartHour)*F/e.windowMinutes});return t}function oe(e,t,a={}){let r="windowMinutes"in a?a:ne(a),{allDay:n,timed:o}=ye(t),l=o.map(s=>({event:s,startTs:d(s.start).getTime(),endTs:d(s.end).getTime()}));return{columns:e.map(s=>{let i=d(`${s}T00:00:00`),p=i.getTime(),y=new Date(i.getFullYear(),i.getMonth(),i.getDate()+1).getTime(),u=l.filter(m=>m.startTs>=y?!1:m.endTs>p?!0:m.startTs===m.endTs&&m.startTs>=p).map(m=>m.event),f=n.filter(m=>S(m.start)<=s&&s<=S(m.end));return{dateOnly:s,allDay:f,timed:he(u,s,r)}}),hourMarks:Ke(r),config:r}}function ie(e,t={}){let a="windowMinutes"in t?t:ne(t),r=e.getHours()*F+e.getMinutes()+e.getSeconds()/60,n=a.dayStartHour*F,o=a.dayEndHour*F;return r<n||r>=o?null:(r-n)/a.windowMinutes}import*as G from"react";import*as J from"react";var le=new Date(2023,0,1);function ve(e,t){let a=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(r,n)=>{let o=(t+n)%7,l=new Date(le.getFullYear(),le.getMonth(),le.getDate()+o);return a.format(l)})}function be(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function De(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(d(e))}function H(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(d(e))}function xe(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(d(e))}import{jsx as I,jsxs as we}from"react/jsx-runtime";function Ue(...e){return e.filter(Boolean).join(" ")}function Xe(e,t,a){let{event:r,isContinuation:n,continuesAfter:o}=e;return r.allDay===!0?a.allDayLabel:n?o?a.continuesLabel:a.formatEndsLabel(H(r.end,t)):H(r.start,t)}function Je({entry:e,locale:t,labels:a}){let{event:r,isContinuation:n,continuesAfter:o}=e,l=Xe(e,t,a),c=r.color?{"--ac-event-accent":r.color}:void 0;return we("li",{className:Ue("aethercal-agenda-event",n&&"is-continuation"),"data-event-id":r.id,"aria-label":`${l} ${r.title}`,style:c,...r.allDay===!0?{"data-all-day":""}:{},...n?{"data-continuation":""}:{},...o?{"data-continues-after":""}:{},children:[I("span",{className:"aethercal-agenda-event-time",children:l}),I("span",{className:"aethercal-agenda-event-title",children:r.title})]})}function Ee({events:e,locale:t,allDayLabel:a,continuesLabel:r,formatEndsLabel:n,emptyLabel:o}){let l=J.useMemo(()=>re(e),[e]),c=J.useId(),s={allDayLabel:a,continuesLabel:r,formatEndsLabel:n};return l.length===0?I("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:I("p",{className:"aethercal-agenda-empty",children:o})}):I("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:l.map(i=>{let p=`${c}-${i.date}`;return we("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":p,"data-date":i.date,children:[I("div",{className:"aethercal-agenda-day-title",id:p,children:xe(i.date,t)}),I("ul",{className:"aethercal-agenda-day-events",role:"list",children:i.entries.map((y,u)=>I(Je,{entry:y,locale:t,labels:s},`${y.event.id}-${u}`))})]},i.date)})})}import*as T from"react";import{jsx as Te,jsxs as je}from"react/jsx-runtime";function j({event:e,timeLabel:t,onDragStart:a,onDragEnd:r}){let n=e.editable!==!1,o=e.color?{"--ac-event-accent":e.color}:void 0,l=t?`${t} ${e.title}`:e.title;return je("div",{className:n?"aethercal-event":"aethercal-event is-locked",draggable:n,"data-event-id":e.id,"aria-label":l,title:e.title,style:o,onDragStart:c=>{c.dataTransfer.setData("text/plain",e.id),c.dataTransfer.effectAllowed="move",a(e.id)},onDragEnd:r,children:[t?Te("time",{className:"aethercal-event-time",children:t}):null,Te("span",{className:"aethercal-event-title",children:e.title})]})}import{jsx as O,jsxs as se}from"react/jsx-runtime";function Ce(...e){return e.filter(Boolean).join(" ")}function qe(e){let t=[];for(let a=0;a<e.length;a+=7)t.push(e.slice(a,a+7));return t}function Qe(e){let t=new Map;for(let a of e){let r=S(a.start),n=t.get(r);n?n.push(a):t.set(r,[a])}return t}function ke(e){let{events:t,anchor:a,locale:r,firstDayOfWeek:n,weekdayLabels:o,maxEventsPerDay:l,formatMore:c,onEventDrop:s}=e,i=T.useMemo(()=>te(a,n),[a,n]),p=T.useMemo(()=>qe(i),[i]),y=T.useMemo(()=>o??ve(r,n),[o,r,n]),u=T.useMemo(()=>Qe(t),[t]),f=a.getMonth(),m=S(N(new Date)),[b,k]=T.useReducer(z,Y),[x,M]=T.useState(()=>new Set),g=T.useCallback(D=>{M(E=>{let v=new Set(E);return v.add(D),v})},[]),h=T.useCallback(D=>E=>{if(E.preventDefault(),!P(b)){k({type:"DROP"});return}let v=b.eventId,_=E.dataTransfer.getData("text/plain");if(k({type:"DROP"}),_&&_!==v||!s)return;let R=t.find(U=>U.id===v);!R||R.editable===!1||s(B(R,D))},[b,t,s]),A=!!s;return se("div",{className:Ce("aethercal-calendar",P(b)&&"is-dragging"),role:"grid","aria-label":be(a,r),"data-view":"month",children:[O("div",{className:"aethercal-weekdays",role:"row",children:y.map((D,E)=>O("div",{role:"columnheader",className:"aethercal-weekday",children:D},E))}),p.map((D,E)=>O("div",{className:"aethercal-week",role:"row",children:D.map(v=>{let _=u.get(v)??[],R=x.has(v),U=R?_:_.slice(0,l),ge=_.length-U.length,Ie=new Date(`${v}T00:00:00`).getMonth()!==f;return se("div",{role:"gridcell",className:Ce("aethercal-day",Ie&&"is-outside",v===m&&"is-today"),"data-date":v,"aria-label":De(v,r),onDragOver:A?W=>W.preventDefault():void 0,onDrop:A?h(v):void 0,children:[O("div",{className:"aethercal-day-head",children:O("span",{className:"aethercal-day-number",children:Number(v.slice(-2))})}),se("div",{className:"aethercal-day-events",children:[U.map(W=>O(j,{event:W,timeLabel:W.allDay?null:H(W.start,r),onDragStart:Pe=>k({type:"DRAG_START",eventId:Pe}),onDragEnd:()=>k({type:"DRAG_CANCEL"})},W.id)),ge>0&&!R?O("button",{type:"button",className:"aethercal-more",onClick:()=>g(v),children:c(ge)}):null]})]},v)})},E))]})}var Me="aethercal-calendar-styles",Se=`
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
`;function K(){if(typeof document>"u"||document.getElementById(Me))return;let e=document.createElement("style");e.id=Me,e.textContent=Se,document.head.appendChild(e)}import*as C from"react";var Re="All day";function Ae(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(d(e))}function Le(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function Ne(e,t){if(e.length===0)return"";let a=d(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(a);let r=d(e[e.length-1]),n=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${n.format(a)} \u2013 ${n.format(r)}`}var Fe="aethercal-timegrid-styles",Ge=`
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
`;function de(){if(typeof document>"u"||document.getElementById(Fe))return;let e=document.createElement("style");e.id=Fe,e.textContent=Ge,document.head.appendChild(e)}import{jsx as w,jsxs as V}from"react/jsx-runtime";function q(...e){return e.filter(Boolean).join(" ")}var $=e=>`${e*100}%`;function Ze(e){let{block:t,locale:a,onDragStart:r,onDragEnd:n}=e,{event:o}=t,l=o.editable!==!1,c=H(o.start,a),s={top:$(t.topFraction),height:$(t.heightFraction),left:$(t.lane/t.laneCount),width:$(1/t.laneCount),...o.color?{"--ac-tg-event-accent":o.color}:{}};return V("div",{className:q("aethercal-tg-event",!l&&"is-locked"),draggable:l,"data-event-id":o.id,"data-lane":t.lane,"data-lane-count":t.laneCount,"aria-label":`${c} ${o.title}`,title:o.title,style:s,onDragStart:i=>{i.dataTransfer.setData("text/plain",o.id),i.dataTransfer.effectAllowed="move",r(o.id)},onDragEnd:n,children:[w("time",{className:"aethercal-tg-event-time",children:c}),w("span",{className:"aethercal-tg-event-title",children:o.title})]})}function ce(e){let{view:t,days:a,events:r,locale:n,config:o,now:l,allDayLabel:c=Re,onEventDrop:s}=e;C.useEffect(()=>{K(),de()},[]);let i=C.useMemo(()=>oe(a,r,o),[a,r,o]),p=C.useMemo(()=>ie(l,o),[l,o]),y=C.useMemo(()=>S(N(l)),[l]),[u,f]=C.useReducer(z,Y),m=C.useCallback(g=>h=>{if(h.preventDefault(),!P(u)){f({type:"DROP"});return}let A=u.eventId,D=h.dataTransfer.getData("text/plain");if(f({type:"DROP"}),D&&D!==A||!s)return;let E=r.find(v=>v.id===A);!E||E.editable===!1||s(B(E,g))},[u,r,s]),b=!!s,k=C.useCallback(g=>f({type:"DRAG_START",eventId:g}),[]),x=C.useCallback(()=>f({type:"DRAG_CANCEL"}),[]),M={"--ac-tg-cols":i.columns.length,"--ac-tg-hours":i.config.dayEndHour-i.config.dayStartHour};return V("div",{className:q("aethercal-calendar","aethercal-timegrid",P(u)&&"is-dragging"),role:"grid","aria-label":Ne(a,n),"data-view":t,style:M,children:[V("div",{className:"aethercal-tg-head",role:"row",children:[w("div",{className:"aethercal-tg-corner"}),i.columns.map(g=>w("div",{role:"columnheader",className:q("aethercal-tg-colhead",g.dateOnly===y&&"is-today"),"data-date":g.dateOnly,children:w("span",{className:"aethercal-tg-colhead-date",children:Ae(g.dateOnly,n)})},g.dateOnly))]}),V("div",{className:"aethercal-tg-allday",role:"row",children:[w("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:c}),i.columns.map(g=>w("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":g.dateOnly,onDragOver:b?h=>h.preventDefault():void 0,onDrop:b?m(g.dateOnly):void 0,children:g.allDay.map(h=>w(j,{event:h,timeLabel:null,onDragStart:k,onDragEnd:x},h.id))},g.dateOnly))]}),V("div",{className:"aethercal-tg-body",role:"row",children:[w("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:i.hourMarks.map(g=>w("div",{className:"aethercal-tg-hour",style:{top:$(g.topFraction)},children:Le(g.hour,n)},g.hour))}),i.columns.map(g=>V("div",{role:"gridcell",className:q("aethercal-tg-col",g.dateOnly===y&&"is-today"),"data-date":g.dateOnly,onDragOver:b?h=>h.preventDefault():void 0,onDrop:b?m(g.dateOnly):void 0,children:[i.hourMarks.map(h=>w("div",{className:"aethercal-tg-line",style:{top:$(h.topFraction)},"aria-hidden":"true"},h.hour)),g.timed.map(h=>w(Ze,{block:h,locale:n,onDragStart:k,onDragEnd:x},h.event.id)),p!==null&&g.dateOnly===y?w("div",{className:"aethercal-now-indicator",style:{top:$(p)},"aria-hidden":"true"}):null]},g.dateOnly))]})]})}import{jsx as Q}from"react/jsx-runtime";function et(e){return e instanceof Date?e:typeof e=="string"?d(e):new Date}function tt(e){return e instanceof Date?e:typeof e=="string"?d(e):new Date}var at=e=>`+${e} more`,rt=e=>`ends ${e}`;function _e(e){let{view:t="month",events:a,anchor:r,locale:n="en",firstDayOfWeek:o=1,maxEventsPerDay:l=3,weekdayLabels:c,formatMore:s=at,unavailableLabel:i="This view is not available yet.",dayStartHour:p,dayEndHour:y,allDayLabel:u="All day",now:f,continuesLabel:m="Continues",formatEndsLabel:b=rt,agendaEmptyLabel:k="No events",onEventDrop:x}=e;G.useEffect(()=>{K()},[]);let M=G.useMemo(()=>et(r),[r]),[g,h]=G.useState(()=>new Date);G.useEffect(()=>{if(f!==void 0||t!=="week"&&t!=="day")return;let R=setInterval(()=>h(new Date),6e4);return()=>clearInterval(R)},[f,t]);let A=G.useMemo(()=>f!==void 0?tt(f):g,[f,g]),D=Number.isInteger(o)&&o>=0&&o<=6?o:1,E=Number.isInteger(l)&&l>=0?l:3,v=c&&c.length===7?c:void 0,_=G.useMemo(()=>({...p!==void 0?{dayStartHour:p}:{},...y!==void 0?{dayEndHour:y}:{}}),[p,y]);if(t==="list")return Q(Ee,{events:a??[],locale:n,allDayLabel:u,continuesLabel:m,formatEndsLabel:b,emptyLabel:k});if(t==="month")return Q(ke,{events:a??[],anchor:M,locale:n,firstDayOfWeek:D,maxEventsPerDay:E,formatMore:s,...v?{weekdayLabels:v}:{},...x?{onEventDrop:x}:{}});if(t==="week"||t==="day"){let R=t==="week"?ee(M,D):[S(N(M))];return Q(ce,{view:t,days:R,events:a??[],locale:n,config:_,now:A,allDayLabel:u,...x?{onEventDrop:x}:{}})}return Q("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,children:i})}var nt=_e;export{_e as AetherCalendar,Se as CALENDAR_CSS,Ge as TIME_GRID_CSS,ce as TimeGridView,nt as default,K as ensureCalendarStyles,de as ensureTimeGridStyles};
