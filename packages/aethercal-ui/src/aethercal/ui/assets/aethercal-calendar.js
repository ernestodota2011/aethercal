function Z(e){return String(e).padStart(2,"0")}function u(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,n,a,r,i,o,c]=t,s=Number(n),l=Number(a),d=Number(r),h=Number(i??"0"),g=Number(o??"0"),D=Number(c??"0");if(l<1||l>12||d<1||d>31||h>23||g>59||D>59)throw new Error(`out-of-range ISO datetime: ${e}`);let b=new Date(s,l-1,d,h,g,D);if(b.getFullYear()!==s||b.getMonth()!==l-1||b.getDate()!==d)throw new Error(`nonexistent calendar date: ${e}`);return b}function k(e){return`${e.getFullYear()}-${Z(e.getMonth()+1)}-${Z(e.getDate())}T${Z(e.getHours())}:${Z(e.getMinutes())}:${Z(e.getSeconds())}`}function W(e){let t=u(e);return`${t.getFullYear()}-${Z(t.getMonth()+1)}-${Z(t.getDate())}`}function Et(e,t){return(e.getDay()-t+7)%7}function Ce(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate());return n.setDate(n.getDate()-Et(n,t)),n}function Je(e,t){return Array.from({length:t},(n,a)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+a);return`${r.getFullYear()}-${Z(r.getMonth()+1)}-${Z(r.getDate())}`})}function Se(e,t=1){return Je(Ce(e,t),7)}function Ie(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),1);return Je(Ce(n,t),42)}function xt(e,t){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()),a=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((a.getTime()-n.getTime())/864e5)}function se(e,t){let n=u(e.start),a=u(e.end),r=u(t),i=xt(n,r),o=new Date(n.getFullYear(),n.getMonth(),n.getDate()+i,n.getHours(),n.getMinutes(),n.getSeconds()),c=new Date(a.getFullYear(),a.getMonth(),a.getDate()+i,a.getHours(),a.getMinutes(),a.getSeconds()),s={id:e.id,start:k(o),end:k(c)};return e.revision!==void 0&&(s.revision=e.revision),s}var Rt=370;function Xe(e){return String(e).padStart(2,"0")}function ke(e){return`${e.getFullYear()}-${Xe(e.getMonth()+1)}-${Xe(e.getDate())}`}function je(e){return new Date(e.getFullYear(),e.getMonth(),e.getDate())}function Tt(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function Mt(e){let t=u(e.start),n=u(e.end),a=je(t),r;n.getTime()<=t.getTime()?r=a:(r=je(new Date(n.getTime()-1)),r.getTime()<a.getTime()&&(r=a));let i=[],o=a;for(let c=0;c<Rt&&o.getTime()<=r.getTime();c+=1)i.push(ke(o)),o=Tt(o,1);return{keys:i,startKey:ke(a),lastKey:ke(r)}}function Le(e){let t=new Map;return e.forEach((n,a)=>{let{keys:r,startKey:i,lastKey:o}=Mt(n),c=u(n.start).getTime(),s=u(n.end).getTime();for(let l of r){let d={entry:{event:n,isContinuation:l!==i,continuesAfter:l!==o},startMs:c,endMs:s,index:a},h=t.get(l);h?h.push(d):t.set(l,[d])}}),[...t.keys()].sort().map(n=>{let a=t.get(n);return a.sort((r,i)=>r.startMs-i.startMs||r.endMs-i.endMs||r.index-i.index),{date:n,entries:a.map(r=>r.entry)}})}var ue={status:"idle"};function ge(e){return e.status==="dragging"}function Pe(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return ue}}var pe={status:"idle"};function Ae(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"RESIZE_START":return{status:"resizing",eventId:t.eventId,edge:t.edge};case"SELECT_START":return{status:"selecting",anchor:t.point,current:t.point};case"SELECT_MOVE":return e.status!=="selecting"?e:{status:"selecting",anchor:e.anchor,current:t.point};case"COMMIT":case"CANCEL":return pe}}var Ze=60,Qe=6e4,de=15;function qe(e,t,n){return Math.min(n,Math.max(t,e))}function me(e,t){let n=u(`${e}T00:00:00`);return new Date(n.getFullYear(),n.getMonth(),n.getDate(),0,t,0)}function ae(e,t,n=de){let a=t.dayStartHour*Ze,r=t.dayEndHour*Ze,i=a+qe(e,0,1)*t.windowMinutes,o=n>0?n:de,c=Math.round(i/o)*o;return qe(c,a,r)}function Oe(e,t,n){if(n===null)return se(e,t);let a=u(e.start),i=u(e.end).getTime()-a.getTime(),o=me(t,n),c=new Date(o.getTime()+i),s={id:e.id,start:k(o),end:k(c)};return e.revision!==void 0&&(s.revision=e.revision),s}function Fe(e,t,n,a,r={}){let i=(r.minDurationMinutes??de)*Qe,o=u(e.start),c=u(e.end),s=me(n,a),l=o,d=c;if(t==="end"){let g=o.getTime()+i;d=new Date(Math.max(s.getTime(),g))}else{let g=c.getTime()-i;l=new Date(Math.min(s.getTime(),g))}let h={id:e.id,start:k(l),end:k(d)};return e.revision!==void 0&&(h.revision=e.revision),h}function fe(e,t,n={}){let a=n.minDurationMinutes??de;if(e.minuteOfDay===null||t.minuteOfDay===null){let[l,d]=e.dateOnly<=t.dateOnly?[e.dateOnly,t.dateOnly]:[t.dateOnly,e.dateOnly],h=u(`${l}T00:00:00`),g=u(`${d}T00:00:00`),D=new Date(g.getFullYear(),g.getMonth(),g.getDate()+1);return{start:k(h),end:k(D),allDay:!0}}let i=me(e.dateOnly,e.minuteOfDay??0),o=me(t.dateOnly,t.minuteOfDay??0),c=i.getTime()<=o.getTime()?i:o,s=i.getTime()<=o.getTime()?o:i;return s.getTime()===c.getTime()&&(s=new Date(c.getTime()+a*Qe)),{start:k(c),end:k(s),allDay:!1}}var Ne={overrides:{},appliedRevision:{}};function wt(e,t){let n={...e};return delete n[t],n}function _e(e,t){switch(t.type){case"SUBMIT":{let n=t.baseRevision??Number.NEGATIVE_INFINITY,a=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;return{overrides:{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"pending",start:t.start,end:t.end,...t.baseRevision!==void 0?{revision:t.baseRevision}:{}}},appliedRevision:{...e.appliedRevision,[t.id]:Math.max(a,n)}}}case"RESOLVE":{let n=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;if(t.revision<=n)return e;let a=e.overrides[t.id];return{overrides:a!==void 0&&a.clientMutationId===t.clientMutationId&&a.status==="pending"?{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"committed",start:t.start,end:t.end,revision:t.revision}}:e.overrides,appliedRevision:{...e.appliedRevision,[t.id]:t.revision}}}case"REJECT":case"TIMEOUT":{let n=e.overrides[t.id];return!n||n.clientMutationId!==t.clientMutationId||n.status!=="pending"?e:{...e,overrides:{...e.overrides,[t.id]:{...n,status:"rolledback"}}}}case"CLEAR":{let n=e.overrides[t.id];return!n||t.clientMutationId&&n.clientMutationId!==t.clientMutationId?e:{...e,overrides:wt(e.overrides,t.id)}}}}function Ge(e,t){let n=new Set,a=new Set;return{events:e.map(i=>{let o=t.overrides[i.id];return o?o.status==="pending"?(n.add(i.id),{...i,start:o.start,end:o.end}):o.status==="rolledback"?(a.add(i.id),i):i.revision!==void 0&&o.revision!==void 0&&i.revision>=o.revision?i:{...i,start:o.start,end:o.end,...o.revision!==void 0?{revision:o.revision}:{}}:i}),pendingIds:n,rolledBackIds:a}}function ze(e,t){let n=new Map(e.map(r=>[r.id,r])),a=[];for(let[r,i]of Object.entries(t.overrides)){if(i.status!=="committed")continue;let o=n.get(r);o&&o.revision!==void 0&&i.revision!==void 0&&o.revision>=i.revision&&a.push(r)}return a}var q=60,Ct=24*q,St=864e5;function ye(e,t,n){return Math.min(n,Math.max(t,e))}function He(e={}){let t=e.dayStartHour,n=e.dayEndHour,a=Number.isFinite(t)&&t!==void 0?ye(Math.trunc(t),0,23):0,r=Number.isFinite(n)&&n!==void 0?ye(Math.trunc(n),1,24):24,[i,o]=r>a?[a,r]:[0,24];return{dayStartHour:i,dayEndHour:o,windowMinutes:(o-i)*q}}function tt(e){let t=[],n=[];for(let a of e)a.allDay===!0?t.push(a):n.push(a);return{allDay:t,timed:n}}function et(e,t){let n=u(e),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()),r=Math.round((a.getTime()-t.getTime())/St),i=n.getHours()*q+n.getMinutes()+n.getSeconds()/60;return r*Ct+i}function It(e,t){let n=u(e.start).getTime(),a=u(e.end).getTime(),r=u(t.start).getTime(),i=u(t.end).getTime();return n<i&&r<a}function le(e,t,n){let a=u(`${t}T00:00:00`),r=n.dayStartHour*q,i=n.dayEndHour*q,o=[...e].sort((g,D)=>{let b=u(g.start).getTime(),m=u(D.start).getTime();return b!==m?b-m:u(D.end).getTime()-u(g.end).getTime()}),c=[],s=[],l=[],d=Number.NEGATIVE_INFINITY,h=()=>{let g=s.length;for(let D of l)c[D].laneCount=g;s=[],l=[],d=Number.NEGATIVE_INFINITY};for(let g of o){let D=et(g.start,a),b=et(g.end,a);if(b<=r||D>=i)continue;let m=u(g.start).getTime(),M=u(g.end).getTime();l.length>0&&m>=d&&h();let L=s.findIndex(_=>!It(_,g));L===-1?(L=s.length,s.push(g)):s[L]=g;let R=ye(D,r,i),S=ye(b,R,i),E=(R-r)/n.windowMinutes,F=(S-R)/n.windowMinutes;l.push(c.length),c.push({event:g,lane:L,laneCount:1,topFraction:E,heightFraction:F}),d=Math.max(d,M)}return h(),c}function kt(e){let t=[];for(let n=e.dayStartHour;n<e.dayEndHour;n+=1)t.push({hour:n,topFraction:(n-e.dayStartHour)*q/e.windowMinutes});return t}function $e(e,t,n={}){let a="windowMinutes"in n?n:He(n),{allDay:r,timed:i}=tt(t),o=i.map(s=>({event:s,startTs:u(s.start).getTime(),endTs:u(s.end).getTime()}));return{columns:e.map(s=>{let l=u(`${s}T00:00:00`),d=l.getTime(),h=new Date(l.getFullYear(),l.getMonth(),l.getDate()+1).getTime(),g=o.filter(b=>b.startTs>=h?!1:b.endTs>d?!0:b.startTs===b.endTs&&b.startTs>=d).map(b=>b.event),D=r.filter(b=>W(b.start)<=s&&s<=W(b.end));return{dateOnly:s,allDay:D,timed:le(g,s,a)}}),hourMarks:kt(a),config:a}}function Be(e,t={}){let n="windowMinutes"in t?t:He(t),a=e.getHours()*q+e.getMinutes()+e.getSeconds()/60,r=n.dayStartHour*q,i=n.dayEndHour*q;return a<r||a>=i?null:(a-r)/n.windowMinutes}import*as ee from"react";import*as ve from"react";var Ye=new Date(2023,0,1);function nt(e,t){let n=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(a,r)=>{let i=(t+r)%7,o=new Date(Ye.getFullYear(),Ye.getMonth(),Ye.getDate()+i);return n.format(o)})}function at(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function rt(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(u(e))}function re(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(u(e))}function it(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(u(e))}import{jsx as te,jsxs as st}from"react/jsx-runtime";function Lt(...e){return e.filter(Boolean).join(" ")}function Pt(e,t,n){let{event:a,isContinuation:r,continuesAfter:i}=e;return a.allDay===!0?n.allDayLabel:r?i?n.continuesLabel:n.formatEndsLabel(re(a.end,t)):re(a.start,t)}function At({entry:e,locale:t,labels:n}){let{event:a,isContinuation:r,continuesAfter:i}=e,o=Pt(e,t,n),c=a.color?{"--ac-event-accent":a.color}:void 0;return st("li",{className:Lt("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":a.id,"aria-label":`${o} ${a.title}`,style:c,...a.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...i?{"data-continues-after":""}:{},children:[te("span",{className:"aethercal-agenda-event-time",children:o}),te("span",{className:"aethercal-agenda-event-title",children:a.title})]})}function ot({events:e,locale:t,allDayLabel:n,continuesLabel:a,formatEndsLabel:r,emptyLabel:i}){let o=ve.useMemo(()=>Le(e),[e]),c=ve.useId(),s={allDayLabel:n,continuesLabel:a,formatEndsLabel:r};return o.length===0?te("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:te("p",{className:"aethercal-agenda-empty",children:i})}):te("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:o.map(l=>{let d=`${c}-${l.date}`;return st("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":d,"data-date":l.date,children:[te("div",{className:"aethercal-agenda-day-title",id:d,children:it(l.date,t)}),te("ul",{className:"aethercal-agenda-day-events",role:"list",children:l.entries.map((h,g)=>te(At,{entry:h,locale:t,labels:s},`${h.event.id}-${g}`))})]},l.date)})})}import*as Y from"react";import{jsx as dt,jsxs as Ft}from"react/jsx-runtime";function Ot(...e){return e.filter(Boolean).join(" ")}function he({event:e,timeLabel:t,onDragStart:n,onDragEnd:a,isPending:r,isRolledBack:i,onClick:o,onContextMenu:c}){let s=e.editable!==!1,l=e.color?{"--ac-event-accent":e.color}:void 0,d=t?`${t} ${e.title}`:e.title;return Ft("div",{className:Ot("aethercal-event",!s&&"is-locked",r&&"is-pending",i&&"is-rolledback"),draggable:s,"data-event-id":e.id,"aria-label":d,title:e.title,style:l,onDragStart:h=>{h.dataTransfer.setData("text/plain",e.id),h.dataTransfer.effectAllowed="move",n(e.id)},onDragEnd:a,onClick:o,onContextMenu:c?h=>{h.preventDefault(),h.stopPropagation(),c()}:void 0,children:[t?dt("time",{className:"aethercal-event-time",children:t}):null,dt("span",{className:"aethercal-event-title",children:e.title})]})}import{jsx as ie,jsxs as Ue}from"react/jsx-runtime";var lt=new Set;function ct(...e){return e.filter(Boolean).join(" ")}function Nt(e){let t=[];for(let n=0;n<e.length;n+=7)t.push(e.slice(n,n+7));return t}function _t(e){let t=new Map;for(let n of e){let a=W(n.start),r=t.get(a);r?r.push(n):t.set(a,[n])}return t}function ut(e){let{events:t,anchor:n,locale:a,firstDayOfWeek:r,weekdayLabels:i,maxEventsPerDay:o,formatMore:c,onEventDrop:s,onEventClick:l,onContextMenu:d,pendingIds:h=lt,rolledBackIds:g=lt}=e,D=Y.useMemo(()=>Ie(n,r),[n,r]),b=Y.useMemo(()=>Nt(D),[D]),m=Y.useMemo(()=>i??nt(a,r),[i,a,r]),M=Y.useMemo(()=>_t(t),[t]),L=n.getMonth(),R=W(k(new Date)),[S,E]=Y.useReducer(Pe,ue),[F,_]=Y.useState(()=>new Set),A=Y.useCallback(U=>{_(z=>{let w=new Set(z);return w.add(U),w})},[]),T=Y.useCallback(U=>z=>{if(z.preventDefault(),!ge(S)){E({type:"DROP"});return}let w=S.eventId,V=z.dataTransfer.getData("text/plain");if(E({type:"DROP"}),V&&V!==w||!s)return;let H=t.find(K=>K.id===w);!H||H.editable===!1||s(se(H,U))},[S,t,s]),G=!!s;return Ue("div",{className:ct("aethercal-calendar",ge(S)&&"is-dragging"),role:"grid","aria-label":at(n,a),"data-view":"month",children:[ie("div",{className:"aethercal-weekdays",role:"row",children:m.map((U,z)=>ie("div",{role:"columnheader",className:"aethercal-weekday",children:U},z))}),b.map((U,z)=>ie("div",{className:"aethercal-week",role:"row",children:U.map(w=>{let V=M.get(w)??[],H=F.has(w),K=H?V:V.slice(0,o),J=V.length-K.length,Re=new Date(`${w}T00:00:00`).getMonth()!==L;return Ue("div",{role:"gridcell",className:ct("aethercal-day",Re&&"is-outside",w===R&&"is-today"),"data-date":w,"aria-label":rt(w,a),onDragOver:G?P=>P.preventDefault():void 0,onDrop:G?T(w):void 0,onContextMenu:d?P=>{P.target===P.currentTarget&&(P.preventDefault(),d({start:`${w}T00:00:00`}))}:void 0,children:[ie("div",{className:"aethercal-day-head",children:ie("span",{className:"aethercal-day-number",children:Number(w.slice(-2))})}),Ue("div",{className:"aethercal-day-events",children:[K.map(P=>ie(he,{event:P,timeLabel:P.allDay?null:re(P.start,a),onDragStart:Me=>E({type:"DRAG_START",eventId:Me}),onDragEnd:()=>E({type:"DRAG_CANCEL"}),isPending:h.has(P.id),isRolledBack:g.has(P.id),...l?{onClick:()=>l({id:P.id})}:{},...d?{onContextMenu:()=>d({id:P.id})}:{}},P.id)),J>0&&!H?ie("button",{type:"button",className:"aethercal-more",onClick:()=>A(w),children:c(J)}):null]})]},w)})},z))]})}var gt="aethercal-calendar-styles",pt=`
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
`;function ce(){if(typeof document>"u"||document.getElementById(gt))return;let e=document.createElement("style");e.id=gt,e.textContent=pt,document.head.appendChild(e)}import*as C from"react";var mt="All day";function ft(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(u(e))}function yt(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function vt(e,t){if(e.length===0)return"";let n=u(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(n);let a=u(e[e.length-1]),r=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${r.format(n)} \u2013 ${r.format(a)}`}var ht="aethercal-timegrid-styles",bt=`
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
`;function Ve(){if(typeof document>"u"||document.getElementById(ht))return;let e=document.createElement("style");e.id=ht,e.textContent=bt,document.head.appendChild(e)}import{Fragment as Gt,jsx as O,jsxs as oe}from"react/jsx-runtime";function be(...e){return e.filter(Boolean).join(" ")}var Q=e=>`${e*100}%`,Dt=new Set;function De(e,t){let n=t.getBoundingClientRect();return n.height>0?(e-n.top)/n.height:0}function We(e){let{view:t,days:n,events:a,locale:r,config:i,now:o,allDayLabel:c=mt,onEventDrop:s,onEventResize:l,onRangeSelect:d,onEventClick:h,onContextMenu:g,pendingIds:D=Dt,rolledBackIds:b=Dt}=e;C.useEffect(()=>{ce(),Ve()},[]);let m=C.useMemo(()=>$e(n,a,i),[n,a,i]),M=C.useMemo(()=>Be(o,i),[o,i]),L=C.useMemo(()=>W(k(o)),[o]),[R,S]=C.useReducer(Ae,pe),E=C.useRef(null),[F,_]=C.useState(null),[A,T]=C.useState(null),G=!!s,U=!!l,z=!!d,w=R.status==="dragging",V=C.useCallback((f,v)=>p=>{if(p.preventDefault(),R.status!=="dragging"){S({type:"COMMIT"});return}let I=R.eventId,$=p.dataTransfer.getData("text/plain");if(S({type:"COMMIT"}),$&&$!==I||!s)return;let x=a.find(X=>X.id===I);if(!x||x.editable===!1)return;let y=null;if(v&&x.allDay!==!0){let B=p.currentTarget.getBoundingClientRect();B.height>0&&Number.isFinite(p.clientY)&&(y=ae((p.clientY-B.top)/B.height,m.config))}s(Oe(x,f,y))},[R,a,s,m.config]),H=C.useCallback(f=>{E.current?.kind!=="resize"&&S({type:"DRAG_START",eventId:f})},[]),K=C.useCallback(()=>S({type:"CANCEL"}),[]),J=C.useCallback((f,v)=>p=>{if(!l||f.editable===!1||p.button!==0)return;let I=p.currentTarget.closest(".aethercal-tg-col");I?.dataset.date&&(p.preventDefault(),p.stopPropagation(),E.current={kind:"resize",pointerId:p.pointerId,eventId:f.id,edge:v,dateOnly:I.dataset.date,colEl:I,payload:null},p.currentTarget.setPointerCapture?.(p.pointerId),S({type:"RESIZE_START",eventId:f.id,edge:v}))},[l]),Re=C.useCallback(f=>v=>{if(!d||v.button!==0||v.target!==v.currentTarget)return;let p=v.currentTarget,I=ae(De(v.clientY,p),m.config);E.current={kind:"select",pointerId:v.pointerId,anchorDate:f,anchorCol:p,anchorMinute:I,currentDate:f,currentCol:p,currentMinute:I},S({type:"SELECT_START",point:{dateOnly:f,minuteOfDay:I}})},[d,m.config]),Te=R.status==="resizing"||R.status==="selecting";C.useEffect(()=>{if(!Te)return;let f=x=>{let y=E.current;if(!(!y||x.pointerId!==y.pointerId))if(y.kind==="resize"){let X=ae(De(x.clientY,y.colEl),m.config),B=a.find(j=>j.id===y.eventId);if(!B)return;let ne=Fe(B,y.edge,y.dateOnly,X);y.payload=ne,_(ne)}else{let X=document.elementFromPoint(x.clientX,x.clientY)?.closest(".aethercal-tg-col"),B=X?.dataset.date?X:y.currentCol;y.currentCol=B,y.currentDate=B.dataset.date??y.anchorDate,y.currentMinute=ae(De(x.clientY,B),m.config);let ne=fe({dateOnly:y.anchorDate,minuteOfDay:y.anchorMinute},{dateOnly:y.currentDate,minuteOfDay:y.currentMinute}),we=(y.currentDate===y.anchorDate?le([{id:"__sel",title:"",start:ne.start,end:ne.end}],y.anchorDate,m.config):[])[0];T(we?{dateOnly:y.anchorDate,topFraction:we.topFraction,heightFraction:we.heightFraction}:null)}},v=x=>{let y=E.current;E.current=null,_(null),T(null),x&&y&&(y.kind==="resize"&&y.payload&&l&&l(y.payload),y.kind==="select"&&d&&(y.currentDate!==y.anchorDate||y.currentMinute!==y.anchorMinute)&&d(fe({dateOnly:y.anchorDate,minuteOfDay:y.anchorMinute},{dateOnly:y.currentDate,minuteOfDay:y.currentMinute}))),S({type:x?"COMMIT":"CANCEL"})},p=x=>{E.current&&x.pointerId!==E.current.pointerId||v(!0)},I=x=>{E.current&&x.pointerId!==E.current.pointerId||v(!1)},$=x=>{x.key==="Escape"&&v(!1)};return window.addEventListener("pointermove",f),window.addEventListener("pointerup",p),window.addEventListener("pointercancel",I),window.addEventListener("keydown",$),()=>{window.removeEventListener("pointermove",f),window.removeEventListener("pointerup",p),window.removeEventListener("pointercancel",I),window.removeEventListener("keydown",$)}},[Te,a,m.config,l,d]);let P=C.useCallback((f,v)=>p=>{if(!g||p.target!==p.currentTarget)return;if(p.preventDefault(),!v){g({start:`${f}T00:00:00`});return}let I=ae(De(p.clientY,p.currentTarget),m.config),$=u(`${f}T00:00:00`),x=new Date($.getFullYear(),$.getMonth(),$.getDate(),0,I,0);g({start:k(x)})},[g,m.config]),Me={"--ac-tg-cols":m.columns.length,"--ac-tg-hours":m.config.dayEndHour-m.config.dayStartHour};return oe("div",{className:be("aethercal-calendar","aethercal-timegrid",w&&"is-dragging",R.status==="resizing"&&"is-resizing",R.status==="selecting"&&"is-selecting"),role:"grid","aria-label":vt(n,r),"data-view":t,style:Me,children:[oe("div",{className:"aethercal-tg-head",role:"row",children:[O("div",{className:"aethercal-tg-corner"}),m.columns.map(f=>O("div",{role:"columnheader",className:be("aethercal-tg-colhead",f.dateOnly===L&&"is-today"),"data-date":f.dateOnly,children:O("span",{className:"aethercal-tg-colhead-date",children:ft(f.dateOnly,r)})},f.dateOnly))]}),oe("div",{className:"aethercal-tg-allday",role:"row",children:[O("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:c}),m.columns.map(f=>O("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":f.dateOnly,onDragOver:G?v=>v.preventDefault():void 0,onDrop:G?V(f.dateOnly,!1):void 0,onContextMenu:g?P(f.dateOnly,!1):void 0,children:f.allDay.map(v=>O(he,{event:v,timeLabel:null,onDragStart:H,onDragEnd:K,isPending:D.has(v.id),isRolledBack:b.has(v.id),...h?{onClick:()=>h({id:v.id})}:{},...g?{onContextMenu:()=>g({id:v.id})}:{}},v.id))},f.dateOnly))]}),oe("div",{className:"aethercal-tg-body",role:"row",children:[O("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:m.hourMarks.map(f=>O("div",{className:"aethercal-tg-hour",style:{top:Q(f.topFraction)},children:yt(f.hour,r)},f.hour))}),m.columns.map(f=>oe("div",{role:"gridcell",className:be("aethercal-tg-col",f.dateOnly===L&&"is-today"),"data-date":f.dateOnly,onDragOver:G?v=>v.preventDefault():void 0,onDrop:G?V(f.dateOnly,!0):void 0,onPointerDown:z?Re(f.dateOnly):void 0,onContextMenu:g?P(f.dateOnly,!0):void 0,children:[m.hourMarks.map(v=>O("div",{className:"aethercal-tg-line",style:{top:Q(v.topFraction)},"aria-hidden":"true"},v.hour)),A&&A.dateOnly===f.dateOnly?O("div",{className:"aethercal-tg-select-band",style:{top:Q(A.topFraction),height:Q(A.heightFraction)},"aria-hidden":"true"}):null,f.timed.map(v=>{let{event:p}=v,I=p.editable!==!1,$=re(p.start,r),x=F?.id===p.id?F:null,y=x?le([{...p,start:x.start,end:x.end}],f.dateOnly,m.config)[0]:void 0,X=y?y.topFraction:v.topFraction,B=y?y.heightFraction:v.heightFraction,ne={top:Q(X),height:Q(B),left:Q(v.lane/v.laneCount),width:Q(1/v.laneCount),...p.color?{"--ac-tg-event-accent":p.color}:{}};return oe("div",{className:be("aethercal-tg-event",!I&&"is-locked",D.has(p.id)&&"is-pending",b.has(p.id)&&"is-rolledback",!!x&&"is-resizing"),draggable:I,"data-event-id":p.id,"data-lane":v.lane,"data-lane-count":v.laneCount,"aria-label":`${$} ${p.title}`,title:p.title,style:ne,onDragStart:j=>{if(E.current?.kind==="resize"){j.preventDefault();return}j.dataTransfer.setData("text/plain",p.id),j.dataTransfer.effectAllowed="move",H(p.id)},onDragEnd:K,onClick:h?()=>h({id:p.id}):void 0,onContextMenu:g?j=>{j.preventDefault(),j.stopPropagation(),g({id:p.id})}:void 0,children:[O("time",{className:"aethercal-tg-event-time",children:$}),O("span",{className:"aethercal-tg-event-title",children:p.title}),U&&I?oe(Gt,{children:[O("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:J(p,"start")}),O("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:J(p,"end")})]}):null]},p.id)}),M!==null&&f.dateOnly===L?O("div",{className:"aethercal-now-indicator",style:{top:Q(M)},"aria-hidden":"true"}):null]},f.dateOnly))]})]})}import{jsx as Ee}from"react/jsx-runtime";function zt(e){return e instanceof Date?e:typeof e=="string"?u(e):new Date}function Ht(e){return e instanceof Date?e:typeof e=="string"?u(e):new Date}var $t=e=>`+${e} more`,Bt=e=>`ends ${e}`;function xe(e){let{view:t="month",events:n,anchor:a,locale:r="en",firstDayOfWeek:i=1,maxEventsPerDay:o=3,weekdayLabels:c,formatMore:s=$t,unavailableLabel:l="This view is not available yet.",dayStartHour:d,dayEndHour:h,allDayLabel:g="All day",now:D,continuesLabel:b="Continues",formatEndsLabel:m=Bt,agendaEmptyLabel:M="No events",onEventDrop:L,onEventResize:R,onRangeSelect:S,onEventClick:E,onContextMenu:F,pendingIds:_,rolledBackIds:A}=e;ee.useEffect(()=>{ce()},[]);let T=ee.useMemo(()=>zt(a),[a]),[G,U]=ee.useState(()=>new Date);ee.useEffect(()=>{if(D!==void 0||t!=="week"&&t!=="day")return;let J=setInterval(()=>U(new Date),6e4);return()=>clearInterval(J)},[D,t]);let z=ee.useMemo(()=>D!==void 0?Ht(D):G,[D,G]),w=Number.isInteger(i)&&i>=0&&i<=6?i:1,V=Number.isInteger(o)&&o>=0?o:3,H=c&&c.length===7?c:void 0,K=ee.useMemo(()=>({...d!==void 0?{dayStartHour:d}:{},...h!==void 0?{dayEndHour:h}:{}}),[d,h]);if(t==="list")return Ee(ot,{events:n??[],locale:r,allDayLabel:g,continuesLabel:b,formatEndsLabel:m,emptyLabel:M});if(t==="month")return Ee(ut,{events:n??[],anchor:T,locale:r,firstDayOfWeek:w,maxEventsPerDay:V,formatMore:s,...H?{weekdayLabels:H}:{},...L?{onEventDrop:L}:{},...E?{onEventClick:E}:{},...F?{onContextMenu:F}:{},..._?{pendingIds:_}:{},...A?{rolledBackIds:A}:{}});if(t==="week"||t==="day"){let J=t==="week"?Se(T,w):[W(k(T))];return Ee(We,{view:t,days:J,events:n??[],locale:r,config:K,now:z,allDayLabel:g,...L?{onEventDrop:L}:{},...R?{onEventResize:R}:{},...S?{onRangeSelect:S}:{},...E?{onEventClick:E}:{},...F?{onContextMenu:F}:{},..._?{pendingIds:_}:{},...A?{rolledBackIds:A}:{}})}return Ee("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,children:l})}var Yt=xe;import*as N from"react";function Ut(){return typeof crypto<"u"&&typeof crypto.randomUUID=="function"?crypto.randomUUID():`cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}var Vt=8e3,Wt=900;function Ke(e){let{events:t,mutate:n,timeoutMs:a=Vt,rollbackFlashMs:r=Wt,generateId:i=Ut}=e,[o,c]=N.useReducer(_e,Ne),s=N.useRef(o);s.current=o;let l=N.useRef(t);l.current=t;let d=N.useRef(!0),h=N.useRef(new Map);N.useEffect(()=>{d.current=!0;let b=h.current;return()=>{d.current=!1;for(let m of b.values())clearTimeout(m);b.clear()}},[]),N.useEffect(()=>{for(let b of ze(t,s.current)){let m=s.current.overrides[b];c({type:"CLEAR",id:b,...m?{clientMutationId:m.clientMutationId}:{}})}},[t]);let g=N.useCallback((b,m)=>{let M=i(),L=l.current.find(T=>T.id===m.id),R=h.current,S=T=>{let G=R.get(T);G!==void 0&&(clearTimeout(G),R.delete(T))},E=()=>{R.set(`fl:${M}`,setTimeout(()=>{R.delete(`fl:${M}`),d.current&&c({type:"CLEAR",id:m.id,clientMutationId:M})},r))};c({type:"SUBMIT",id:m.id,clientMutationId:M,start:m.start,end:m.end,...L?.revision!==void 0?{baseRevision:L.revision}:{}}),R.set(`to:${M}`,setTimeout(()=>{R.delete(`to:${M}`),d.current&&(c({type:"TIMEOUT",id:m.id,clientMutationId:M}),E())},a));let F=()=>{S(`to:${M}`),d.current&&(c({type:"REJECT",id:m.id,clientMutationId:M}),E())},_={kind:b,clientMutationId:M,payload:{...m,client_mutation_id:M}},A;try{A=n(_)}catch(T){A=Promise.reject(T instanceof Error?T:new Error(String(T)))}A.then(T=>{if(T.id!==m.id){F();return}S(`to:${M}`),d.current&&c({type:"RESOLVE",id:T.id,clientMutationId:M,start:T.start,end:T.end,revision:T.revision})}).catch(F)},[n,a,r,i]),D=N.useMemo(()=>Ge(t,o),[t,o]);return{events:D.events,pendingIds:D.pendingIds,rolledBackIds:D.rolledBackIds,submit:g}}import{jsx as Jt}from"react/jsx-runtime";function Kt({events:e,mutate:t,timeoutMs:n,rollbackFlashMs:a,generateId:r,...i}){let{events:o,pendingIds:c,rolledBackIds:s,submit:l}=Ke({events:e,mutate:t,...n!==void 0?{timeoutMs:n}:{},...a!==void 0?{rollbackFlashMs:a}:{},...r?{generateId:r}:{}});return Jt(xe,{...i,events:o,pendingIds:c,rolledBackIds:s,onEventDrop:d=>l("drop",d),onEventResize:d=>l("resize",d)})}export{xe as AetherCalendar,pt as CALENDAR_CSS,Kt as OptimisticCalendar,bt as TIME_GRID_CSS,We as TimeGridView,Yt as default,ce as ensureCalendarStyles,Ve as ensureTimeGridStyles,Ke as useOptimisticEvents};
