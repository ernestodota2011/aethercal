function U(e){return String(e).padStart(2,"0")}function u(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,n,a,r,i,o,p]=t,s=Number(n),d=Number(a),c=Number(r),v=Number(i??"0"),g=Number(o??"0"),E=Number(p??"0");if(d<1||d>12||c<1||c>31||v>23||g>59||E>59)throw new Error(`out-of-range ISO datetime: ${e}`);let l=new Date(s,d-1,c,v,g,E);if(l.getFullYear()!==s||l.getMonth()!==d-1||l.getDate()!==c)throw new Error(`nonexistent calendar date: ${e}`);return l}function I(e){return`${e.getFullYear()}-${U(e.getMonth()+1)}-${U(e.getDate())}T${U(e.getHours())}:${U(e.getMinutes())}:${U(e.getSeconds())}`}function ee(e){let t=u(e);return`${t.getFullYear()}-${U(t.getMonth()+1)}-${U(t.getDate())}`}function et(e){return`${e.getFullYear()}-${U(e.getMonth()+1)}-${U(e.getDate())}`}function de(e){let t=u(e.start),n=u(e.end),a=new Date(t.getFullYear(),t.getMonth(),t.getDate()),r=a;if(n.getTime()>t.getTime()){let i=new Date(n.getTime()-1),o=new Date(i.getFullYear(),i.getMonth(),i.getDate());o.getTime()>a.getTime()&&(r=o)}return{startKey:et(a),lastKey:et(r)}}function St(e,t){return(e.getDay()-t+7)%7}function Le(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate());return n.setDate(n.getDate()-St(n,t)),n}function tt(e,t){return Array.from({length:t},(n,a)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+a);return`${r.getFullYear()}-${U(r.getMonth()+1)}-${U(r.getDate())}`})}function Ae(e,t=1){return tt(Le(e,t),7)}function Pe(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),1);return tt(Le(n,t),42)}function Oe(e,t){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()),a=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((a.getTime()-n.getTime())/864e5)}function le(e,t){let n=u(e.start),a=u(e.end),r=u(t),i=Oe(n,r),o=new Date(n.getFullYear(),n.getMonth(),n.getDate()+i,n.getHours(),n.getMinutes(),n.getSeconds()),p=new Date(a.getFullYear(),a.getMonth(),a.getDate()+i,a.getHours(),a.getMinutes(),a.getSeconds()),s={id:e.id,start:I(o),end:I(p)};return e.revision!==void 0&&(s.revision=e.revision),s}var It=370;function nt(e){return String(e).padStart(2,"0")}function at(e){return`${e.getFullYear()}-${nt(e.getMonth()+1)}-${nt(e.getDate())}`}function kt(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function Lt(e){let{startKey:t,lastKey:n}=de(e),a=[],r=u(t);for(let i=0;i<It&&at(r)<=n;i+=1)a.push(at(r)),r=kt(r,1);return{keys:a,startKey:t,lastKey:n}}function Fe(e){let t=new Map;return e.forEach((n,a)=>{let{keys:r,startKey:i,lastKey:o}=Lt(n),p=u(n.start).getTime(),s=u(n.end).getTime();for(let d of r){let c={entry:{event:n,isContinuation:d!==i,continuesAfter:d!==o},startMs:p,endMs:s,index:a},v=t.get(d);v?v.push(c):t.set(d,[c])}}),[...t.keys()].sort().map(n=>{let a=t.get(n);return a.sort((r,i)=>r.startMs-i.startMs||r.endMs-i.endMs||r.index-i.index),{date:n,entries:a.map(r=>r.entry)}})}var fe={status:"idle"};function ye(e){return e.status==="dragging"}function Ne(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return fe}}var ve={status:"idle"};function _e(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"RESIZE_START":return{status:"resizing",eventId:t.eventId,edge:t.edge};case"SELECT_START":return{status:"selecting",anchor:t.point,current:t.point};case"SELECT_MOVE":return e.status!=="selecting"?e:{status:"selecting",anchor:e.anchor,current:t.point};case"COMMIT":case"CANCEL":return ve}}var he=60,ce=15;function rt(e,t,n){return Math.min(n,Math.max(t,e))}function be(e,t){let n=u(`${e}T00:00:00`);return new Date(n.getFullYear(),n.getMonth(),n.getDate(),0,t,0)}function Ge(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate(),e.getHours(),e.getMinutes()+t,e.getSeconds())}function ae(e,t,n=ce){let a=t.dayStartHour*he,r=t.dayEndHour*he,i=a+rt(e,0,1)*t.windowMinutes,o=n>0?n:ce,p=a+Math.round((i-a)/o)*o;return rt(p,a,r)}function ze(e,t,n){if(n===null)return le(e,t);let a=u(e.start),r=u(e.end),i=be(t,n),o=Oe(a,r),p=a.getHours()*he+a.getMinutes(),d=r.getHours()*he+r.getMinutes()-p,c=new Date(i.getFullYear(),i.getMonth(),i.getDate()+o,i.getHours(),i.getMinutes()+d,0),v={id:e.id,start:I(i),end:I(c)};return e.revision!==void 0&&(v.revision=e.revision),v}function He(e,t,n,a,r={}){let i=r.minDurationMinutes??ce,o=u(e.start),p=u(e.end),s=be(n,a),d=o,c=p;if(t==="end"){let g=Ge(o,i);c=s.getTime()>=g.getTime()?s:g}else{let g=Ge(p,-i);d=s.getTime()<=g.getTime()?s:g}let v={id:e.id,start:I(d),end:I(c)};return e.revision!==void 0&&(v.revision=e.revision),v}function Ee(e,t,n={}){let a=n.minDurationMinutes??ce;if(e.minuteOfDay===null||t.minuteOfDay===null){let[d,c]=e.dateOnly<=t.dateOnly?[e.dateOnly,t.dateOnly]:[t.dateOnly,e.dateOnly],v=u(`${d}T00:00:00`),g=u(`${c}T00:00:00`),E=new Date(g.getFullYear(),g.getMonth(),g.getDate()+1);return{start:I(v),end:I(E),allDay:!0}}let i=be(e.dateOnly,e.minuteOfDay??0),o=be(t.dateOnly,t.minuteOfDay??0),p=i.getTime()<=o.getTime()?i:o,s=i.getTime()<=o.getTime()?o:i;return s.getTime()===p.getTime()&&(s=Ge(p,a)),{start:I(p),end:I(s),allDay:!1}}var $e={overrides:{},appliedRevision:{}};function At(e,t){let n={...e};return delete n[t],n}function Be(e,t){switch(t.type){case"SUBMIT":{let n=t.baseRevision??Number.NEGATIVE_INFINITY,a=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;return{overrides:{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"pending",start:t.start,end:t.end,...t.baseRevision!==void 0?{revision:t.baseRevision}:{}}},appliedRevision:{...e.appliedRevision,[t.id]:Math.max(a,n)}}}case"RESOLVE":{let n=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;if(t.revision<=n)return e;let a=e.overrides[t.id];return{overrides:a!==void 0&&a.clientMutationId===t.clientMutationId&&a.status==="pending"?{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"committed",start:t.start,end:t.end,revision:t.revision}}:e.overrides,appliedRevision:{...e.appliedRevision,[t.id]:t.revision}}}case"REJECT":case"TIMEOUT":{let n=e.overrides[t.id];return!n||n.clientMutationId!==t.clientMutationId||n.status!=="pending"?e:{...e,overrides:{...e.overrides,[t.id]:{...n,status:"rolledback"}}}}case"CLEAR":{let n=e.overrides[t.id];return!n||t.clientMutationId&&n.clientMutationId!==t.clientMutationId?e:{...e,overrides:At(e.overrides,t.id)}}}}function Ye(e,t){let n=new Set,a=new Set;return{events:e.map(i=>{let o=t.overrides[i.id];return o?o.status==="pending"?(n.add(i.id),{...i,start:o.start,end:o.end}):o.status==="rolledback"?(a.add(i.id),i):i.revision!==void 0&&o.revision!==void 0&&i.revision>=o.revision?i:{...i,start:o.start,end:o.end,...o.revision!==void 0?{revision:o.revision}:{}}:i}),pendingIds:n,rolledBackIds:a}}function Ue(e,t){let n=new Map(e.map(r=>[r.id,r])),a=[];for(let[r,i]of Object.entries(t.overrides)){if(i.status!=="committed")continue;let o=n.get(r);o&&o.revision!==void 0&&i.revision!==void 0&&o.revision>=i.revision&&a.push(r)}return a}var Z=60,Pt=24*Z,Ot=864e5;function De(e,t,n){return Math.min(n,Math.max(t,e))}function Ve(e={}){let t=e.dayStartHour,n=e.dayEndHour,a=Number.isFinite(t)&&t!==void 0?De(Math.trunc(t),0,23):0,r=Number.isFinite(n)&&n!==void 0?De(Math.trunc(n),1,24):24,[i,o]=r>a?[a,r]:[0,24];return{dayStartHour:i,dayEndHour:o,windowMinutes:(o-i)*Z}}function ot(e){let t=[],n=[];for(let a of e)a.allDay===!0?t.push(a):n.push(a);return{allDay:t,timed:n}}function it(e,t){let n=u(e),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()),r=Math.round((a.getTime()-t.getTime())/Ot),i=n.getHours()*Z+n.getMinutes()+n.getSeconds()/60;return r*Pt+i}function Ft(e,t){let n=u(e.start).getTime(),a=u(e.end).getTime(),r=u(t.start).getTime(),i=u(t.end).getTime();return n<i&&r<a}function ue(e,t,n){let a=u(`${t}T00:00:00`),r=n.dayStartHour*Z,i=n.dayEndHour*Z,o=[...e].sort((g,E)=>{let l=u(g.start).getTime(),D=u(E.start).getTime();return l!==D?l-D:u(E.end).getTime()-u(g.end).getTime()}),p=[],s=[],d=[],c=Number.NEGATIVE_INFINITY,v=()=>{let g=s.length;for(let E of d)p[E].laneCount=g;s=[],d=[],c=Number.NEGATIVE_INFINITY};for(let g of o){let E=it(g.start,a),l=it(g.end,a);if(l<=r||E>=i)continue;let D=u(g.start).getTime(),N=u(g.end).getTime();d.length>0&&D>=c&&v();let b=s.findIndex(_=>!Ft(_,g));b===-1?(b=s.length,s.push(g)):s[b]=g;let F=De(E,r,i),L=De(l,F,i),M=(F-r)/n.windowMinutes,k=(L-F)/n.windowMinutes,{startKey:T,lastKey:R}=de(g);d.push(p.length),p.push({event:g,lane:b,laneCount:1,topFraction:M,heightFraction:k,isContinuation:t!==T,continuesAfter:t!==R}),c=Math.max(c,N)}return v(),p}function Nt(e){let t=[];for(let n=e.dayStartHour;n<e.dayEndHour;n+=1)t.push({hour:n,topFraction:(n-e.dayStartHour)*Z/e.windowMinutes});return t}function We(e,t,n={}){let a="windowMinutes"in n?n:Ve(n),{allDay:r,timed:i}=ot(t),o=i.map(s=>({event:s,startTs:u(s.start).getTime(),endTs:u(s.end).getTime()}));return{columns:e.map(s=>{let d=u(`${s}T00:00:00`),c=d.getTime(),v=new Date(d.getFullYear(),d.getMonth(),d.getDate()+1).getTime(),g=o.filter(l=>l.startTs>=v?!1:l.endTs>c?!0:l.startTs===l.endTs&&l.startTs>=c).map(l=>l.event),E=r.filter(l=>{let{startKey:D,lastKey:N}=de(l);return D<=s&&s<=N});return{dateOnly:s,allDay:E,timed:ue(g,s,a)}}),hourMarks:Nt(a),config:a}}function Ke(e,t={}){let n="windowMinutes"in t?t:Ve(t),a=e.getHours()*Z+e.getMinutes()+e.getSeconds()/60,r=n.dayStartHour*Z,i=n.dayEndHour*Z;return a<r||a>=i?null:(a-r)/n.windowMinutes}import*as Q from"react";import*as xe from"react";var Je=new Date(2023,0,1);function st(e,t){let n=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(a,r)=>{let i=(t+r)%7,o=new Date(Je.getFullYear(),Je.getMonth(),Je.getDate()+i);return n.format(o)})}function dt(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function lt(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(u(e))}function te(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(u(e))}function ct(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(u(e))}import{jsx as ne,jsxs as gt}from"react/jsx-runtime";function _t(...e){return e.filter(Boolean).join(" ")}function Gt(e,t,n){let{event:a,isContinuation:r,continuesAfter:i}=e;return a.allDay===!0?n.allDayLabel:r?i?n.continuesLabel:n.formatEndsLabel(te(a.end,t)):te(a.start,t)}function zt({entry:e,locale:t,labels:n}){let{event:a,isContinuation:r,continuesAfter:i}=e,o=Gt(e,t,n),p=a.color?{"--ac-event-accent":a.color}:void 0;return gt("li",{className:_t("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":a.id,"aria-label":`${o} ${a.title}`,style:p,...a.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...i?{"data-continues-after":""}:{},children:[ne("span",{className:"aethercal-agenda-event-time",children:o}),ne("span",{className:"aethercal-agenda-event-title",children:a.title})]})}function ut({events:e,locale:t,allDayLabel:n,continuesLabel:a,formatEndsLabel:r,emptyLabel:i}){let o=xe.useMemo(()=>Fe(e),[e]),p=xe.useId(),s={allDayLabel:n,continuesLabel:a,formatEndsLabel:r};return o.length===0?ne("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:ne("p",{className:"aethercal-agenda-empty",children:i})}):ne("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",children:o.map(d=>{let c=`${p}-${d.date}`;return gt("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":c,"data-date":d.date,children:[ne("div",{className:"aethercal-agenda-day-title",id:c,children:ct(d.date,t)}),ne("ul",{className:"aethercal-agenda-day-events",role:"list",children:d.entries.map((v,g)=>ne(zt,{entry:v,locale:t,labels:s},`${v.event.id}-${g}`))})]},d.date)})})}import*as B from"react";import{jsx as pt,jsxs as $t}from"react/jsx-runtime";function Ht(...e){return e.filter(Boolean).join(" ")}function Te({event:e,timeLabel:t,onDragStart:n,onDragEnd:a,isPending:r,isRolledBack:i,onClick:o,onContextMenu:p}){let s=e.editable!==!1,d=e.color?{"--ac-event-accent":e.color}:void 0,c=t?`${t} ${e.title}`:e.title;return $t("div",{className:Ht("aethercal-event",!s&&"is-locked",r&&"is-pending",i&&"is-rolledback"),draggable:s,"data-event-id":e.id,"aria-label":c,title:e.title,style:d,onDragStart:v=>{v.dataTransfer.setData("text/plain",e.id),v.dataTransfer.effectAllowed="move",n(e.id)},onDragEnd:a,onClick:o,onContextMenu:p?v=>{v.preventDefault(),v.stopPropagation(),p()}:void 0,children:[t?pt("time",{className:"aethercal-event-time",children:t}):null,pt("span",{className:"aethercal-event-title",children:e.title})]})}import{jsx as re,jsxs as Xe}from"react/jsx-runtime";var mt=new Set;function ft(...e){return e.filter(Boolean).join(" ")}function Bt(e){let t=[];for(let n=0;n<e.length;n+=7)t.push(e.slice(n,n+7));return t}function Yt(e){let t=new Map;for(let n of e){let a=ee(n.start),r=t.get(a);r?r.push(n):t.set(a,[n])}return t}function yt(e){let{events:t,anchor:n,locale:a,firstDayOfWeek:r,weekdayLabels:i,maxEventsPerDay:o,formatMore:p,onEventDrop:s,onEventClick:d,onContextMenu:c,pendingIds:v=mt,rolledBackIds:g=mt}=e,E=B.useMemo(()=>Pe(n,r),[n,r]),l=B.useMemo(()=>Bt(E),[E]),D=B.useMemo(()=>i??st(a,r),[i,a,r]),N=B.useMemo(()=>Yt(t),[t]),b=n.getMonth(),F=ee(I(new Date)),[L,M]=B.useReducer(Ne,fe),[k,T]=B.useState(()=>new Set),R=B.useCallback(z=>{T(A=>{let C=new Set(A);return C.add(z),C})},[]),_=B.useCallback(z=>A=>{if(A.preventDefault(),!ye(L)){M({type:"DROP"});return}let C=L.eventId,W=A.dataTransfer.getData("text/plain");if(M({type:"DROP"}),W&&W!==C||!s)return;let Y=t.find(X=>X.id===C);!Y||Y.editable===!1||s(le(Y,z))},[L,t,s]),V=!!s;return Xe("div",{className:ft("aethercal-calendar",ye(L)&&"is-dragging"),role:"grid","aria-label":dt(n,a),"data-view":"month",children:[re("div",{className:"aethercal-weekdays",role:"row",children:D.map((z,A)=>re("div",{role:"columnheader",className:"aethercal-weekday",children:z},A))}),l.map((z,A)=>re("div",{className:"aethercal-week",role:"row",children:z.map(C=>{let W=N.get(C)??[],Y=k.has(C),X=Y?W:W.slice(0,o),j=W.length-X.length,pe=new Date(`${C}T00:00:00`).getMonth()!==b;return Xe("div",{role:"gridcell",className:ft("aethercal-day",pe&&"is-outside",C===F&&"is-today"),"data-date":C,"aria-label":lt(C,a),onDragOver:V?P=>P.preventDefault():void 0,onDrop:V?_(C):void 0,onContextMenu:c?P=>{P.target.closest("[data-event-id], button")||(P.preventDefault(),c({start:`${C}T00:00:00`}))}:void 0,children:[re("div",{className:"aethercal-day-head",children:re("span",{className:"aethercal-day-number",children:Number(C.slice(-2))})}),Xe("div",{className:"aethercal-day-events",children:[X.map(P=>re(Te,{event:P,timeLabel:P.allDay?null:te(P.start,a),onDragStart:me=>M({type:"DRAG_START",eventId:me}),onDragEnd:()=>M({type:"DRAG_CANCEL"}),isPending:v.has(P.id),isRolledBack:g.has(P.id),...d?{onClick:()=>d({id:P.id})}:{},...c?{onContextMenu:()=>c({id:P.id})}:{}},P.id)),j>0&&!Y?re("button",{type:"button",className:"aethercal-more",onClick:()=>R(C),children:p(j)}):null]})]},C)})},A))]})}var vt="aethercal-calendar-styles",ht=`
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
`;function ge(){if(typeof document>"u"||document.getElementById(vt))return;let e=document.createElement("style");e.id=vt,e.textContent=ht,document.head.appendChild(e)}var bt="All day",Re="Continues",Me=e=>`ends ${e}`;function Et(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(u(e))}function Dt(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function xt(e,t){if(e.length===0)return"";let n=u(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(n);let a=u(e[e.length-1]),r=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${r.format(n)} \u2013 ${r.format(a)}`}import*as w from"react";var Tt="aethercal-timegrid-styles",Rt=`
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
`;function je(){if(typeof document>"u"||document.getElementById(Tt))return;let e=document.createElement("style");e.id=Tt,e.textContent=Rt,document.head.appendChild(e)}import{Fragment as Vt,jsx as O,jsxs as ie}from"react/jsx-runtime";function Ce(...e){return e.filter(Boolean).join(" ")}var q=e=>`${e*100}%`,Mt=new Set;function Ut(e,t,n,a){let{event:r,isContinuation:i,continuesAfter:o}=e;return i?o?n:a(te(r.end,t)):te(r.start,t)}function we(e,t){let n=t.getBoundingClientRect();return n.height>0?(e-n.top)/n.height:0}function Ze(e){let{view:t,days:n,events:a,locale:r,config:i,now:o,allDayLabel:p=bt,continuesLabel:s=Re,formatEndsLabel:d=Me,onEventDrop:c,onEventResize:v,onRangeSelect:g,onEventClick:E,onContextMenu:l,pendingIds:D=Mt,rolledBackIds:N=Mt}=e;w.useEffect(()=>{ge(),je()},[]);let b=w.useMemo(()=>We(n,a,i),[n,a,i]),F=w.useMemo(()=>Ke(o,i),[o,i]),L=w.useMemo(()=>ee(I(o)),[o]),[M,k]=w.useReducer(_e,ve),T=w.useRef(null),[R,_]=w.useState(null),[V,z]=w.useState(null),A=!!c,C=!!v,W=!!g,Y=M.status==="dragging",X=w.useCallback((f,h)=>m=>{if(m.preventDefault(),M.status!=="dragging"){k({type:"COMMIT"});return}let S=M.eventId,H=m.dataTransfer.getData("text/plain");if(k({type:"COMMIT"}),H&&H!==S||!c)return;let x=a.find(K=>K.id===S);if(!x||x.editable===!1)return;let y=null;if(h&&x.allDay!==!0){let $=m.currentTarget.getBoundingClientRect();$.height>0&&Number.isFinite(m.clientY)&&(y=ae((m.clientY-$.top)/$.height,b.config))}c(ze(x,f,y))},[M,a,c,b.config]),j=w.useCallback(f=>{T.current?.kind!=="resize"&&k({type:"DRAG_START",eventId:f})},[]),pe=w.useCallback(()=>k({type:"CANCEL"}),[]),ke=w.useCallback((f,h)=>m=>{if(!v||f.editable===!1||m.button!==0||T.current)return;let S=m.currentTarget.closest(".aethercal-tg-col");S?.dataset.date&&(m.preventDefault(),m.stopPropagation(),T.current={kind:"resize",pointerId:m.pointerId,eventId:f.id,edge:h,dateOnly:S.dataset.date,colEl:S,payload:null},m.currentTarget.setPointerCapture?.(m.pointerId),k({type:"RESIZE_START",eventId:f.id,edge:h}))},[v]),P=w.useCallback(f=>h=>{if(!g||h.button!==0||T.current||h.target.closest("[data-event-id], button"))return;let m=h.currentTarget,S=ae(we(h.clientY,m),b.config);T.current={kind:"select",pointerId:h.pointerId,anchorDate:f,anchorCol:m,anchorMinute:S,currentDate:f,currentCol:m,currentMinute:S},m.setPointerCapture?.(h.pointerId),k({type:"SELECT_START",point:{dateOnly:f,minuteOfDay:S}})},[g,b.config]),me=M.status==="resizing"||M.status==="selecting";w.useLayoutEffect(()=>{if(!me)return;let f=x=>{let y=T.current;if(!(!y||x.pointerId!==y.pointerId))if(y.kind==="resize"){let K=document.elementFromPoint(x.clientX,x.clientY)?.closest(".aethercal-tg-col"),$=K?.dataset.date?K:y.colEl,oe=ae(we(x.clientY,$),b.config),J=a.find(wt=>wt.id===y.eventId);if(!J)return;let se=He(J,y.edge,$.dataset.date??y.dateOnly,oe);y.payload=se,_(se)}else{let K=document.elementFromPoint(x.clientX,x.clientY)?.closest(".aethercal-tg-col"),$=K?.dataset.date?K:y.currentCol;y.currentCol=$,y.currentDate=$.dataset.date??y.anchorDate,y.currentMinute=ae(we(x.clientY,$),b.config);let oe=Ee({dateOnly:y.anchorDate,minuteOfDay:y.anchorMinute},{dateOnly:y.currentDate,minuteOfDay:y.currentMinute}),se=(y.currentDate===y.anchorDate?ue([{id:"__sel",title:"",start:oe.start,end:oe.end}],y.anchorDate,b.config):[])[0];z(se?{dateOnly:y.anchorDate,topFraction:se.topFraction,heightFraction:se.heightFraction}:null)}},h=x=>{let y=T.current;T.current=null,_(null),z(null),x&&y&&(y.kind==="resize"&&y.payload&&v&&v(y.payload),y.kind==="select"&&g&&(y.currentDate!==y.anchorDate||y.currentMinute!==y.anchorMinute)&&g(Ee({dateOnly:y.anchorDate,minuteOfDay:y.anchorMinute},{dateOnly:y.currentDate,minuteOfDay:y.currentMinute}))),k({type:x?"COMMIT":"CANCEL"})},m=x=>{T.current&&x.pointerId!==T.current.pointerId||h(!0)},S=x=>{T.current&&x.pointerId!==T.current.pointerId||h(!1)},H=x=>{x.key==="Escape"&&h(!1)};return window.addEventListener("pointermove",f),window.addEventListener("pointerup",m),window.addEventListener("pointercancel",S),window.addEventListener("keydown",H),()=>{window.removeEventListener("pointermove",f),window.removeEventListener("pointerup",m),window.removeEventListener("pointercancel",S),window.removeEventListener("keydown",H)}},[me,a,b.config,v,g]);let Qe=w.useCallback((f,h)=>m=>{if(!l||m.target.closest("[data-event-id], button"))return;if(m.preventDefault(),!h){l({start:`${f}T00:00:00`});return}let S=ae(we(m.clientY,m.currentTarget),b.config),H=u(`${f}T00:00:00`),x=new Date(H.getFullYear(),H.getMonth(),H.getDate(),0,S,0);l({start:I(x)})},[l,b.config]),Ct={"--ac-tg-cols":b.columns.length,"--ac-tg-hours":b.config.dayEndHour-b.config.dayStartHour};return ie("div",{className:Ce("aethercal-calendar","aethercal-timegrid",Y&&"is-dragging",M.status==="resizing"&&"is-resizing",M.status==="selecting"&&"is-selecting"),role:"grid","aria-label":xt(n,r),"data-view":t,style:Ct,children:[ie("div",{className:"aethercal-tg-head",role:"row",children:[O("div",{className:"aethercal-tg-corner"}),b.columns.map(f=>O("div",{role:"columnheader",className:Ce("aethercal-tg-colhead",f.dateOnly===L&&"is-today"),"data-date":f.dateOnly,children:O("span",{className:"aethercal-tg-colhead-date",children:Et(f.dateOnly,r)})},f.dateOnly))]}),ie("div",{className:"aethercal-tg-allday",role:"row",children:[O("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:p}),b.columns.map(f=>O("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":f.dateOnly,onDragOver:A?h=>h.preventDefault():void 0,onDrop:A?X(f.dateOnly,!1):void 0,onContextMenu:l?Qe(f.dateOnly,!1):void 0,children:f.allDay.map(h=>O(Te,{event:h,timeLabel:null,onDragStart:j,onDragEnd:pe,isPending:D.has(h.id),isRolledBack:N.has(h.id),...E?{onClick:()=>E({id:h.id})}:{},...l?{onContextMenu:()=>l({id:h.id})}:{}},h.id))},f.dateOnly))]}),ie("div",{className:"aethercal-tg-body",role:"row",children:[O("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:b.hourMarks.map(f=>O("div",{className:"aethercal-tg-hour",style:{top:q(f.topFraction)},children:Dt(f.hour,r)},f.hour))}),b.columns.map(f=>ie("div",{role:"gridcell",className:Ce("aethercal-tg-col",f.dateOnly===L&&"is-today"),"data-date":f.dateOnly,onDragOver:A?h=>h.preventDefault():void 0,onDrop:A?X(f.dateOnly,!0):void 0,onPointerDown:W?P(f.dateOnly):void 0,onContextMenu:l?Qe(f.dateOnly,!0):void 0,children:[b.hourMarks.map(h=>O("div",{className:"aethercal-tg-line",style:{top:q(h.topFraction)},"aria-hidden":"true"},h.hour)),V&&V.dateOnly===f.dateOnly?O("div",{className:"aethercal-tg-select-band",style:{top:q(V.topFraction),height:q(V.heightFraction)},"aria-hidden":"true"}):null,f.timed.map(h=>{let{event:m}=h,S=m.editable!==!1,H=Ut(h,r,s,d),x=R?.id===m.id?R:null,y=x?ue([{...m,start:x.start,end:x.end}],f.dateOnly,b.config)[0]:void 0,K=y?y.topFraction:h.topFraction,$=y?y.heightFraction:h.heightFraction,oe={top:q(K),height:q($),left:q(h.lane/h.laneCount),width:q(1/h.laneCount),...m.color?{"--ac-tg-event-accent":m.color}:{}};return ie("div",{className:Ce("aethercal-tg-event",!S&&"is-locked",D.has(m.id)&&"is-pending",N.has(m.id)&&"is-rolledback",!!x&&"is-resizing"),draggable:S,"data-event-id":m.id,"data-lane":h.lane,"data-lane-count":h.laneCount,"aria-label":`${H} ${m.title}`,title:m.title,style:oe,onDragStart:J=>{if(T.current?.kind==="resize"){J.preventDefault();return}J.dataTransfer.setData("text/plain",m.id),J.dataTransfer.effectAllowed="move",j(m.id)},onDragEnd:pe,onClick:E?()=>E({id:m.id}):void 0,onContextMenu:l?J=>{J.preventDefault(),J.stopPropagation(),l({id:m.id})}:void 0,children:[O("time",{className:"aethercal-tg-event-time",children:H}),O("span",{className:"aethercal-tg-event-title",children:m.title}),C&&S?ie(Vt,{children:[O("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:ke(m,"start")}),O("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:ke(m,"end")})]}):null]},m.id)}),F!==null&&f.dateOnly===L?O("div",{className:"aethercal-now-indicator",style:{top:q(F)},"aria-hidden":"true"}):null]},f.dateOnly))]})]})}import{jsx as Se}from"react/jsx-runtime";function Wt(e){return e instanceof Date?e:typeof e=="string"?u(e):new Date}function Kt(e){return e instanceof Date?e:typeof e=="string"?u(e):new Date}var Jt=e=>`+${e} more`;function Ie(e){let{view:t="month",events:n,anchor:a,locale:r="en",firstDayOfWeek:i=1,maxEventsPerDay:o=3,weekdayLabels:p,formatMore:s=Jt,unavailableLabel:d="This view is not available yet.",dayStartHour:c,dayEndHour:v,allDayLabel:g="All day",now:E,continuesLabel:l=Re,formatEndsLabel:D=Me,agendaEmptyLabel:N="No events",onEventDrop:b,onEventResize:F,onRangeSelect:L,onEventClick:M,onContextMenu:k,pendingIds:T,rolledBackIds:R}=e;Q.useEffect(()=>{ge()},[]);let _=Q.useMemo(()=>Wt(a),[a]),[V,z]=Q.useState(()=>new Date);Q.useEffect(()=>{if(E!==void 0||t!=="week"&&t!=="day")return;let j=setInterval(()=>z(new Date),6e4);return()=>clearInterval(j)},[E,t]);let A=Q.useMemo(()=>E!==void 0?Kt(E):V,[E,V]),C=Number.isInteger(i)&&i>=0&&i<=6?i:1,W=Number.isInteger(o)&&o>=0?o:3,Y=p&&p.length===7?p:void 0,X=Q.useMemo(()=>({...c!==void 0?{dayStartHour:c}:{},...v!==void 0?{dayEndHour:v}:{}}),[c,v]);if(t==="list")return Se(ut,{events:n??[],locale:r,allDayLabel:g,continuesLabel:l,formatEndsLabel:D,emptyLabel:N});if(t==="month")return Se(yt,{events:n??[],anchor:_,locale:r,firstDayOfWeek:C,maxEventsPerDay:W,formatMore:s,...Y?{weekdayLabels:Y}:{},...b?{onEventDrop:b}:{},...M?{onEventClick:M}:{},...k?{onContextMenu:k}:{},...T?{pendingIds:T}:{},...R?{rolledBackIds:R}:{}});if(t==="week"||t==="day"){let j=t==="week"?Ae(_,C):[ee(I(_))];return Se(Ze,{view:t,days:j,events:n??[],locale:r,config:X,now:A,allDayLabel:g,continuesLabel:l,formatEndsLabel:D,...b?{onEventDrop:b}:{},...F?{onEventResize:F}:{},...L?{onRangeSelect:L}:{},...M?{onEventClick:M}:{},...k?{onContextMenu:k}:{},...T?{pendingIds:T}:{},...R?{rolledBackIds:R}:{}})}return Se("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,children:d})}var Xt=Ie;import*as G from"react";function jt(){return typeof crypto<"u"&&typeof crypto.randomUUID=="function"?crypto.randomUUID():`cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}var Zt=8e3,qt=900;function qe(e){let{events:t,mutate:n,timeoutMs:a=Zt,rollbackFlashMs:r=qt,generateId:i=jt}=e,[o,p]=G.useReducer(Be,$e),s=G.useRef(t);s.current=t;let d=G.useRef(!0),c=G.useRef(new Map);G.useEffect(()=>{d.current=!0;let E=c.current;return()=>{d.current=!1;for(let l of E.values())clearTimeout(l);E.clear()}},[]),G.useEffect(()=>{for(let E of Ue(t,o)){let l=o.overrides[E];p({type:"CLEAR",id:E,...l?{clientMutationId:l.clientMutationId}:{}})}},[t,o]);let v=G.useCallback((E,l)=>{let D=i(),N=s.current.find(R=>R.id===l.id),b=c.current,F=R=>{let _=b.get(R);_!==void 0&&(clearTimeout(_),b.delete(R))},L=()=>{b.set(`fl:${D}`,setTimeout(()=>{b.delete(`fl:${D}`),d.current&&p({type:"CLEAR",id:l.id,clientMutationId:D})},r))};p({type:"SUBMIT",id:l.id,clientMutationId:D,start:l.start,end:l.end,...N?.revision!==void 0?{baseRevision:N.revision}:{}}),b.set(`to:${D}`,setTimeout(()=>{b.delete(`to:${D}`),d.current&&(p({type:"TIMEOUT",id:l.id,clientMutationId:D}),L())},a));let M=()=>{F(`to:${D}`),d.current&&(p({type:"REJECT",id:l.id,clientMutationId:D}),L())},k={kind:E,clientMutationId:D,payload:{...l,client_mutation_id:D}},T;try{T=n(k)}catch(R){T=Promise.reject(R instanceof Error?R:new Error(String(R)))}T.then(R=>{if(R.id!==l.id){M();return}F(`to:${D}`),d.current&&p({type:"RESOLVE",id:R.id,clientMutationId:D,start:R.start,end:R.end,revision:R.revision})}).catch(M)},[n,a,r,i]),g=G.useMemo(()=>Ye(t,o),[t,o]);return{events:g.events,pendingIds:g.pendingIds,rolledBackIds:g.rolledBackIds,submit:v}}import{jsx as en}from"react/jsx-runtime";function Qt({events:e,mutate:t,timeoutMs:n,rollbackFlashMs:a,generateId:r,...i}){let{events:o,pendingIds:p,rolledBackIds:s,submit:d}=qe({events:e,mutate:t,...n!==void 0?{timeoutMs:n}:{},...a!==void 0?{rollbackFlashMs:a}:{},...r?{generateId:r}:{}});return en(Ie,{...i,events:o,pendingIds:p,rolledBackIds:s,onEventDrop:c=>d("drop",c),onEventResize:c=>d("resize",c)})}export{Ie as AetherCalendar,ht as CALENDAR_CSS,Qt as OptimisticCalendar,Rt as TIME_GRID_CSS,Ze as TimeGridView,Xt as default,ge as ensureCalendarStyles,je as ensureTimeGridStyles,qe as useOptimisticEvents};
