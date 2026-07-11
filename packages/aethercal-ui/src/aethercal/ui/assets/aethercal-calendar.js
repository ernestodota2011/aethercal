function ae(e){return String(e).padStart(2,"0")}function y(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,n,a,r,i,s,g]=t,c=Number(n),l=Number(a),h=Number(r),b=Number(i??"0"),m=Number(s??"0"),k=Number(g??"0");if(l<1||l>12||h<1||h>31||b>23||m>59||k>59)throw new Error(`out-of-range ISO datetime: ${e}`);let E=new Date(c,l-1,h,b,m,k);if(E.getFullYear()!==c||E.getMonth()!==l-1||E.getDate()!==h)throw new Error(`nonexistent calendar date: ${e}`);return E}function F(e){return`${e.getFullYear()}-${ae(e.getMonth()+1)}-${ae(e.getDate())}T${ae(e.getHours())}:${ae(e.getMinutes())}:${ae(e.getSeconds())}`}function re(e){let t=y(e);return`${t.getFullYear()}-${ae(t.getMonth()+1)}-${ae(t.getDate())}`}function Yt(e){return`${e.getFullYear()}-${ae(e.getMonth()+1)}-${ae(e.getDate())}`}function ze(e){let t=y(e.start),n=y(e.end),a=new Date(t.getFullYear(),t.getMonth(),t.getDate()),r=a;if(n.getTime()>t.getTime()){let i=new Date(n.getTime()-1),s=new Date(i.getFullYear(),i.getMonth(),i.getDate());s.getTime()>a.getTime()&&(r=s)}return{startKey:Yt(a),lastKey:Yt(r)}}function Rn(e,t){return(e.getDay()-t+7)%7}function Ce(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate());return n.setDate(n.getDate()-Rn(n,t)),n}function Kt(e,t){return Array.from({length:t},(n,a)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+a);return`${r.getFullYear()}-${ae(r.getMonth()+1)}-${ae(r.getDate())}`})}function yt(e,t=1){return Kt(Ce(e,t),7)}function ht(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),1);return Kt(Ce(n,t),42)}function pe(e,t){let n=y(`${re(e)}T00:00:00`),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()+t);return`${a.getFullYear()}-${ae(a.getMonth()+1)}-${ae(a.getDate())}`}function bt(e,t){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()),a=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((a.getTime()-n.getTime())/864e5)}function Ne(e,t){let n=y(e.start),a=y(e.end),r=y(t),i=bt(n,r),s=new Date(n.getFullYear(),n.getMonth(),n.getDate()+i,n.getHours(),n.getMinutes(),n.getSeconds()),g=new Date(a.getFullYear(),a.getMonth(),a.getDate()+i,a.getHours(),a.getMinutes(),a.getSeconds()),c={id:e.id,start:F(s),end:F(g)};return e.revision!==void 0&&(c.revision=e.revision),c}var Cn=370;function Wt(e){return String(e).padStart(2,"0")}function Jt(e){return`${e.getFullYear()}-${Wt(e.getMonth()+1)}-${Wt(e.getDate())}`}function Mn(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function kn(e){let{startKey:t,lastKey:n}=ze(e),a=[],r=y(t);for(let i=0;i<Cn&&Jt(r)<=n;i+=1)a.push(Jt(r)),r=Mn(r,1);return{keys:a,startKey:t,lastKey:n}}function Dt(e){let t=new Map;return e.forEach((n,a)=>{let{keys:r,startKey:i,lastKey:s}=kn(n),g=y(n.start).getTime(),c=y(n.end).getTime();for(let l of r){let h={entry:{event:n,isContinuation:l!==i,continuesAfter:l!==s},startMs:g,endMs:c,index:a},b=t.get(l);b?b.push(h):t.set(l,[h])}}),[...t.keys()].sort().map(n=>{let a=t.get(n);return a.sort((r,i)=>r.startMs-i.startMs||r.endMs-i.endMs||r.index-i.index),{date:n,entries:a.map(r=>r.entry)}})}function Me(e,t,n,a){let r=n*a;if(r<=0)return e;let i=Math.min(Math.max(e,0),r-1),s=i-i%a,g=Math.min(s+a-1,r-1);switch(t){case"ArrowLeft":return i>s?i-1:i;case"ArrowRight":return i<g?i+1:i;case"ArrowUp":{let c=i-a;return c>=0?c:i}case"ArrowDown":{let c=i+a;return c<r?c:i}case"Home":return s;case"End":return g;default:return i}}var Sn=1;function He(e,t,n=Sn){let a=t.getFullYear(),r=t.getMonth(),i=t.getDate(),s,g;switch(e){case"week":{s=Ce(t,n),g=new Date(s.getFullYear(),s.getMonth(),s.getDate()+7);break}case"day":{s=new Date(a,r,i),g=new Date(a,r,i+1);break}default:{s=new Date(a,r,1),g=new Date(a,r+1,1);break}}return{view:e,from:F(s),to:F(g)}}function Ve(e,t,n){let a=e.getFullYear(),r=e.getMonth(),i=e.getDate();switch(t){case"week":return new Date(a,r,i+7*n);case"day":return new Date(a,r,i+n);default:return new Date(a,r+n,1)}}var qe={status:"idle"};function Qe(e){return e.status==="dragging"}function Et(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return qe}}var et={status:"idle"};function xt(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"RESIZE_START":return{status:"resizing",eventId:t.eventId,edge:t.edge};case"SELECT_START":return{status:"selecting",anchor:t.point,current:t.point};case"SELECT_MOVE":return e.status!=="selecting"?e:{status:"selecting",anchor:e.anchor,current:t.point};case"COMMIT":case"CANCEL":return et}}var ke=60,Se=15;function wt(e,t,n){return Math.min(n,Math.max(t,e))}function tt(e,t){let n=y(`${e}T00:00:00`);return new Date(n.getFullYear(),n.getMonth(),n.getDate(),0,t,0)}function Rt(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate(),e.getHours(),e.getMinutes()+t,e.getSeconds())}function Ie(e,t,n=Se){let a=t.dayStartHour*ke,r=t.dayEndHour*ke,i=a+wt(e,0,1)*t.windowMinutes,s=n>0?n:Se,g=a+Math.round((i-a)/s)*s;return wt(g,a,r)}function nt(e,t){return wt(e,t.dayStartHour*ke,t.dayEndHour*ke)}var Tt=24*ke;function Ct(e,t,n,a){let r=t+n,i=e;for(;r<0;)r+=Tt,i=pe(i,-1);for(;r>Tt;)r-=Tt,i=pe(i,1);return{dateOnly:i,minuteOfDay:nt(r,a)}}function at(e,t,n){if(n===null)return Ne(e,t);let a=y(e.start),r=y(e.end),i=tt(t,n),s=bt(a,r),g=a.getHours()*ke+a.getMinutes(),l=r.getHours()*ke+r.getMinutes()-g,h=new Date(i.getFullYear(),i.getMonth(),i.getDate()+s,i.getHours(),i.getMinutes()+l,0),b={id:e.id,start:F(i),end:F(h)};return e.revision!==void 0&&(b.revision=e.revision),b}function Be(e,t,n,a,r={}){let i=r.minDurationMinutes??Se,s=y(e.start),g=y(e.end),c=tt(n,a),l=s,h=g;if(t==="end"){let m=Rt(s,i);h=c.getTime()>=m.getTime()?c:m}else{let m=Rt(g,-i);l=c.getTime()<=m.getTime()?c:m}let b={id:e.id,start:F(l),end:F(h)};return e.revision!==void 0&&(b.revision=e.revision),b}function Ue(e,t,n={}){let a=n.minDurationMinutes??Se;if(e.minuteOfDay===null||t.minuteOfDay===null){let[l,h]=e.dateOnly<=t.dateOnly?[e.dateOnly,t.dateOnly]:[t.dateOnly,e.dateOnly],b=y(`${l}T00:00:00`),m=y(`${h}T00:00:00`),k=new Date(m.getFullYear(),m.getMonth(),m.getDate()+1);return{start:F(b),end:F(k),allDay:!0}}let i=tt(e.dateOnly,e.minuteOfDay??0),s=tt(t.dateOnly,t.minuteOfDay??0),g=i.getTime()<=s.getTime()?i:s,c=i.getTime()<=s.getTime()?s:i;return c.getTime()===g.getTime()&&(c=Rt(g,a)),{start:F(g),end:F(c),allDay:!1}}var Mt={overrides:{},appliedRevision:{}};function In(e,t){let n={...e};return delete n[t],n}function kt(e,t){switch(t.type){case"SUBMIT":{let n=t.baseRevision??Number.NEGATIVE_INFINITY,a=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;return{overrides:{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"pending",start:t.start,end:t.end,...t.baseRevision!==void 0?{revision:t.baseRevision}:{}}},appliedRevision:{...e.appliedRevision,[t.id]:Math.max(a,n)}}}case"RESOLVE":{let n=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;if(t.revision<=n)return e;let a=e.overrides[t.id];return{overrides:a!==void 0&&a.clientMutationId===t.clientMutationId&&a.status==="pending"?{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"committed",start:t.start,end:t.end,revision:t.revision}}:e.overrides,appliedRevision:{...e.appliedRevision,[t.id]:t.revision}}}case"REJECT":case"TIMEOUT":{let n=e.overrides[t.id];return!n||n.clientMutationId!==t.clientMutationId||n.status!=="pending"?e:{...e,overrides:{...e.overrides,[t.id]:{...n,status:"rolledback"}}}}case"CLEAR":{let n=e.overrides[t.id];return!n||t.clientMutationId&&n.clientMutationId!==t.clientMutationId?e:{...e,overrides:In(e.overrides,t.id)}}}}function St(e,t){let n=new Set,a=new Set;return{events:e.map(i=>{let s=t.overrides[i.id];return s?s.status==="pending"?(n.add(i.id),{...i,start:s.start,end:s.end}):s.status==="rolledback"?(a.add(i.id),i):i.revision!==void 0&&s.revision!==void 0&&i.revision>=s.revision?i:{...i,start:s.start,end:s.end,...s.revision!==void 0?{revision:s.revision}:{}}:i}),pendingIds:n,rolledBackIds:a}}function It(e,t){let n=new Map(e.map(r=>[r.id,r])),a=[];for(let[r,i]of Object.entries(t.overrides)){if(i.status!=="committed")continue;let s=n.get(r);s&&s.revision!==void 0&&i.revision!==void 0&&s.revision>=i.revision&&a.push(r)}return a}var he=60,An=24*he,Pn=864e5;function rt(e,t,n){return Math.min(n,Math.max(t,e))}function At(e={}){let t=e.dayStartHour,n=e.dayEndHour,a=Number.isFinite(t)&&t!==void 0?rt(Math.trunc(t),0,23):0,r=Number.isFinite(n)&&n!==void 0?rt(Math.trunc(n),1,24):24,[i,s]=r>a?[a,r]:[0,24];return{dayStartHour:i,dayEndHour:s,windowMinutes:(s-i)*he}}function jt(e){let t=[],n=[];for(let a of e)a.allDay===!0?t.push(a):n.push(a);return{allDay:t,timed:n}}function Xt(e,t){let n=y(e),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()),r=Math.round((a.getTime()-t.getTime())/Pn),i=n.getHours()*he+n.getMinutes()+n.getSeconds()/60;return r*An+i}function On(e,t){let n=y(e.start).getTime(),a=y(e.end).getTime(),r=y(t.start).getTime(),i=y(t.end).getTime();return n<i&&r<a}function Ye(e,t,n){let a=y(`${t}T00:00:00`),r=n.dayStartHour*he,i=n.dayEndHour*he,s=[...e].sort((m,k)=>{let E=y(m.start).getTime(),u=y(k.start).getTime();return E!==u?E-u:y(k.end).getTime()-y(m.end).getTime()}),g=[],c=[],l=[],h=Number.NEGATIVE_INFINITY,b=()=>{let m=c.length;for(let k of l)g[k].laneCount=m;c=[],l=[],h=Number.NEGATIVE_INFINITY};for(let m of s){let k=Xt(m.start,a),E=Xt(m.end,a);if(E<=r||k>=i)continue;let u=y(m.start).getTime(),T=y(m.end).getTime();l.length>0&&u>=h&&b();let z=c.findIndex(te=>!On(te,m));z===-1?(z=c.length,c.push(m)):c[z]=m;let G=rt(k,r,i),H=rt(E,G,i),X=(G-r)/n.windowMinutes,P=(H-G)/n.windowMinutes,{startKey:W,lastKey:I}=ze(m);l.push(g.length),g.push({event:m,lane:z,laneCount:1,topFraction:X,heightFraction:P,isContinuation:t!==W,continuesAfter:t!==I}),h=Math.max(h,T)}return b(),g}function Ln(e){let t=[];for(let n=e.dayStartHour;n<e.dayEndHour;n+=1)t.push({hour:n,topFraction:(n-e.dayStartHour)*he/e.windowMinutes});return t}function Pt(e,t,n={}){let a="windowMinutes"in n?n:At(n),{allDay:r,timed:i}=jt(t),s=i.map(c=>({event:c,startTs:y(c.start).getTime(),endTs:y(c.end).getTime()}));return{columns:e.map(c=>{let l=y(`${c}T00:00:00`),h=l.getTime(),b=new Date(l.getFullYear(),l.getMonth(),l.getDate()+1).getTime(),m=s.filter(E=>E.startTs>=b?!1:E.endTs>h?!0:E.startTs===E.endTs&&E.startTs>=h).map(E=>E.event),k=r.filter(E=>{let{startKey:u,lastKey:T}=ze(E);return u<=c&&c<=T});return{dateOnly:c,allDay:k,timed:Ye(m,c,a)}}),hourMarks:Ln(a),config:a}}function Ot(e,t={}){let n="windowMinutes"in t?t:At(t),a=e.getHours()*he+e.getMinutes()+e.getSeconds()/60,r=n.dayStartHour*he,i=n.dayEndHour*he;return a<r||a>=i?null:(a-r)/n.windowMinutes}import*as ge from"react";import*as it from"react";var Lt=new Date(2023,0,1);function Zt(e,t){let n=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(a,r)=>{let i=(t+r)%7,s=new Date(Lt.getFullYear(),Lt.getMonth(),Lt.getDate()+i);return n.format(s)})}function Nt(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function qt(e,t,n,a){if(e==="day")return new Intl.DateTimeFormat(n,{dateStyle:"full"}).format(t);if(e==="week"){let r=Ce(t,a),i=new Date(r.getFullYear(),r.getMonth(),r.getDate()+6),s=new Intl.DateTimeFormat(n,{month:"short",day:"numeric"}).format(r),g=new Intl.DateTimeFormat(n,{month:"short",day:"numeric",year:"numeric"}).format(i);return`${s} \u2013 ${g}`}return Nt(t,n)}function Ke(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(y(e))}function oe(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(y(e))}function Qt(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(y(e))}import{jsx as xe,jsxs as tn}from"react/jsx-runtime";function Nn(...e){return e.filter(Boolean).join(" ")}function Fn(e,t,n){let{event:a,isContinuation:r,continuesAfter:i}=e;return a.allDay===!0?n.allDay:r?i?n.continues:n.endsAt(oe(a.end,t)):oe(a.start,t)}function Gn({entry:e,locale:t,messages:n}){let{event:a,isContinuation:r,continuesAfter:i}=e,s=Fn(e,t,n),g=a.color?{"--ac-event-accent":a.color}:void 0;return tn("li",{className:Nn("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":a.id,"aria-label":`${s} ${a.title}`,style:g,...a.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...i?{"data-continues-after":""}:{},children:[xe("span",{className:"aethercal-agenda-event-time",children:s}),xe("span",{className:"aethercal-agenda-event-title",children:a.title})]})}function en({events:e,locale:t,messages:n,themeVars:a}){let r=it.useMemo(()=>Dt(e),[e]),i=it.useId();return r.length===0?xe("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:xe("p",{className:"aethercal-agenda-empty",children:n.noEvents})}):xe("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:r.map(s=>{let g=`${i}-${s.date}`;return tn("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":g,"data-date":s.date,children:[xe("div",{className:"aethercal-agenda-day-title",id:g,children:Qt(s.date,t)}),xe("ul",{className:"aethercal-agenda-day-events",role:"list",children:s.entries.map((c,l)=>xe(Gn,{entry:c,locale:t,messages:n},`${c.event.id}-${l}`))})]},s.date)})})}import{jsx as Te,jsxs as nn}from"react/jsx-runtime";var _n=["month","week","day","list"];function Ft({view:e,anchor:t,now:n,locale:a,firstDayOfWeek:r,messages:i,showViews:s=!0,onRangeChange:g,onViewChange:c}){let l=b=>{g?.(He(e,b,r))},h=qt(e,t,a,r);return nn("div",{className:"aethercal-nav",role:"toolbar","aria-label":i.navToolbar,children:[nn("div",{className:"aethercal-nav-group",children:[Te("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":i.navPrevious,onClick:()=>l(Ve(t,e,-1)),children:Te("span",{"aria-hidden":"true",children:"\u2039"})}),Te("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-today",onClick:()=>l(n),children:i.navToday}),Te("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":i.navNext,onClick:()=>l(Ve(t,e,1)),children:Te("span",{"aria-hidden":"true",children:"\u203A"})})]}),Te("span",{className:"aethercal-nav-title","aria-live":"polite",children:h}),s?Te("div",{className:"aethercal-nav-views",children:_n.map(b=>Te("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-view","aria-pressed":b===e,onClick:()=>c?.(He(b,t,r)),children:i.viewNames[b]},b))}):null]})}var $n={allDay:"All day",continues:"Continues",endsAt:e=>`ends ${e}`,more:e=>`+${e} more`,noEvents:"No events",unavailable:"This view is not available yet.",keyboardHint:"Use the arrow keys to move between days. Press Enter on an event to grab it, the arrow keys to move or resize it, Enter to drop, and Escape to cancel.",grabbedMoveHint:e=>`Grabbed ${e}. Use the arrow keys to move it, Enter to drop, Escape to cancel.`,grabbedResizeHint:e=>`Resizing ${e}. Use the up and down arrow keys to change its duration, Enter to confirm, Escape to cancel.`,movedTo:e=>`Moved to ${e}`,resizedTo:e=>`Duration ${e}`,dropped:e=>`Dropped on ${e}`,resized:e=>`Duration set to ${e}`,createHere:e=>`Create an event on ${e}`,cancelled:"Cancelled",navToolbar:"Calendar navigation",navPrevious:"Previous",navNext:"Next",navToday:"Today",viewNames:{month:"Month",week:"Week",day:"Day",list:"Agenda"}},zn={allDay:"Todo el d\xEDa",continues:"Contin\xFAa",endsAt:e=>`termina ${e}`,more:e=>`+${e} m\xE1s`,noEvents:"Sin eventos",unavailable:"Esta vista a\xFAn no est\xE1 disponible.",keyboardHint:"Usa las flechas para moverte entre los d\xEDas. Pulsa Enter sobre un evento para agarrarlo, las flechas para moverlo o cambiar su duraci\xF3n, Enter para soltarlo y Escape para cancelar.",grabbedMoveHint:e=>`Agarraste el evento ${e}. Usa las flechas para moverlo, Enter para soltarlo y Escape para cancelar.`,grabbedResizeHint:e=>`Est\xE1s cambiando la duraci\xF3n de ${e}. Usa las flechas hacia arriba y abajo para ajustarla, Enter para confirmar y Escape para cancelar.`,movedTo:e=>`Movido a ${e}`,resizedTo:e=>`Duraci\xF3n ${e}`,dropped:e=>`Soltado en ${e}`,resized:e=>`Duraci\xF3n establecida en ${e}`,createHere:e=>`Crear un evento en ${e}`,cancelled:"Cancelado",navToolbar:"Navegaci\xF3n del calendario",navPrevious:"Anterior",navNext:"Siguiente",navToday:"Hoy",viewNames:{month:"Mes",week:"Semana",day:"D\xEDa",list:"Agenda"}},Gt={en:$n,es:zn};function Hn(e){return e.toLowerCase().split("-")[0]??""}function We(e,t,n=Gt){let a=e.toLowerCase(),r=n[a]??n[Hn(e)]??n.en??Gt.en;return t?{...r,...t}:r}import*as L from"react";import{jsx as an}from"react/jsx-runtime";function ot({message:e}){return an("div",{className:"aethercal-sr-only","aria-live":"polite","aria-atomic":"true",children:e})}function st({id:e,text:t}){return an("div",{id:e,className:"aethercal-sr-only",children:t})}import{jsx as rn,jsxs as Bn}from"react/jsx-runtime";function Vn(...e){return e.filter(Boolean).join(" ")}function dt({event:e,timeLabel:t,onDragStart:n,onDragEnd:a,isPending:r,isRolledBack:i,onClick:s,onContextMenu:g,id:c,interactive:l,isActive:h,isGrabbed:b}){let m=e.editable!==!1,k=e.color?{"--ac-event-accent":e.color}:void 0,E=t?`${t} ${e.title}`:e.title;return Bn("div",{className:Vn("aethercal-event",!m&&"is-locked",r&&"is-pending",i&&"is-rolledback",h&&"is-active",b&&"is-grabbed"),...c?{id:c}:{},...l?{role:"button"}:{},draggable:m,"data-event-id":e.id,"aria-label":E,title:e.title,style:k,onDragStart:u=>{u.dataTransfer.setData("text/plain",e.id),u.dataTransfer.effectAllowed="move",n(e.id)},onDragEnd:a,onClick:s,onContextMenu:g?u=>{u.preventDefault(),u.stopPropagation(),g()}:void 0,children:[t?rn("time",{className:"aethercal-event-time",children:t}):null,rn("span",{className:"aethercal-event-title",children:e.title})]})}import{Fragment as Wn,jsx as be,jsxs as lt}from"react/jsx-runtime";var on=new Set,Fe=7,sn=6;function dn(...e){return e.filter(Boolean).join(" ")}function Un(e){let t=[];for(let n=0;n<e.length;n+=Fe)t.push(e.slice(n,n+Fe));return t}function Yn(e){let t=new Map;for(let n of e){let a=re(n.start),r=t.get(a);r?r.push(n):t.set(a,[n])}return t}function Kn(e){return{start:`${e}T00:00:00`,end:`${pe(e,1)}T00:00:00`,allDay:!0}}function ln(e){let{events:t,anchor:n,locale:a,firstDayOfWeek:r,messages:i,weekdayLabels:s,maxEventsPerDay:g,themeVars:c,onEventDrop:l,onRangeSelect:h,onEventClick:b,onContextMenu:m,pendingIds:k=on,rolledBackIds:E=on}=e,u=L.useMemo(()=>ht(n,r),[n,r]),T=L.useMemo(()=>Un(u),[u]),z=L.useMemo(()=>s??Zt(a,r),[s,a,r]),G=L.useMemo(()=>Yn(t),[t]),H=n.getMonth(),X=re(F(new Date)),P=L.useMemo(()=>re(F(n)),[n]),[W,I]=L.useReducer(Et,qe),[te,Ae]=L.useState(()=>new Set),se=L.useId(),[_,de]=L.useState(P),[J,U]=L.useState(null),[M,q]=L.useState(null),[Re,fe]=L.useState("");L.useEffect(()=>{u.includes(_)||(de(P),U(null),q(null))},[u,_,P]);let le=L.useCallback(R=>!!b||R.editable!==!1&&!!l,[b,l]);L.useEffect(()=>{let R=new Set((G.get(_)??[]).filter(x=>le(x)).map(x=>x.id));M&&!R.has(M.eventId)?(q(null),U(null)):!M&&J!==null&&!R.has(J)&&U(null)},[G,_,J,M,le]);let ve=R=>`${se}-c-${R}`,ye=(R,x)=>`${se}-e-${R}-${x}`,V=`${se}-hint`,A=M?ye(_,M.eventId):J?ye(_,J):ve(_),ce=L.useCallback(R=>{Ae(x=>{let S=new Set(x);return S.add(R),S})},[]),$=L.useCallback(R=>x=>{if(x.preventDefault(),!Qe(W)){I({type:"DROP"});return}let S=W.eventId,Q=x.dataTransfer.getData("text/plain");if(I({type:"DROP"}),Q&&Q!==S||!l)return;let Y=t.find(K=>K.id===S);!Y||Y.editable===!1||l(Ne(Y,R))},[W,t,l]),j=!!l,N=L.useCallback(R=>{if(!M)return;let x=pe(M.targetDate,R),S=u[0],Q=u[u.length-1];x<S||x>Q||(fe(i.movedTo(Ke(x,a))),q({...M,targetDate:x,moved:!0}))},[M,u,a,i]),ue=L.useCallback(()=>{if(!M)return;if(!M.moved){U(M.eventId),q(null);return}let R=t.find(x=>x.id===M.eventId);R&&R.editable!==!1&&l&&(l(Ne(R,M.targetDate)),fe(i.dropped(Ke(M.targetDate,a)))),de(M.targetDate),U(null),q(null)},[M,t,l,i,a]),Pe={ArrowLeft:-1,ArrowRight:1,ArrowUp:-Fe,ArrowDown:Fe},me=L.useCallback(R=>{let{key:x}=R,S=x==="Enter"||x===" "||x==="Spacebar";if(M){if(x in Pe){R.preventDefault(),N(Pe[x]);return}if(S){R.preventDefault(),ue();return}if(x==="Escape"){R.preventDefault(),q(null),fe(i.cancelled);return}return}let Q=G.get(_)??[],Y=Q.filter(K=>le(K));if(J){let K=Y.findIndex(O=>O.id===J);if(x==="ArrowDown"){R.preventDefault(),K>=0&&K<Y.length-1&&U(Y[K+1].id);return}if(x==="ArrowUp"){R.preventDefault(),K>0?U(Y[K-1].id):U(null);return}if(S){R.preventDefault();let O=Y.find(_e=>_e.id===J);if(!O)return;O.editable!==!1&&l?(q({eventId:O.id,targetDate:_,moved:!1}),fe(i.grabbedMoveHint(O.title))):b&&b({id:O.id});return}if(x==="Escape"){R.preventDefault(),U(null);return}if(x==="ArrowLeft"||x==="ArrowRight"||x==="Home"||x==="End"){R.preventDefault(),U(null);let O=Me(u.indexOf(_),x,sn,Fe);de(u[O]);return}return}if(x in Pe||x==="Home"||x==="End"){R.preventDefault();let K=Me(u.indexOf(_),x,sn,Fe);de(u[K]);return}S&&(Y.length>0?(R.preventDefault(),ce(_),U(Y[0].id)):Q.length===0&&h&&(R.preventDefault(),h(Kn(_)),fe(i.createHere(Ke(_,a)))))},[M,J,_,u,G,le,l,b,h,N,ue,ce,i,a,Pe]);return lt(Wn,{children:[lt("div",{className:dn("aethercal-calendar",Qe(W)&&"is-dragging"),role:"grid","aria-label":Nt(n,a),"aria-describedby":V,"aria-activedescendant":A,tabIndex:0,"data-view":"month",style:c,onKeyDown:me,children:[be("div",{className:"aethercal-weekdays",role:"row",children:z.map((R,x)=>be("div",{role:"columnheader",className:"aethercal-weekday",children:R},x))}),T.map((R,x)=>be("div",{className:"aethercal-week",role:"row",children:R.map(S=>{let Q=G.get(S)??[],Y=te.has(S),K=Y?Q:Q.slice(0,g),O=Q.length-K.length,_e=new Date(`${S}T00:00:00`).getMonth()!==H,je=S===X,Ze=!J&&!M&&S===_,pt=M?.targetDate===S;return lt("div",{id:ve(S),role:"gridcell",className:dn("aethercal-day",_e&&"is-outside",je&&"is-today",Ze&&"is-active",pt&&"is-drop-target"),"data-date":S,"aria-label":Ke(S,a),onDragOver:j?B=>B.preventDefault():void 0,onDrop:j?$(S):void 0,onContextMenu:m?B=>{B.target.closest("[data-event-id], button")||(B.preventDefault(),m({start:`${S}T00:00:00`}))}:void 0,children:[be("div",{className:"aethercal-day-head",children:be("span",{className:"aethercal-day-number",children:Number(S.slice(-2))})}),lt("div",{className:"aethercal-day-events",children:[K.map(B=>{let ft=M?.eventId===B.id||!M&&J===B.id;return be(dt,{id:ye(S,B.id),event:B,interactive:le(B),isActive:ft,isGrabbed:M?.eventId===B.id,timeLabel:B.allDay?null:oe(B.start,a),onDragStart:o=>I({type:"DRAG_START",eventId:o}),onDragEnd:()=>I({type:"DRAG_CANCEL"}),isPending:k.has(B.id),isRolledBack:E.has(B.id),...b?{onClick:()=>b({id:B.id})}:{},...m?{onContextMenu:()=>m({id:B.id})}:{}},B.id)}),O>0&&!Y?be("button",{type:"button",className:"aethercal-more",onClick:()=>ce(S),children:i.more(O)}):null]})]},S)})},x))]}),be(st,{id:V,text:i.keyboardHint}),be(ot,{message:Re})]})}var cn={light:{"--ac-fg":"#1f2328","--ac-muted":"#6b7280","--ac-faint":"#9ca3af","--ac-bg":"#ffffff","--ac-header-fg":"#4b5563","--ac-border":"#e5e7eb","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#fafafa","--ac-today-marker-bg":"#111827","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#eef1f4","--ac-event-fg":"#1f2328","--ac-event-accent":"#64748b","--ac-more-fg":"#4b5563","--ac-focus":"#2563eb","--ac-rollback":"#b91c1c","--ac-tg-now":"#dc2626"},dark:{"--ac-fg":"#e6e8eb","--ac-muted":"#9aa1ab","--ac-faint":"#6b7280","--ac-bg":"#14161a","--ac-header-fg":"#b3b9c2","--ac-border":"#2a2e35","--ac-cell-bg":"#171a1f","--ac-cell-bg-outside":"#111318","--ac-today-marker-bg":"#e6e8eb","--ac-today-marker-fg":"#14161a","--ac-event-bg":"#242a32","--ac-event-fg":"#e6e8eb","--ac-event-accent":"#8b98a9","--ac-more-fg":"#b3b9c2","--ac-focus":"#6ea8fe","--ac-rollback":"#f87171","--ac-tg-now":"#f87171"},midnight:{"--ac-fg":"#dfe4ea","--ac-muted":"#8b95a1","--ac-faint":"#5b6675","--ac-bg":"#0b0f14","--ac-header-fg":"#a7b0bd","--ac-border":"#1c232c","--ac-cell-bg":"#0e131a","--ac-cell-bg-outside":"#090d12","--ac-today-marker-bg":"#dfe4ea","--ac-today-marker-fg":"#0b0f14","--ac-event-bg":"#17212c","--ac-event-fg":"#dfe4ea","--ac-event-accent":"#7f8ea3","--ac-more-fg":"#a7b0bd","--ac-focus":"#74a9ff","--ac-rollback":"#fb7185","--ac-tg-now":"#fb7185"},high_contrast:{"--ac-fg":"#000000","--ac-muted":"#000000","--ac-faint":"#1a1a1a","--ac-bg":"#ffffff","--ac-header-fg":"#000000","--ac-border":"#000000","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#ffffff","--ac-today-marker-bg":"#000000","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#e0e0e0","--ac-event-fg":"#000000","--ac-event-accent":"#000000","--ac-more-fg":"#000000","--ac-focus":"#0033cc","--ac-rollback":"#b00000","--ac-tg-now":"#d00000"}};var ct=cn,un=["light","dark","midnight","high_contrast"],Xn=new Set(un),jn={"--ac-font":'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',"--ac-radius":"8px","--ac-cell-min-height":"96px"},Zn={"--ac-tg-gutter":"56px","--ac-tg-body-height":"640px","--ac-tg-hour-min-height":"44px","--ac-tg-line":"var(--ac-border)","--ac-tg-event-bg":"var(--ac-event-bg)","--ac-tg-event-fg":"var(--ac-event-fg)","--ac-tg-event-accent":"var(--ac-event-accent)"},gn=["--ac-tg-now"],qn=/[;{}<>]/;function mn(e){return typeof e=="string"&&Xn.has(e)}function pn(e){return Object.entries(e).map(([t,n])=>`  ${t}: ${n};`).join(`
`)}function Qn(){let e={};for(let[t,n]of Object.entries(ct.light))gn.includes(t)||(e[t]=n);return e}function ea(){let e={};for(let t of gn){let n=ct.light[t];n!==void 0&&(e[t]=n)}return e}function _t(){return pn({...jn,...Qn()})}function $t(){return pn({...Zn,...ea()})}function ta(e){let t={};for(let[n,a]of Object.entries(e))n.startsWith("--ac-")&&(typeof a!="string"||a.trim()===""||qn.test(a)||(t[n]=a));return t}function zt(e){return e===void 0?{}:typeof e=="string"?mn(e)?{...ct[e]}:{}:ta(e)}var fn="aethercal-calendar-styles",vn=`
:where(.aethercal-calendar, .aethercal-calendar-shell) {
${_t()}
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

/* Navigation toolbar (F2-NAV): previous / today / next + period title + view switcher. The shell
   stacks the toolbar above the grid and carries the theme tokens so the toolbar themes with the
   calendar. Neutral, no glows \u2014 same anti-slop palette as the grid. */
.aethercal-calendar-shell {
  font-family: var(--ac-font);
  color: var(--ac-fg);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.aethercal-nav {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.aethercal-nav-group { display: inline-flex; align-items: center; gap: 4px; }
.aethercal-nav-btn {
  font: inherit;
  font-size: 13px;
  color: var(--ac-fg);
  background: var(--ac-cell-bg);
  border: 1px solid var(--ac-border);
  border-radius: calc(var(--ac-radius) - 2px);
  padding: 4px 10px;
  cursor: pointer;
  line-height: 1.4;
}
.aethercal-nav-btn:hover { background: var(--ac-cell-bg-outside); }
.aethercal-nav-btn:focus-visible { outline: 2px solid var(--ac-focus); outline-offset: 1px; }
.aethercal-nav-arrow {
  min-width: 32px;
  padding: 4px 8px;
  font-size: 16px;
  line-height: 1;
  text-align: center;
}
.aethercal-nav-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ac-fg);
  flex: 1 1 auto;
}
.aethercal-nav-views { display: inline-flex; gap: 4px; margin-left: auto; }
.aethercal-nav-view[aria-pressed="true"] {
  background: var(--ac-today-marker-bg);
  color: var(--ac-today-marker-fg);
  border-color: var(--ac-today-marker-bg);
}

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

/* Keyboard a11y (F2-E, RNF-7): the grid container is a single tabstop that manages an
   aria-activedescendant; the active cell/event carries the VISIBLE focus ring (so the container's
   own focus outline is suppressed), and a grabbed event (keyboard drag) reads a stronger ring. No
   glows \u2014 a plain outline, honoring the anti-slop palette. */
.aethercal-calendar:focus { outline: none; }
.aethercal-calendar:focus-visible { outline: none; }
.aethercal-day.is-active,
.aethercal-event.is-active,
.aethercal-agenda-event.is-active {
  outline: 2px solid var(--ac-focus);
  outline-offset: -2px;
}
.aethercal-event.is-active { outline-offset: 1px; border-radius: calc(var(--ac-radius) - 3px); }
.aethercal-event.is-grabbed {
  outline: 2px solid var(--ac-focus);
  outline-offset: 2px;
}
.aethercal-day.is-drop-target .aethercal-day-number {
  text-decoration: underline;
  text-decoration-color: var(--ac-focus);
}
/* Visually-hidden helper for the live-region announcer and keyboard-usage instructions: present in
   the accessibility tree, invisible on screen (never display:none, which would mute it). */
.aethercal-sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
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
  /* Belt-and-suspenders (F2-E, RNF-7): neutralize ANY animation/transition inside the calendar for
     users who ask for reduced motion \u2014 the pending/rollback cues above stay as static states, and
     any future animated affordance inherits this without a new opt-in. Keyboard focus/grab rings
     are outlines (no motion), so nothing load-bearing is lost. */
  .aethercal-calendar *,
  .aethercal-calendar *::before,
  .aethercal-calendar *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important;
    scroll-behavior: auto !important;
  }
}
`;function Je(){if(typeof document>"u"||document.getElementById(fn))return;let e=document.createElement("style");e.id=fn,e.textContent=vn,document.head.appendChild(e)}import*as C from"react";function Ge(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(y(e))}function yn(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function hn(e,t){if(e.length===0)return"";let n=y(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(n);let a=y(e[e.length-1]),r=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${r.format(n)} \u2013 ${r.format(a)}`}var bn="aethercal-timegrid-styles",Dn=`
:where(.aethercal-timegrid) {
${$t()}
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
/* Keyboard a11y (F2-E, RNF-7): the active time column / event carries the visible focus ring, and a
   keyboard-grabbed event (move or resize) reads a stronger ring \u2014 plain outlines, no glow. The
   existing is-drop-target column highlight doubles as the keyboard move/resize target cue. */
.aethercal-tg-col.is-active { outline: 2px solid var(--ac-focus); outline-offset: -2px; z-index: 1; }
.aethercal-tg-event.is-active { outline: 2px solid var(--ac-focus); outline-offset: 1px; z-index: 4; }
.aethercal-tg-event.is-grabbed { outline: 2px solid var(--ac-focus); outline-offset: 2px; z-index: 4; }
`;function Ht(){if(typeof document>"u"||document.getElementById(bn))return;let e=document.createElement("style");e.id=bn,e.textContent=Dn,document.head.appendChild(e)}import{Fragment as Tn,jsx as Z,jsxs as we}from"react/jsx-runtime";function ut(...e){return e.filter(Boolean).join(" ")}var De=e=>`${e*100}%`,En=new Set;function xn(e){let t=y(e);return t.getHours()*60+t.getMinutes()}function Vt(e,t,n){let a=y(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}function na(e,t,n,a){let{event:r,isContinuation:i,continuesAfter:s}=e;return i?s?n:a(oe(r.end,t)):oe(r.start,t)}function gt(e,t){let n=t.getBoundingClientRect();return n.height>0?(e-n.top)/n.height:0}function Bt(e){let{view:t,days:n,events:a,locale:r,config:i,now:s,themeVars:g,onEventDrop:c,onEventResize:l,onRangeSelect:h,onEventClick:b,onContextMenu:m,pendingIds:k=En,rolledBackIds:E=En}=e,u=C.useMemo(()=>{if(e.messages)return e.messages;let o={...e.allDayLabel!==void 0?{allDay:e.allDayLabel}:{},...e.continuesLabel!==void 0?{continues:e.continuesLabel}:{},...e.formatEndsLabel!==void 0?{endsAt:e.formatEndsLabel}:{}};return We(r,o)},[e.messages,e.allDayLabel,e.continuesLabel,e.formatEndsLabel,r]);C.useEffect(()=>{Je(),Ht()},[]);let T=C.useMemo(()=>Pt(n,a,i),[n,a,i]),z=C.useMemo(()=>Ot(s,i),[s,i]),G=C.useMemo(()=>re(F(s)),[s]),[H,X]=C.useReducer(xt,et),P=C.useRef(null),[W,I]=C.useState(null),[te,Ae]=C.useState(null),se=!!c,_=!!l,de=!!h,J=H.status==="dragging",U=C.useCallback((o,d)=>p=>{if(p.preventDefault(),H.status!=="dragging"){X({type:"COMMIT"});return}let w=H.eventId,D=p.dataTransfer.getData("text/plain");if(X({type:"COMMIT"}),D&&D!==w||!c)return;let v=a.find(ne=>ne.id===w);if(!v||v.editable===!1)return;let f=null;if(d&&v.allDay!==!0){let ee=p.currentTarget.getBoundingClientRect();ee.height>0&&Number.isFinite(p.clientY)&&(f=Ie((p.clientY-ee.top)/ee.height,T.config))}c(at(v,o,f))},[H,a,c,T.config]),M=C.useCallback(o=>{P.current?.kind!=="resize"&&X({type:"DRAG_START",eventId:o})},[]),q=C.useCallback(()=>X({type:"CANCEL"}),[]),Re=C.useCallback((o,d)=>p=>{if(!l||o.editable===!1||p.button!==0||P.current)return;let w=p.currentTarget.closest(".aethercal-tg-col");w?.dataset.date&&(p.preventDefault(),p.stopPropagation(),P.current={kind:"resize",pointerId:p.pointerId,eventId:o.id,edge:d,dateOnly:w.dataset.date,colEl:w,payload:null},p.currentTarget.setPointerCapture?.(p.pointerId),X({type:"RESIZE_START",eventId:o.id,edge:d}))},[l]),fe=C.useCallback(o=>d=>{if(!h||d.button!==0||P.current||d.target.closest("[data-event-id], button"))return;let p=d.currentTarget,w=Ie(gt(d.clientY,p),T.config);P.current={kind:"select",pointerId:d.pointerId,anchorDate:o,anchorCol:p,anchorMinute:w,currentDate:o,currentCol:p,currentMinute:w},p.setPointerCapture?.(d.pointerId),X({type:"SELECT_START",point:{dateOnly:o,minuteOfDay:w}})},[h,T.config]),le=H.status==="resizing"||H.status==="selecting";C.useLayoutEffect(()=>{if(!le)return;let o=v=>{let f=P.current;if(!(!f||v.pointerId!==f.pointerId))if(f.kind==="resize"){let ne=document.elementFromPoint(v.clientX,v.clientY)?.closest(".aethercal-tg-col"),ee=ne?.dataset.date?ne:f.colEl,Oe=Ie(gt(v.clientY,ee),T.config),$e=a.find(vt=>vt.id===f.eventId);if(!$e)return;let Ee=Be($e,f.edge,ee.dataset.date??f.dateOnly,Oe);f.payload=Ee,I(Ee)}else{let ne=document.elementFromPoint(v.clientX,v.clientY)?.closest(".aethercal-tg-col"),ee=ne?.dataset.date?ne:f.currentCol;f.currentCol=ee,f.currentDate=ee.dataset.date??f.anchorDate,f.currentMinute=Ie(gt(v.clientY,ee),T.config);let Oe=Ue({dateOnly:f.anchorDate,minuteOfDay:f.anchorMinute},{dateOnly:f.currentDate,minuteOfDay:f.currentMinute}),Ee=(f.currentDate===f.anchorDate?Ye([{id:"__sel",title:"",start:Oe.start,end:Oe.end}],f.anchorDate,T.config):[])[0];Ae(Ee?{dateOnly:f.anchorDate,topFraction:Ee.topFraction,heightFraction:Ee.heightFraction}:null)}},d=v=>{let f=P.current;P.current=null,I(null),Ae(null),v&&f&&(f.kind==="resize"&&f.payload&&l&&l(f.payload),f.kind==="select"&&h&&(f.currentDate!==f.anchorDate||f.currentMinute!==f.anchorMinute)&&h(Ue({dateOnly:f.anchorDate,minuteOfDay:f.anchorMinute},{dateOnly:f.currentDate,minuteOfDay:f.currentMinute}))),X({type:v?"COMMIT":"CANCEL"})},p=v=>{P.current&&v.pointerId!==P.current.pointerId||d(!0)},w=v=>{P.current&&v.pointerId!==P.current.pointerId||d(!1)},D=v=>{v.key==="Escape"&&d(!1)};return window.addEventListener("pointermove",o),window.addEventListener("pointerup",p),window.addEventListener("pointercancel",w),window.addEventListener("keydown",D),()=>{window.removeEventListener("pointermove",o),window.removeEventListener("pointerup",p),window.removeEventListener("pointercancel",w),window.removeEventListener("keydown",D)}},[le,a,T.config,l,h]);let ve=C.useCallback((o,d)=>p=>{if(!m||p.target.closest("[data-event-id], button"))return;if(p.preventDefault(),!d){m({start:`${o}T00:00:00`});return}let w=Ie(gt(p.clientY,p.currentTarget),T.config),D=y(`${o}T00:00:00`),v=new Date(D.getFullYear(),D.getMonth(),D.getDate(),0,w,0);m({start:F(v)})},[m,T.config]),ye=C.useId(),V=C.useMemo(()=>T.columns.map(o=>o.dateOnly),[T.columns]),[A,ce]=C.useState(()=>(V.includes(G)?G:V[0])??""),[$,j]=C.useState(null),[N,ue]=C.useState(null),[Pe,me]=C.useState("");C.useEffect(()=>{V.includes(A)||(ce(V[0]??""),j(null),ue(null))},[V,A]);let R=o=>`${ye}-col-${o}`,x=(o,d)=>`${ye}-e-${o}-${d}`,S=`${ye}-hint`,Q=Se,Y=C.useCallback(o=>!!b||o.editable!==!1&&!!(c||l),[b,c,l]),K=C.useMemo(()=>{let o=T.columns.find(d=>d.dateOnly===A);return o?[...o.allDay,...o.timed.map(d=>d.event)]:[]},[T.columns,A]),O=C.useMemo(()=>K.filter(o=>Y(o)),[K,Y]);C.useEffect(()=>{let o=new Set(O.map(d=>d.id));N&&!o.has(N.eventId)?(ue(null),j(null)):!N&&$!==null&&!o.has($)&&j(null)},[O,$,N]);let _e=N?x(A,N.eventId):$?x(A,$):R(A),je=C.useCallback(o=>{let d=N;if(!d)return;let p=d.dateOnly,w=d.minute,D=a.find(f=>f.id===d.eventId),v=D?.allDay===!0;if(!v&&(o==="ArrowUp"||o==="ArrowDown")){let f=Ct(p,w,o==="ArrowUp"?-Q:Q,T.config);p=f.dateOnly,w=f.minuteOfDay}else o==="ArrowLeft"?p=pe(p,-1):o==="ArrowRight"&&(p=pe(p,1));if(!(p===d.dateOnly&&w===d.minute)){if(D)if(d.kind==="move")me(u.movedTo(v?Ge(p,r):`${Ge(p,r)} ${Vt(p,w,r)}`));else{let f=Be(D,"end",p,w);me(u.resizedTo(`${oe(f.start,r)} \u2013 ${oe(f.end,r)}`))}ue({...d,dateOnly:p,minute:w,moved:!0})}},[N,Q,T.config,a,u,r]),Ze=C.useCallback(()=>{let o=N;if(!o)return;if(!o.moved){j(o.eventId),ue(null);return}let d=a.find(p=>p.id===o.eventId);if(d&&d.editable!==!1&&o.kind==="move"&&c){let p=at(d,o.dateOnly,d.allDay===!0?null:o.minute);c(p);let w=re(p.start);ce(V.includes(w)?w:A),j(null),me(u.dropped(d.allDay===!0?Ge(o.dateOnly,r):Vt(o.dateOnly,o.minute,r)))}else if(d&&d.editable!==!1&&o.kind==="resize"&&l){let p=Be(d,"end",o.dateOnly,o.minute);l(p),j(o.eventId),me(u.resized(`${oe(p.start,r)} \u2013 ${oe(p.end,r)}`))}else j(o.eventId);ue(null)},[N,a,c,l,V,A,u,r]),pt=C.useCallback(o=>{let{key:d}=o,p=d==="Enter"||d===" "||d==="Spacebar",w=d==="ArrowUp"||d==="ArrowDown"||d==="ArrowLeft"||d==="ArrowRight";if(N){if(w){o.preventDefault(),je(d);return}if(p){o.preventDefault(),Ze();return}if(d==="Escape"){o.preventDefault(),ue(null),me(u.cancelled);return}return}if($){let D=O.findIndex(v=>v.id===$);if(d==="ArrowDown"){o.preventDefault(),D>=0&&D<O.length-1&&j(O[D+1].id);return}if(d==="ArrowUp"){o.preventDefault(),D>0?j(O[D-1].id):j(null);return}if(d==="ArrowLeft"||d==="ArrowRight"){o.preventDefault(),j(null);let v=V.indexOf(A);ce(V[Me(v,d,1,V.length)]);return}if(p){o.preventDefault();let v=O.find(f=>f.id===$);if(!v)return;v.editable!==!1&&c?(ue({kind:"move",eventId:v.id,dateOnly:re(v.start),minute:xn(v.start),moved:!1}),me(u.grabbedMoveHint(v.title))):b&&b({id:v.id});return}if((d==="r"||d==="R")&&l){o.preventDefault();let v=O.find(f=>f.id===$);v&&v.allDay!==!0&&v.editable!==!1&&(ue({kind:"resize",eventId:v.id,dateOnly:re(v.end),minute:xn(v.end),moved:!1}),me(u.grabbedResizeHint(v.title)));return}if(d==="Escape"){o.preventDefault(),j(null);return}return}if(d==="ArrowLeft"||d==="ArrowRight"||d==="Home"||d==="End"){o.preventDefault();let D=V.indexOf(A);ce(V[Me(D,d,1,V.length)]);return}if(d==="ArrowDown"){O.length>0&&(o.preventDefault(),j(O[0].id));return}if(p){if(O.length>0)o.preventDefault(),j(O[0].id);else if(K.length===0&&h){let D=T.config.dayEndHour*60,v=nt(T.config.dayStartHour*60,T.config),f=Math.min(v+60,D);f>v&&(o.preventDefault(),h(Ue({dateOnly:A,minuteOfDay:v},{dateOnly:A,minuteOfDay:f})),me(u.createHere(`${Ge(A,r)} ${Vt(A,v,r)}`)))}}},[N,$,A,K,O,V,c,l,b,h,je,Ze,T.config,u,r]),B={"--ac-tg-cols":T.columns.length,"--ac-tg-hours":T.config.dayEndHour-T.config.dayStartHour,...g??{}},ft=u.allDay;return we(Tn,{children:[we("div",{className:ut("aethercal-calendar","aethercal-timegrid",J&&"is-dragging",H.status==="resizing"&&"is-resizing",H.status==="selecting"&&"is-selecting"),role:"grid","aria-label":hn(n,r),"aria-describedby":S,"aria-activedescendant":_e,tabIndex:0,"data-view":t,style:B,onKeyDown:pt,children:[we("div",{className:"aethercal-tg-head",role:"row",children:[Z("div",{className:"aethercal-tg-corner"}),T.columns.map(o=>Z("div",{role:"columnheader",className:ut("aethercal-tg-colhead",o.dateOnly===G&&"is-today"),"data-date":o.dateOnly,children:Z("span",{className:"aethercal-tg-colhead-date",children:Ge(o.dateOnly,r)})},o.dateOnly))]}),we("div",{className:"aethercal-tg-allday",role:"row",children:[Z("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:ft}),T.columns.map(o=>Z("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":o.dateOnly,onDragOver:se?d=>d.preventDefault():void 0,onDrop:se?U(o.dateOnly,!1):void 0,onContextMenu:m?ve(o.dateOnly,!1):void 0,children:o.allDay.map(d=>{let p=N?.eventId===d.id&&o.dateOnly===A||!N&&$===d.id&&o.dateOnly===A;return Z(dt,{id:x(o.dateOnly,d.id),event:d,interactive:Y(d),isActive:p,isGrabbed:N?.eventId===d.id&&o.dateOnly===A,timeLabel:null,onDragStart:M,onDragEnd:q,isPending:k.has(d.id),isRolledBack:E.has(d.id),...b?{onClick:()=>b({id:d.id})}:{},...m?{onContextMenu:()=>m({id:d.id})}:{}},d.id)})},o.dateOnly))]}),we("div",{className:"aethercal-tg-body",role:"row",children:[Z("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:T.hourMarks.map(o=>Z("div",{className:"aethercal-tg-hour",style:{top:De(o.topFraction)},children:yn(o.hour,r)},o.hour))}),T.columns.map(o=>{let d=!$&&!N&&o.dateOnly===A,p=N?.dateOnly===o.dateOnly;return we("div",{id:R(o.dateOnly),role:"gridcell",className:ut("aethercal-tg-col",o.dateOnly===G&&"is-today",d&&"is-active",p&&"is-drop-target"),"data-date":o.dateOnly,onDragOver:se?w=>w.preventDefault():void 0,onDrop:se?U(o.dateOnly,!0):void 0,onPointerDown:de?fe(o.dateOnly):void 0,onContextMenu:m?ve(o.dateOnly,!0):void 0,children:[T.hourMarks.map(w=>Z("div",{className:"aethercal-tg-line",style:{top:De(w.topFraction)},"aria-hidden":"true"},w.hour)),te&&te.dateOnly===o.dateOnly?Z("div",{className:"aethercal-tg-select-band",style:{top:De(te.topFraction),height:De(te.heightFraction)},"aria-hidden":"true"}):null,o.timed.map(w=>{let{event:D}=w,v=D.editable!==!1,f=na(w,r,u.continues,u.endsAt),ne=W?.id===D.id?W:null,ee=ne?Ye([{...D,start:ne.start,end:ne.end}],o.dateOnly,T.config)[0]:void 0,Oe=ee?ee.topFraction:w.topFraction,$e=ee?ee.heightFraction:w.heightFraction,Ee=N?.eventId===D.id&&o.dateOnly===A||!N&&$===D.id&&o.dateOnly===A,vt=N?.eventId===D.id&&o.dateOnly===A,wn={top:De(Oe),height:De($e),left:De(w.lane/w.laneCount),width:De(1/w.laneCount),...D.color?{"--ac-tg-event-accent":D.color}:{}};return we("div",{id:x(o.dateOnly,D.id),className:ut("aethercal-tg-event",!v&&"is-locked",k.has(D.id)&&"is-pending",E.has(D.id)&&"is-rolledback",!!ne&&"is-resizing",Ee&&"is-active",vt&&"is-grabbed"),...Y(D)?{role:"button"}:{},draggable:v,"data-event-id":D.id,"data-lane":w.lane,"data-lane-count":w.laneCount,"aria-label":`${f} ${D.title}`,title:D.title,style:wn,onDragStart:Le=>{if(P.current?.kind==="resize"){Le.preventDefault();return}Le.dataTransfer.setData("text/plain",D.id),Le.dataTransfer.effectAllowed="move",M(D.id)},onDragEnd:q,onClick:b?()=>b({id:D.id}):void 0,onContextMenu:m?Le=>{Le.preventDefault(),Le.stopPropagation(),m({id:D.id})}:void 0,children:[Z("time",{className:"aethercal-tg-event-time",children:f}),Z("span",{className:"aethercal-tg-event-title",children:D.title}),_&&v?we(Tn,{children:[Z("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:Re(D,"start")}),Z("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:Re(D,"end")})]}):null]},D.id)}),z!==null&&o.dateOnly===G?Z("div",{className:"aethercal-now-indicator",style:{top:De(z)},"aria-hidden":"true"}):null]},o.dateOnly)})]})]}),Z(st,{id:S,text:u.keyboardHint}),Z(ot,{message:Pe})]})}import{jsx as Xe,jsxs as oa}from"react/jsx-runtime";function aa(e){if(e instanceof Date)return e;if(typeof e=="string"){let t=e.trim();if(t==="")return new Date;try{return y(t)}catch{return new Date}}return new Date}function ra(e){return e instanceof Date?e:typeof e=="string"?y(e):new Date}function mt(e){let{view:t="month",events:n,anchor:a,locale:r="en",theme:i,messages:s,firstDayOfWeek:g=1,maxEventsPerDay:c=3,weekdayLabels:l,formatMore:h,unavailableLabel:b,dayStartHour:m,dayEndHour:k,allDayLabel:E,now:u,continuesLabel:T,formatEndsLabel:z,agendaEmptyLabel:G,onEventDrop:H,onEventResize:X,onRangeSelect:P,onEventClick:W,onContextMenu:I,navigation:te=!1,navigationViews:Ae=!0,onRangeChange:se,onViewChange:_,pendingIds:de,rolledBackIds:J}=e;ge.useEffect(()=>{Je()},[]);let U=ge.useMemo(()=>aa(a),[a]),M=ge.useMemo(()=>zt(i),[i]),q=ge.useMemo(()=>{let $={...E!==void 0?{allDay:E}:{},...T!==void 0?{continues:T}:{},...z!==void 0?{endsAt:z}:{},...G!==void 0?{noEvents:G}:{},...b!==void 0?{unavailable:b}:{},...h!==void 0?{more:h}:{},...s};return We(r,$)},[r,E,T,z,G,b,h,s]),[Re,fe]=ge.useState(()=>new Date);ge.useEffect(()=>{if(u!==void 0||t!=="week"&&t!=="day")return;let $=setInterval(()=>fe(new Date),6e4);return()=>clearInterval($)},[u,t]);let le=ge.useMemo(()=>u!==void 0?ra(u):Re,[u,Re]),ve=Number.isInteger(g)&&g>=0&&g<=6?g:1,ye=Number.isInteger(c)&&c>=0?c:3,V=l&&l.length===7?l:void 0,A=ge.useMemo(()=>({...m!==void 0?{dayStartHour:m}:{},...k!==void 0?{dayEndHour:k}:{}}),[m,k]),ce=(()=>{if(t==="list")return Xe(en,{events:n??[],locale:r,messages:q,themeVars:M});if(t==="month")return Xe(ln,{events:n??[],anchor:U,locale:r,messages:q,themeVars:M,firstDayOfWeek:ve,maxEventsPerDay:ye,...V?{weekdayLabels:V}:{},...H?{onEventDrop:H}:{},...P?{onRangeSelect:P}:{},...W?{onEventClick:W}:{},...I?{onContextMenu:I}:{},...de?{pendingIds:de}:{},...J?{rolledBackIds:J}:{}});if(t==="week"||t==="day"){let $=t==="week"?yt(U,ve):[re(F(U))];return Xe(Bt,{view:t,days:$,events:n??[],locale:r,messages:q,themeVars:M,config:A,now:le,...H?{onEventDrop:H}:{},...X?{onEventResize:X}:{},...P?{onRangeSelect:P}:{},...W?{onEventClick:W}:{},...I?{onContextMenu:I}:{},...de?{pendingIds:de}:{},...J?{rolledBackIds:J}:{}})}return Xe("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,style:M,children:q.unavailable})})();return te?oa("div",{className:"aethercal-calendar-shell",style:M,children:[Xe(Ft,{view:t,anchor:U,now:le,locale:r,firstDayOfWeek:ve,messages:q,showViews:Ae,...se?{onRangeChange:se}:{},..._?{onViewChange:_}:{}}),ce]}):ce}var ia=mt;import*as ie from"react";function sa(){return typeof crypto<"u"&&typeof crypto.randomUUID=="function"?crypto.randomUUID():`cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}var da=8e3,la=900;function Ut(e){let{events:t,mutate:n,timeoutMs:a=da,rollbackFlashMs:r=la,generateId:i=sa}=e,[s,g]=ie.useReducer(kt,Mt),c=ie.useRef(t);c.current=t;let l=ie.useRef(!0),h=ie.useRef(new Map);ie.useEffect(()=>{l.current=!0;let k=h.current;return()=>{l.current=!1;for(let E of k.values())clearTimeout(E);k.clear()}},[]),ie.useEffect(()=>{for(let k of It(t,s)){let E=s.overrides[k];g({type:"CLEAR",id:k,...E?{clientMutationId:E.clientMutationId}:{}})}},[t,s]);let b=ie.useCallback((k,E)=>{let u=i(),T=c.current.find(I=>I.id===E.id),z=h.current,G=I=>{let te=z.get(I);te!==void 0&&(clearTimeout(te),z.delete(I))},H=()=>{z.set(`fl:${u}`,setTimeout(()=>{z.delete(`fl:${u}`),l.current&&g({type:"CLEAR",id:E.id,clientMutationId:u})},r))};g({type:"SUBMIT",id:E.id,clientMutationId:u,start:E.start,end:E.end,...T?.revision!==void 0?{baseRevision:T.revision}:{}}),z.set(`to:${u}`,setTimeout(()=>{z.delete(`to:${u}`),l.current&&(g({type:"TIMEOUT",id:E.id,clientMutationId:u}),H())},a));let X=()=>{G(`to:${u}`),l.current&&(g({type:"REJECT",id:E.id,clientMutationId:u}),H())},P={kind:k,clientMutationId:u,payload:{...E,client_mutation_id:u}},W;try{W=n(P)}catch(I){W=Promise.reject(I instanceof Error?I:new Error(String(I)))}W.then(I=>{if(I.id!==E.id){X();return}G(`to:${u}`),l.current&&g({type:"RESOLVE",id:I.id,clientMutationId:u,start:I.start,end:I.end,revision:I.revision})}).catch(X)},[n,a,r,i]),m=ie.useMemo(()=>St(t,s),[t,s]);return{events:m.events,pendingIds:m.pendingIds,rolledBackIds:m.rolledBackIds,submit:b}}import{jsx as ua}from"react/jsx-runtime";function ca({events:e,mutate:t,timeoutMs:n,rollbackFlashMs:a,generateId:r,...i}){let{events:s,pendingIds:g,rolledBackIds:c,submit:l}=Ut({events:e,mutate:t,...n!==void 0?{timeoutMs:n}:{},...a!==void 0?{rollbackFlashMs:a}:{},...r?{generateId:r}:{}});return ua(mt,{...i,events:s,pendingIds:g,rolledBackIds:c,onEventDrop:h=>l("drop",h),onEventResize:h=>l("resize",h)})}export{mt as AetherCalendar,vn as CALENDAR_CSS,Ft as CalendarNav,Gt as DEFAULT_LOCALE_MESSAGES,ca as OptimisticCalendar,ct as PRESETS,un as PRESET_NAMES,Dn as TIME_GRID_CSS,Bt as TimeGridView,ia as default,_t as defaultBaseTokenCss,$t as defaultTimeGridTokenCss,Je as ensureCalendarStyles,Ht as ensureTimeGridStyles,He as getVisibleRange,mn as isThemePreset,y as parseLocalDateTime,We as resolveMessages,zt as resolveThemeVars,Ve as stepAnchor,Ut as useOptimisticEvents};
