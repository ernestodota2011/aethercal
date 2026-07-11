function ae(e){return String(e).padStart(2,"0")}function v(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,n,a,r,i,d,y]=t,c=Number(n),l=Number(a),h=Number(r),T=Number(i??"0"),g=Number(d??"0"),C=Number(y??"0");if(l<1||l>12||h<1||h>31||T>23||g>59||C>59)throw new Error(`out-of-range ISO datetime: ${e}`);let D=new Date(c,l-1,h,T,g,C);if(D.getFullYear()!==c||D.getMonth()!==l-1||D.getDate()!==h)throw new Error(`nonexistent calendar date: ${e}`);return D}function H(e){return`${e.getFullYear()}-${ae(e.getMonth()+1)}-${ae(e.getDate())}T${ae(e.getHours())}:${ae(e.getMinutes())}:${ae(e.getSeconds())}`}function re(e){let t=v(e);return`${t.getFullYear()}-${ae(t.getMonth()+1)}-${ae(t.getDate())}`}function zt(e){return`${e.getFullYear()}-${ae(e.getMonth()+1)}-${ae(e.getDate())}`}function _e(e){let t=v(e.start),n=v(e.end),a=new Date(t.getFullYear(),t.getMonth(),t.getDate()),r=a;if(n.getTime()>t.getTime()){let i=new Date(n.getTime()-1),d=new Date(i.getFullYear(),i.getMonth(),i.getDate());d.getTime()>a.getTime()&&(r=d)}return{startKey:zt(a),lastKey:zt(r)}}function bn(e,t){return(e.getDay()-t+7)%7}function mt(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate());return n.setDate(n.getDate()-bn(n,t)),n}function Ht(e,t){return Array.from({length:t},(n,a)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+a);return`${r.getFullYear()}-${ae(r.getMonth()+1)}-${ae(r.getDate())}`})}function ft(e,t=1){return Ht(mt(e,t),7)}function pt(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),1);return Ht(mt(n,t),42)}function pe(e,t){let n=v(`${re(e)}T00:00:00`),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()+t);return`${a.getFullYear()}-${ae(a.getMonth()+1)}-${ae(a.getDate())}`}function yt(e,t){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()),a=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((a.getTime()-n.getTime())/864e5)}function Pe(e,t){let n=v(e.start),a=v(e.end),r=v(t),i=yt(n,r),d=new Date(n.getFullYear(),n.getMonth(),n.getDate()+i,n.getHours(),n.getMinutes(),n.getSeconds()),y=new Date(a.getFullYear(),a.getMonth(),a.getDate()+i,a.getHours(),a.getMinutes(),a.getSeconds()),c={id:e.id,start:H(d),end:H(y)};return e.revision!==void 0&&(c.revision=e.revision),c}var Dn=370;function Bt(e){return String(e).padStart(2,"0")}function Ut(e){return`${e.getFullYear()}-${Bt(e.getMonth()+1)}-${Bt(e.getDate())}`}function En(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function Tn(e){let{startKey:t,lastKey:n}=_e(e),a=[],r=v(t);for(let i=0;i<Dn&&Ut(r)<=n;i+=1)a.push(Ut(r)),r=En(r,1);return{keys:a,startKey:t,lastKey:n}}function vt(e){let t=new Map;return e.forEach((n,a)=>{let{keys:r,startKey:i,lastKey:d}=Tn(n),y=v(n.start).getTime(),c=v(n.end).getTime();for(let l of r){let h={entry:{event:n,isContinuation:l!==i,continuesAfter:l!==d},startMs:y,endMs:c,index:a},T=t.get(l);T?T.push(h):t.set(l,[h])}}),[...t.keys()].sort().map(n=>{let a=t.get(n);return a.sort((r,i)=>r.startMs-i.startMs||r.endMs-i.endMs||r.index-i.index),{date:n,entries:a.map(r=>r.entry)}})}function Me(e,t,n,a){let r=n*a;if(r<=0)return e;let i=Math.min(Math.max(e,0),r-1),d=i-i%a,y=Math.min(d+a-1,r-1);switch(t){case"ArrowLeft":return i>d?i-1:i;case"ArrowRight":return i<y?i+1:i;case"ArrowUp":{let c=i-a;return c>=0?c:i}case"ArrowDown":{let c=i+a;return c<r?c:i}case"Home":return d;case"End":return y;default:return i}}var We={status:"idle"};function Je(e){return e.status==="dragging"}function ht(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return We}}var Xe={status:"idle"};function bt(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"RESIZE_START":return{status:"resizing",eventId:t.eventId,edge:t.edge};case"SELECT_START":return{status:"selecting",anchor:t.point,current:t.point};case"SELECT_MOVE":return e.status!=="selecting"?e:{status:"selecting",anchor:e.anchor,current:t.point};case"COMMIT":case"CANCEL":return Xe}}var we=60,Ce=15;function Et(e,t,n){return Math.min(n,Math.max(t,e))}function je(e,t){let n=v(`${e}T00:00:00`);return new Date(n.getFullYear(),n.getMonth(),n.getDate(),0,t,0)}function Tt(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate(),e.getHours(),e.getMinutes()+t,e.getSeconds())}function Se(e,t,n=Ce){let a=t.dayStartHour*we,r=t.dayEndHour*we,i=a+Et(e,0,1)*t.windowMinutes,d=n>0?n:Ce,y=a+Math.round((i-a)/d)*d;return Et(y,a,r)}function Ze(e,t){return Et(e,t.dayStartHour*we,t.dayEndHour*we)}var Dt=24*we;function xt(e,t,n,a){let r=t+n,i=e;for(;r<0;)r+=Dt,i=pe(i,-1);for(;r>Dt;)r-=Dt,i=pe(i,1);return{dateOnly:i,minuteOfDay:Ze(r,a)}}function qe(e,t,n){if(n===null)return Pe(e,t);let a=v(e.start),r=v(e.end),i=je(t,n),d=yt(a,r),y=a.getHours()*we+a.getMinutes(),l=r.getHours()*we+r.getMinutes()-y,h=new Date(i.getFullYear(),i.getMonth(),i.getDate()+d,i.getHours(),i.getMinutes()+l,0),T={id:e.id,start:H(i),end:H(h)};return e.revision!==void 0&&(T.revision=e.revision),T}function $e(e,t,n,a,r={}){let i=r.minDurationMinutes??Ce,d=v(e.start),y=v(e.end),c=je(n,a),l=d,h=y;if(t==="end"){let g=Tt(d,i);h=c.getTime()>=g.getTime()?c:g}else{let g=Tt(y,-i);l=c.getTime()<=g.getTime()?c:g}let T={id:e.id,start:H(l),end:H(h)};return e.revision!==void 0&&(T.revision=e.revision),T}function ze(e,t,n={}){let a=n.minDurationMinutes??Ce;if(e.minuteOfDay===null||t.minuteOfDay===null){let[l,h]=e.dateOnly<=t.dateOnly?[e.dateOnly,t.dateOnly]:[t.dateOnly,e.dateOnly],T=v(`${l}T00:00:00`),g=v(`${h}T00:00:00`),C=new Date(g.getFullYear(),g.getMonth(),g.getDate()+1);return{start:H(T),end:H(C),allDay:!0}}let i=je(e.dateOnly,e.minuteOfDay??0),d=je(t.dateOnly,t.minuteOfDay??0),y=i.getTime()<=d.getTime()?i:d,c=i.getTime()<=d.getTime()?d:i;return c.getTime()===y.getTime()&&(c=Tt(y,a)),{start:H(y),end:H(c),allDay:!1}}var Rt={overrides:{},appliedRevision:{}};function xn(e,t){let n={...e};return delete n[t],n}function Mt(e,t){switch(t.type){case"SUBMIT":{let n=t.baseRevision??Number.NEGATIVE_INFINITY,a=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;return{overrides:{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"pending",start:t.start,end:t.end,...t.baseRevision!==void 0?{revision:t.baseRevision}:{}}},appliedRevision:{...e.appliedRevision,[t.id]:Math.max(a,n)}}}case"RESOLVE":{let n=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;if(t.revision<=n)return e;let a=e.overrides[t.id];return{overrides:a!==void 0&&a.clientMutationId===t.clientMutationId&&a.status==="pending"?{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"committed",start:t.start,end:t.end,revision:t.revision}}:e.overrides,appliedRevision:{...e.appliedRevision,[t.id]:t.revision}}}case"REJECT":case"TIMEOUT":{let n=e.overrides[t.id];return!n||n.clientMutationId!==t.clientMutationId||n.status!=="pending"?e:{...e,overrides:{...e.overrides,[t.id]:{...n,status:"rolledback"}}}}case"CLEAR":{let n=e.overrides[t.id];return!n||t.clientMutationId&&n.clientMutationId!==t.clientMutationId?e:{...e,overrides:xn(e.overrides,t.id)}}}}function wt(e,t){let n=new Set,a=new Set;return{events:e.map(i=>{let d=t.overrides[i.id];return d?d.status==="pending"?(n.add(i.id),{...i,start:d.start,end:d.end}):d.status==="rolledback"?(a.add(i.id),i):i.revision!==void 0&&d.revision!==void 0&&i.revision>=d.revision?i:{...i,start:d.start,end:d.end,...d.revision!==void 0?{revision:d.revision}:{}}:i}),pendingIds:n,rolledBackIds:a}}function Ct(e,t){let n=new Map(e.map(r=>[r.id,r])),a=[];for(let[r,i]of Object.entries(t.overrides)){if(i.status!=="committed")continue;let d=n.get(r);d&&d.revision!==void 0&&i.revision!==void 0&&d.revision>=i.revision&&a.push(r)}return a}var ve=60,Rn=24*ve,Mn=864e5;function Qe(e,t,n){return Math.min(n,Math.max(t,e))}function St(e={}){let t=e.dayStartHour,n=e.dayEndHour,a=Number.isFinite(t)&&t!==void 0?Qe(Math.trunc(t),0,23):0,r=Number.isFinite(n)&&n!==void 0?Qe(Math.trunc(n),1,24):24,[i,d]=r>a?[a,r]:[0,24];return{dayStartHour:i,dayEndHour:d,windowMinutes:(d-i)*ve}}function Kt(e){let t=[],n=[];for(let a of e)a.allDay===!0?t.push(a):n.push(a);return{allDay:t,timed:n}}function Yt(e,t){let n=v(e),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()),r=Math.round((a.getTime()-t.getTime())/Mn),i=n.getHours()*ve+n.getMinutes()+n.getSeconds()/60;return r*Rn+i}function wn(e,t){let n=v(e.start).getTime(),a=v(e.end).getTime(),r=v(t.start).getTime(),i=v(t.end).getTime();return n<i&&r<a}function He(e,t,n){let a=v(`${t}T00:00:00`),r=n.dayStartHour*ve,i=n.dayEndHour*ve,d=[...e].sort((g,C)=>{let D=v(g.start).getTime(),u=v(C.start).getTime();return D!==u?D-u:v(C.end).getTime()-v(g.end).getTime()}),y=[],c=[],l=[],h=Number.NEGATIVE_INFINITY,T=()=>{let g=c.length;for(let C of l)y[C].laneCount=g;c=[],l=[],h=Number.NEGATIVE_INFINITY};for(let g of d){let C=Yt(g.start,a),D=Yt(g.end,a);if(D<=r||C>=i)continue;let u=v(g.start).getTime(),x=v(g.end).getTime();l.length>0&&u>=h&&T();let _=c.findIndex(X=>!wn(X,g));_===-1?(_=c.length,c.push(g)):c[_]=g;let G=Qe(C,r,i),$=Qe(D,G,i),K=(G-r)/n.windowMinutes,A=($-G)/n.windowMinutes,{startKey:Y,lastKey:I}=_e(g);l.push(y.length),y.push({event:g,lane:_,laneCount:1,topFraction:K,heightFraction:A,isContinuation:t!==Y,continuesAfter:t!==I}),h=Math.max(h,x)}return T(),y}function Cn(e){let t=[];for(let n=e.dayStartHour;n<e.dayEndHour;n+=1)t.push({hour:n,topFraction:(n-e.dayStartHour)*ve/e.windowMinutes});return t}function kt(e,t,n={}){let a="windowMinutes"in n?n:St(n),{allDay:r,timed:i}=Kt(t),d=i.map(c=>({event:c,startTs:v(c.start).getTime(),endTs:v(c.end).getTime()}));return{columns:e.map(c=>{let l=v(`${c}T00:00:00`),h=l.getTime(),T=new Date(l.getFullYear(),l.getMonth(),l.getDate()+1).getTime(),g=d.filter(D=>D.startTs>=T?!1:D.endTs>h?!0:D.startTs===D.endTs&&D.startTs>=h).map(D=>D.event),C=r.filter(D=>{let{startKey:u,lastKey:x}=_e(D);return u<=c&&c<=x});return{dateOnly:c,allDay:C,timed:He(g,c,a)}}),hourMarks:Cn(a),config:a}}function It(e,t={}){let n="windowMinutes"in t?t:St(t),a=e.getHours()*ve+e.getMinutes()+e.getSeconds()/60,r=n.dayStartHour*ve,i=n.dayEndHour*ve;return a<r||a>=i?null:(a-r)/n.windowMinutes}import*as ue from"react";import*as et from"react";var At=new Date(2023,0,1);function Vt(e,t){let n=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(a,r)=>{let i=(t+r)%7,d=new Date(At.getFullYear(),At.getMonth(),At.getDate()+i);return n.format(d)})}function Wt(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function Be(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(v(e))}function se(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(v(e))}function Jt(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(v(e))}import{jsx as Te,jsxs as jt}from"react/jsx-runtime";function Sn(...e){return e.filter(Boolean).join(" ")}function kn(e,t,n){let{event:a,isContinuation:r,continuesAfter:i}=e;return a.allDay===!0?n.allDay:r?i?n.continues:n.endsAt(se(a.end,t)):se(a.start,t)}function In({entry:e,locale:t,messages:n}){let{event:a,isContinuation:r,continuesAfter:i}=e,d=kn(e,t,n),y=a.color?{"--ac-event-accent":a.color}:void 0;return jt("li",{className:Sn("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":a.id,"aria-label":`${d} ${a.title}`,style:y,...a.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...i?{"data-continues-after":""}:{},children:[Te("span",{className:"aethercal-agenda-event-time",children:d}),Te("span",{className:"aethercal-agenda-event-title",children:a.title})]})}function Xt({events:e,locale:t,messages:n,themeVars:a}){let r=et.useMemo(()=>vt(e),[e]),i=et.useId();return r.length===0?Te("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:Te("p",{className:"aethercal-agenda-empty",children:n.noEvents})}):Te("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:r.map(d=>{let y=`${i}-${d.date}`;return jt("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":y,"data-date":d.date,children:[Te("div",{className:"aethercal-agenda-day-title",id:y,children:Jt(d.date,t)}),Te("ul",{className:"aethercal-agenda-day-events",role:"list",children:d.entries.map((c,l)=>Te(In,{entry:c,locale:t,messages:n},`${c.event.id}-${l}`))})]},d.date)})})}var An={allDay:"All day",continues:"Continues",endsAt:e=>`ends ${e}`,more:e=>`+${e} more`,noEvents:"No events",unavailable:"This view is not available yet.",keyboardHint:"Use the arrow keys to move between days. Press Enter on an event to grab it, the arrow keys to move or resize it, Enter to drop, and Escape to cancel.",grabbedMoveHint:e=>`Grabbed ${e}. Use the arrow keys to move it, Enter to drop, Escape to cancel.`,grabbedResizeHint:e=>`Resizing ${e}. Use the up and down arrow keys to change its duration, Enter to confirm, Escape to cancel.`,movedTo:e=>`Moved to ${e}`,resizedTo:e=>`Duration ${e}`,dropped:e=>`Dropped on ${e}`,resized:e=>`Duration set to ${e}`,createHere:e=>`Create an event on ${e}`,cancelled:"Cancelled"},On={allDay:"Todo el d\xEDa",continues:"Contin\xFAa",endsAt:e=>`termina ${e}`,more:e=>`+${e} m\xE1s`,noEvents:"Sin eventos",unavailable:"Esta vista a\xFAn no est\xE1 disponible.",keyboardHint:"Usa las flechas para moverte entre los d\xEDas. Pulsa Enter sobre un evento para agarrarlo, las flechas para moverlo o cambiar su duraci\xF3n, Enter para soltarlo y Escape para cancelar.",grabbedMoveHint:e=>`Agarraste el evento ${e}. Usa las flechas para moverlo, Enter para soltarlo y Escape para cancelar.`,grabbedResizeHint:e=>`Est\xE1s cambiando la duraci\xF3n de ${e}. Usa las flechas hacia arriba y abajo para ajustarla, Enter para confirmar y Escape para cancelar.`,movedTo:e=>`Movido a ${e}`,resizedTo:e=>`Duraci\xF3n ${e}`,dropped:e=>`Soltado en ${e}`,resized:e=>`Duraci\xF3n establecida en ${e}`,createHere:e=>`Crear un evento en ${e}`,cancelled:"Cancelado"},Ot={en:An,es:On};function Pn(e){return e.toLowerCase().split("-")[0]??""}function Ue(e,t,n=Ot){let a=e.toLowerCase(),r=n[a]??n[Pn(e)]??n.en??Ot.en;return t?{...r,...t}:r}import*as P from"react";import{jsx as Zt}from"react/jsx-runtime";function tt({message:e}){return Zt("div",{className:"aethercal-sr-only","aria-live":"polite","aria-atomic":"true",children:e})}function nt({id:e,text:t}){return Zt("div",{id:e,className:"aethercal-sr-only",children:t})}import{jsx as qt,jsxs as Fn}from"react/jsx-runtime";function Ln(...e){return e.filter(Boolean).join(" ")}function at({event:e,timeLabel:t,onDragStart:n,onDragEnd:a,isPending:r,isRolledBack:i,onClick:d,onContextMenu:y,id:c,interactive:l,isActive:h,isGrabbed:T}){let g=e.editable!==!1,C=e.color?{"--ac-event-accent":e.color}:void 0,D=t?`${t} ${e.title}`:e.title;return Fn("div",{className:Ln("aethercal-event",!g&&"is-locked",r&&"is-pending",i&&"is-rolledback",h&&"is-active",T&&"is-grabbed"),...c?{id:c}:{},...l?{role:"button"}:{},draggable:g,"data-event-id":e.id,"aria-label":D,title:e.title,style:C,onDragStart:u=>{u.dataTransfer.setData("text/plain",e.id),u.dataTransfer.effectAllowed="move",n(e.id)},onDragEnd:a,onClick:d,onContextMenu:y?u=>{u.preventDefault(),u.stopPropagation(),y()}:void 0,children:[t?qt("time",{className:"aethercal-event-time",children:t}):null,qt("span",{className:"aethercal-event-title",children:e.title})]})}import{Fragment as $n,jsx as he,jsxs as rt}from"react/jsx-runtime";var Qt=new Set,Le=7,en=6;function tn(...e){return e.filter(Boolean).join(" ")}function Nn(e){let t=[];for(let n=0;n<e.length;n+=Le)t.push(e.slice(n,n+Le));return t}function Gn(e){let t=new Map;for(let n of e){let a=re(n.start),r=t.get(a);r?r.push(n):t.set(a,[n])}return t}function _n(e){return{start:`${e}T00:00:00`,end:`${pe(e,1)}T00:00:00`,allDay:!0}}function nn(e){let{events:t,anchor:n,locale:a,firstDayOfWeek:r,messages:i,weekdayLabels:d,maxEventsPerDay:y,themeVars:c,onEventDrop:l,onRangeSelect:h,onEventClick:T,onContextMenu:g,pendingIds:C=Qt,rolledBackIds:D=Qt}=e,u=P.useMemo(()=>pt(n,r),[n,r]),x=P.useMemo(()=>Nn(u),[u]),_=P.useMemo(()=>d??Vt(a,r),[d,a,r]),G=P.useMemo(()=>Gn(t),[t]),$=n.getMonth(),K=re(H(new Date)),A=P.useMemo(()=>re(H(n)),[n]),[Y,I]=P.useReducer(ht,We),[X,ye]=P.useState(()=>new Set),oe=P.useId(),[F,de]=P.useState(A),[j,Z]=P.useState(null),[S,te]=P.useState(null),[ke,ge]=P.useState("");P.useEffect(()=>{u.includes(F)||(de(A),Z(null),te(null))},[u,F,A]);let me=P.useCallback(M=>!!T||M.editable!==!1&&!!l,[T,l]);P.useEffect(()=>{let M=new Set((G.get(F)??[]).filter(E=>me(E)).map(E=>E.id));S&&!M.has(S.eventId)?(te(null),Z(null)):!S&&j!==null&&!M.has(j)&&Z(null)},[G,F,j,S,me]);let le=M=>`${oe}-c-${M}`,Re=(M,E)=>`${oe}-e-${M}-${E}`,V=`${oe}-hint`,L=S?Re(F,S.eventId):j?Re(F,j):le(F),De=P.useCallback(M=>{ye(E=>{let k=new Set(E);return k.add(M),k})},[]),q=P.useCallback(M=>E=>{if(E.preventDefault(),!Je(Y)){I({type:"DROP"});return}let k=Y.eventId,Q=E.dataTransfer.getData("text/plain");if(I({type:"DROP"}),Q&&Q!==k||!l)return;let B=t.find(U=>U.id===k);!B||B.editable===!1||l(Pe(B,M))},[Y,t,l]),W=!!l,N=P.useCallback(M=>{if(!S)return;let E=pe(S.targetDate,M),k=u[0],Q=u[u.length-1];E<k||E>Q||(ge(i.movedTo(Be(E,a))),te({...S,targetDate:E,moved:!0}))},[S,u,a,i]),ce=P.useCallback(()=>{if(!S)return;if(!S.moved){Z(S.eventId),te(null);return}let M=t.find(E=>E.id===S.eventId);M&&M.editable!==!1&&l&&(l(Pe(M,S.targetDate)),ge(i.dropped(Be(S.targetDate,a)))),de(S.targetDate),Z(null),te(null)},[S,t,l,i,a]),Ie={ArrowLeft:-1,ArrowRight:1,ArrowUp:-Le,ArrowDown:Le},fe=P.useCallback(M=>{let{key:E}=M,k=E==="Enter"||E===" "||E==="Spacebar";if(S){if(E in Ie){M.preventDefault(),N(Ie[E]);return}if(k){M.preventDefault(),ce();return}if(E==="Escape"){M.preventDefault(),te(null),ge(i.cancelled);return}return}let Q=G.get(F)??[],B=Q.filter(U=>me(U));if(j){let U=B.findIndex(O=>O.id===j);if(E==="ArrowDown"){M.preventDefault(),U>=0&&U<B.length-1&&Z(B[U+1].id);return}if(E==="ArrowUp"){M.preventDefault(),U>0?Z(B[U-1].id):Z(null);return}if(k){M.preventDefault();let O=B.find(Ne=>Ne.id===j);if(!O)return;O.editable!==!1&&l?(te({eventId:O.id,targetDate:F,moved:!1}),ge(i.grabbedMoveHint(O.title))):T&&T({id:O.id});return}if(E==="Escape"){M.preventDefault(),Z(null);return}if(E==="ArrowLeft"||E==="ArrowRight"||E==="Home"||E==="End"){M.preventDefault(),Z(null);let O=Me(u.indexOf(F),E,en,Le);de(u[O]);return}return}if(E in Ie||E==="Home"||E==="End"){M.preventDefault();let U=Me(u.indexOf(F),E,en,Le);de(u[U]);return}k&&(B.length>0?(M.preventDefault(),De(F),Z(B[0].id)):Q.length===0&&h&&(M.preventDefault(),h(_n(F)),ge(i.createHere(Be(F,a)))))},[S,j,F,u,G,me,l,T,h,N,ce,De,i,a,Ie]);return rt($n,{children:[rt("div",{className:tn("aethercal-calendar",Je(Y)&&"is-dragging"),role:"grid","aria-label":Wt(n,a),"aria-describedby":V,"aria-activedescendant":L,tabIndex:0,"data-view":"month",style:c,onKeyDown:fe,children:[he("div",{className:"aethercal-weekdays",role:"row",children:_.map((M,E)=>he("div",{role:"columnheader",className:"aethercal-weekday",children:M},E))}),x.map((M,E)=>he("div",{className:"aethercal-week",role:"row",children:M.map(k=>{let Q=G.get(k)??[],B=X.has(k),U=B?Q:Q.slice(0,y),O=Q.length-U.length,Ne=new Date(`${k}T00:00:00`).getMonth()!==$,Ke=k===K,Ve=!j&&!S&&k===F,ct=S?.targetDate===k;return rt("div",{id:le(k),role:"gridcell",className:tn("aethercal-day",Ne&&"is-outside",Ke&&"is-today",Ve&&"is-active",ct&&"is-drop-target"),"data-date":k,"aria-label":Be(k,a),onDragOver:W?z=>z.preventDefault():void 0,onDrop:W?q(k):void 0,onContextMenu:g?z=>{z.target.closest("[data-event-id], button")||(z.preventDefault(),g({start:`${k}T00:00:00`}))}:void 0,children:[he("div",{className:"aethercal-day-head",children:he("span",{className:"aethercal-day-number",children:Number(k.slice(-2))})}),rt("div",{className:"aethercal-day-events",children:[U.map(z=>{let ut=S?.eventId===z.id||!S&&j===z.id;return he(at,{id:Re(k,z.id),event:z,interactive:me(z),isActive:ut,isGrabbed:S?.eventId===z.id,timeLabel:z.allDay?null:se(z.start,a),onDragStart:o=>I({type:"DRAG_START",eventId:o}),onDragEnd:()=>I({type:"DRAG_CANCEL"}),isPending:C.has(z.id),isRolledBack:D.has(z.id),...T?{onClick:()=>T({id:z.id})}:{},...g?{onContextMenu:()=>g({id:z.id})}:{}},z.id)}),O>0&&!B?he("button",{type:"button",className:"aethercal-more",onClick:()=>De(k),children:i.more(O)}):null]})]},k)})},E))]}),he(nt,{id:V,text:i.keyboardHint}),he(tt,{message:ke})]})}var an={light:{"--ac-fg":"#1f2328","--ac-muted":"#6b7280","--ac-faint":"#9ca3af","--ac-bg":"#ffffff","--ac-header-fg":"#4b5563","--ac-border":"#e5e7eb","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#fafafa","--ac-today-marker-bg":"#111827","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#eef1f4","--ac-event-fg":"#1f2328","--ac-event-accent":"#64748b","--ac-more-fg":"#4b5563","--ac-focus":"#2563eb","--ac-rollback":"#b91c1c","--ac-tg-now":"#dc2626"},dark:{"--ac-fg":"#e6e8eb","--ac-muted":"#9aa1ab","--ac-faint":"#6b7280","--ac-bg":"#14161a","--ac-header-fg":"#b3b9c2","--ac-border":"#2a2e35","--ac-cell-bg":"#171a1f","--ac-cell-bg-outside":"#111318","--ac-today-marker-bg":"#e6e8eb","--ac-today-marker-fg":"#14161a","--ac-event-bg":"#242a32","--ac-event-fg":"#e6e8eb","--ac-event-accent":"#8b98a9","--ac-more-fg":"#b3b9c2","--ac-focus":"#6ea8fe","--ac-rollback":"#f87171","--ac-tg-now":"#f87171"},midnight:{"--ac-fg":"#dfe4ea","--ac-muted":"#8b95a1","--ac-faint":"#5b6675","--ac-bg":"#0b0f14","--ac-header-fg":"#a7b0bd","--ac-border":"#1c232c","--ac-cell-bg":"#0e131a","--ac-cell-bg-outside":"#090d12","--ac-today-marker-bg":"#dfe4ea","--ac-today-marker-fg":"#0b0f14","--ac-event-bg":"#17212c","--ac-event-fg":"#dfe4ea","--ac-event-accent":"#7f8ea3","--ac-more-fg":"#a7b0bd","--ac-focus":"#74a9ff","--ac-rollback":"#fb7185","--ac-tg-now":"#fb7185"},high_contrast:{"--ac-fg":"#000000","--ac-muted":"#000000","--ac-faint":"#1a1a1a","--ac-bg":"#ffffff","--ac-header-fg":"#000000","--ac-border":"#000000","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#ffffff","--ac-today-marker-bg":"#000000","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#e0e0e0","--ac-event-fg":"#000000","--ac-event-accent":"#000000","--ac-more-fg":"#000000","--ac-focus":"#0033cc","--ac-rollback":"#b00000","--ac-tg-now":"#d00000"}};var it=an,rn=["light","dark","midnight","high_contrast"],Hn=new Set(rn),Bn={"--ac-font":'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',"--ac-radius":"8px","--ac-cell-min-height":"96px"},Un={"--ac-tg-gutter":"56px","--ac-tg-body-height":"640px","--ac-tg-hour-min-height":"44px","--ac-tg-line":"var(--ac-border)","--ac-tg-event-bg":"var(--ac-event-bg)","--ac-tg-event-fg":"var(--ac-event-fg)","--ac-tg-event-accent":"var(--ac-event-accent)"},on=["--ac-tg-now"],Yn=/[;{}<>]/;function sn(e){return typeof e=="string"&&Hn.has(e)}function dn(e){return Object.entries(e).map(([t,n])=>`  ${t}: ${n};`).join(`
`)}function Kn(){let e={};for(let[t,n]of Object.entries(it.light))on.includes(t)||(e[t]=n);return e}function Vn(){let e={};for(let t of on){let n=it.light[t];n!==void 0&&(e[t]=n)}return e}function Pt(){return dn({...Bn,...Kn()})}function Lt(){return dn({...Un,...Vn()})}function Wn(e){let t={};for(let[n,a]of Object.entries(e))n.startsWith("--ac-")&&(typeof a!="string"||a.trim()===""||Yn.test(a)||(t[n]=a));return t}function Ft(e){return e===void 0?{}:typeof e=="string"?sn(e)?{...it[e]}:{}:Wn(e)}var ln="aethercal-calendar-styles",cn=`
:where(.aethercal-calendar) {
${Pt()}
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
`;function Ye(){if(typeof document>"u"||document.getElementById(ln))return;let e=document.createElement("style");e.id=ln,e.textContent=cn,document.head.appendChild(e)}import*as w from"react";function Fe(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(v(e))}function un(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function gn(e,t){if(e.length===0)return"";let n=v(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(n);let a=v(e[e.length-1]),r=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${r.format(n)} \u2013 ${r.format(a)}`}var mn="aethercal-timegrid-styles",fn=`
:where(.aethercal-timegrid) {
${Lt()}
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
`;function Nt(){if(typeof document>"u"||document.getElementById(mn))return;let e=document.createElement("style");e.id=mn,e.textContent=fn,document.head.appendChild(e)}import{Fragment as vn,jsx as J,jsxs as xe}from"react/jsx-runtime";function ot(...e){return e.filter(Boolean).join(" ")}var be=e=>`${e*100}%`,pn=new Set;function yn(e){let t=v(e);return t.getHours()*60+t.getMinutes()}function Gt(e,t,n){let a=v(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}function Jn(e,t,n,a){let{event:r,isContinuation:i,continuesAfter:d}=e;return i?d?n:a(se(r.end,t)):se(r.start,t)}function st(e,t){let n=t.getBoundingClientRect();return n.height>0?(e-n.top)/n.height:0}function _t(e){let{view:t,days:n,events:a,locale:r,config:i,now:d,themeVars:y,onEventDrop:c,onEventResize:l,onRangeSelect:h,onEventClick:T,onContextMenu:g,pendingIds:C=pn,rolledBackIds:D=pn}=e,u=w.useMemo(()=>{if(e.messages)return e.messages;let o={...e.allDayLabel!==void 0?{allDay:e.allDayLabel}:{},...e.continuesLabel!==void 0?{continues:e.continuesLabel}:{},...e.formatEndsLabel!==void 0?{endsAt:e.formatEndsLabel}:{}};return Ue(r,o)},[e.messages,e.allDayLabel,e.continuesLabel,e.formatEndsLabel,r]);w.useEffect(()=>{Ye(),Nt()},[]);let x=w.useMemo(()=>kt(n,a,i),[n,a,i]),_=w.useMemo(()=>It(d,i),[d,i]),G=w.useMemo(()=>re(H(d)),[d]),[$,K]=w.useReducer(bt,Xe),A=w.useRef(null),[Y,I]=w.useState(null),[X,ye]=w.useState(null),oe=!!c,F=!!l,de=!!h,j=$.status==="dragging",Z=w.useCallback((o,s)=>m=>{if(m.preventDefault(),$.status!=="dragging"){K({type:"COMMIT"});return}let R=$.eventId,b=m.dataTransfer.getData("text/plain");if(K({type:"COMMIT"}),b&&b!==R||!c)return;let p=a.find(ne=>ne.id===R);if(!p||p.editable===!1)return;let f=null;if(s&&p.allDay!==!0){let ee=m.currentTarget.getBoundingClientRect();ee.height>0&&Number.isFinite(m.clientY)&&(f=Se((m.clientY-ee.top)/ee.height,x.config))}c(qe(p,o,f))},[$,a,c,x.config]),S=w.useCallback(o=>{A.current?.kind!=="resize"&&K({type:"DRAG_START",eventId:o})},[]),te=w.useCallback(()=>K({type:"CANCEL"}),[]),ke=w.useCallback((o,s)=>m=>{if(!l||o.editable===!1||m.button!==0||A.current)return;let R=m.currentTarget.closest(".aethercal-tg-col");R?.dataset.date&&(m.preventDefault(),m.stopPropagation(),A.current={kind:"resize",pointerId:m.pointerId,eventId:o.id,edge:s,dateOnly:R.dataset.date,colEl:R,payload:null},m.currentTarget.setPointerCapture?.(m.pointerId),K({type:"RESIZE_START",eventId:o.id,edge:s}))},[l]),ge=w.useCallback(o=>s=>{if(!h||s.button!==0||A.current||s.target.closest("[data-event-id], button"))return;let m=s.currentTarget,R=Se(st(s.clientY,m),x.config);A.current={kind:"select",pointerId:s.pointerId,anchorDate:o,anchorCol:m,anchorMinute:R,currentDate:o,currentCol:m,currentMinute:R},m.setPointerCapture?.(s.pointerId),K({type:"SELECT_START",point:{dateOnly:o,minuteOfDay:R}})},[h,x.config]),me=$.status==="resizing"||$.status==="selecting";w.useLayoutEffect(()=>{if(!me)return;let o=p=>{let f=A.current;if(!(!f||p.pointerId!==f.pointerId))if(f.kind==="resize"){let ne=document.elementFromPoint(p.clientX,p.clientY)?.closest(".aethercal-tg-col"),ee=ne?.dataset.date?ne:f.colEl,Ae=Se(st(p.clientY,ee),x.config),Ge=a.find(gt=>gt.id===f.eventId);if(!Ge)return;let Ee=$e(Ge,f.edge,ee.dataset.date??f.dateOnly,Ae);f.payload=Ee,I(Ee)}else{let ne=document.elementFromPoint(p.clientX,p.clientY)?.closest(".aethercal-tg-col"),ee=ne?.dataset.date?ne:f.currentCol;f.currentCol=ee,f.currentDate=ee.dataset.date??f.anchorDate,f.currentMinute=Se(st(p.clientY,ee),x.config);let Ae=ze({dateOnly:f.anchorDate,minuteOfDay:f.anchorMinute},{dateOnly:f.currentDate,minuteOfDay:f.currentMinute}),Ee=(f.currentDate===f.anchorDate?He([{id:"__sel",title:"",start:Ae.start,end:Ae.end}],f.anchorDate,x.config):[])[0];ye(Ee?{dateOnly:f.anchorDate,topFraction:Ee.topFraction,heightFraction:Ee.heightFraction}:null)}},s=p=>{let f=A.current;A.current=null,I(null),ye(null),p&&f&&(f.kind==="resize"&&f.payload&&l&&l(f.payload),f.kind==="select"&&h&&(f.currentDate!==f.anchorDate||f.currentMinute!==f.anchorMinute)&&h(ze({dateOnly:f.anchorDate,minuteOfDay:f.anchorMinute},{dateOnly:f.currentDate,minuteOfDay:f.currentMinute}))),K({type:p?"COMMIT":"CANCEL"})},m=p=>{A.current&&p.pointerId!==A.current.pointerId||s(!0)},R=p=>{A.current&&p.pointerId!==A.current.pointerId||s(!1)},b=p=>{p.key==="Escape"&&s(!1)};return window.addEventListener("pointermove",o),window.addEventListener("pointerup",m),window.addEventListener("pointercancel",R),window.addEventListener("keydown",b),()=>{window.removeEventListener("pointermove",o),window.removeEventListener("pointerup",m),window.removeEventListener("pointercancel",R),window.removeEventListener("keydown",b)}},[me,a,x.config,l,h]);let le=w.useCallback((o,s)=>m=>{if(!g||m.target.closest("[data-event-id], button"))return;if(m.preventDefault(),!s){g({start:`${o}T00:00:00`});return}let R=Se(st(m.clientY,m.currentTarget),x.config),b=v(`${o}T00:00:00`),p=new Date(b.getFullYear(),b.getMonth(),b.getDate(),0,R,0);g({start:H(p)})},[g,x.config]),Re=w.useId(),V=w.useMemo(()=>x.columns.map(o=>o.dateOnly),[x.columns]),[L,De]=w.useState(()=>(V.includes(G)?G:V[0])??""),[q,W]=w.useState(null),[N,ce]=w.useState(null),[Ie,fe]=w.useState("");w.useEffect(()=>{V.includes(L)||(De(V[0]??""),W(null),ce(null))},[V,L]);let M=o=>`${Re}-col-${o}`,E=(o,s)=>`${Re}-e-${o}-${s}`,k=`${Re}-hint`,Q=Ce,B=w.useCallback(o=>!!T||o.editable!==!1&&!!(c||l),[T,c,l]),U=w.useMemo(()=>{let o=x.columns.find(s=>s.dateOnly===L);return o?[...o.allDay,...o.timed.map(s=>s.event)]:[]},[x.columns,L]),O=w.useMemo(()=>U.filter(o=>B(o)),[U,B]);w.useEffect(()=>{let o=new Set(O.map(s=>s.id));N&&!o.has(N.eventId)?(ce(null),W(null)):!N&&q!==null&&!o.has(q)&&W(null)},[O,q,N]);let Ne=N?E(L,N.eventId):q?E(L,q):M(L),Ke=w.useCallback(o=>{let s=N;if(!s)return;let m=s.dateOnly,R=s.minute,b=a.find(f=>f.id===s.eventId),p=b?.allDay===!0;if(!p&&(o==="ArrowUp"||o==="ArrowDown")){let f=xt(m,R,o==="ArrowUp"?-Q:Q,x.config);m=f.dateOnly,R=f.minuteOfDay}else o==="ArrowLeft"?m=pe(m,-1):o==="ArrowRight"&&(m=pe(m,1));if(!(m===s.dateOnly&&R===s.minute)){if(b)if(s.kind==="move")fe(u.movedTo(p?Fe(m,r):`${Fe(m,r)} ${Gt(m,R,r)}`));else{let f=$e(b,"end",m,R);fe(u.resizedTo(`${se(f.start,r)} \u2013 ${se(f.end,r)}`))}ce({...s,dateOnly:m,minute:R,moved:!0})}},[N,Q,x.config,a,u,r]),Ve=w.useCallback(()=>{let o=N;if(!o)return;if(!o.moved){W(o.eventId),ce(null);return}let s=a.find(m=>m.id===o.eventId);if(s&&s.editable!==!1&&o.kind==="move"&&c){let m=qe(s,o.dateOnly,s.allDay===!0?null:o.minute);c(m);let R=re(m.start);De(V.includes(R)?R:L),W(null),fe(u.dropped(s.allDay===!0?Fe(o.dateOnly,r):Gt(o.dateOnly,o.minute,r)))}else if(s&&s.editable!==!1&&o.kind==="resize"&&l){let m=$e(s,"end",o.dateOnly,o.minute);l(m),W(o.eventId),fe(u.resized(`${se(m.start,r)} \u2013 ${se(m.end,r)}`))}else W(o.eventId);ce(null)},[N,a,c,l,V,L,u,r]),ct=w.useCallback(o=>{let{key:s}=o,m=s==="Enter"||s===" "||s==="Spacebar",R=s==="ArrowUp"||s==="ArrowDown"||s==="ArrowLeft"||s==="ArrowRight";if(N){if(R){o.preventDefault(),Ke(s);return}if(m){o.preventDefault(),Ve();return}if(s==="Escape"){o.preventDefault(),ce(null),fe(u.cancelled);return}return}if(q){let b=O.findIndex(p=>p.id===q);if(s==="ArrowDown"){o.preventDefault(),b>=0&&b<O.length-1&&W(O[b+1].id);return}if(s==="ArrowUp"){o.preventDefault(),b>0?W(O[b-1].id):W(null);return}if(s==="ArrowLeft"||s==="ArrowRight"){o.preventDefault(),W(null);let p=V.indexOf(L);De(V[Me(p,s,1,V.length)]);return}if(m){o.preventDefault();let p=O.find(f=>f.id===q);if(!p)return;p.editable!==!1&&c?(ce({kind:"move",eventId:p.id,dateOnly:re(p.start),minute:yn(p.start),moved:!1}),fe(u.grabbedMoveHint(p.title))):T&&T({id:p.id});return}if((s==="r"||s==="R")&&l){o.preventDefault();let p=O.find(f=>f.id===q);p&&p.allDay!==!0&&p.editable!==!1&&(ce({kind:"resize",eventId:p.id,dateOnly:re(p.end),minute:yn(p.end),moved:!1}),fe(u.grabbedResizeHint(p.title)));return}if(s==="Escape"){o.preventDefault(),W(null);return}return}if(s==="ArrowLeft"||s==="ArrowRight"||s==="Home"||s==="End"){o.preventDefault();let b=V.indexOf(L);De(V[Me(b,s,1,V.length)]);return}if(s==="ArrowDown"){O.length>0&&(o.preventDefault(),W(O[0].id));return}if(m){if(O.length>0)o.preventDefault(),W(O[0].id);else if(U.length===0&&h){let b=x.config.dayEndHour*60,p=Ze(x.config.dayStartHour*60,x.config),f=Math.min(p+60,b);f>p&&(o.preventDefault(),h(ze({dateOnly:L,minuteOfDay:p},{dateOnly:L,minuteOfDay:f})),fe(u.createHere(`${Fe(L,r)} ${Gt(L,p,r)}`)))}}},[N,q,L,U,O,V,c,l,T,h,Ke,Ve,x.config,u,r]),z={"--ac-tg-cols":x.columns.length,"--ac-tg-hours":x.config.dayEndHour-x.config.dayStartHour,...y??{}},ut=u.allDay;return xe(vn,{children:[xe("div",{className:ot("aethercal-calendar","aethercal-timegrid",j&&"is-dragging",$.status==="resizing"&&"is-resizing",$.status==="selecting"&&"is-selecting"),role:"grid","aria-label":gn(n,r),"aria-describedby":k,"aria-activedescendant":Ne,tabIndex:0,"data-view":t,style:z,onKeyDown:ct,children:[xe("div",{className:"aethercal-tg-head",role:"row",children:[J("div",{className:"aethercal-tg-corner"}),x.columns.map(o=>J("div",{role:"columnheader",className:ot("aethercal-tg-colhead",o.dateOnly===G&&"is-today"),"data-date":o.dateOnly,children:J("span",{className:"aethercal-tg-colhead-date",children:Fe(o.dateOnly,r)})},o.dateOnly))]}),xe("div",{className:"aethercal-tg-allday",role:"row",children:[J("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:ut}),x.columns.map(o=>J("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":o.dateOnly,onDragOver:oe?s=>s.preventDefault():void 0,onDrop:oe?Z(o.dateOnly,!1):void 0,onContextMenu:g?le(o.dateOnly,!1):void 0,children:o.allDay.map(s=>{let m=N?.eventId===s.id&&o.dateOnly===L||!N&&q===s.id&&o.dateOnly===L;return J(at,{id:E(o.dateOnly,s.id),event:s,interactive:B(s),isActive:m,isGrabbed:N?.eventId===s.id&&o.dateOnly===L,timeLabel:null,onDragStart:S,onDragEnd:te,isPending:C.has(s.id),isRolledBack:D.has(s.id),...T?{onClick:()=>T({id:s.id})}:{},...g?{onContextMenu:()=>g({id:s.id})}:{}},s.id)})},o.dateOnly))]}),xe("div",{className:"aethercal-tg-body",role:"row",children:[J("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:x.hourMarks.map(o=>J("div",{className:"aethercal-tg-hour",style:{top:be(o.topFraction)},children:un(o.hour,r)},o.hour))}),x.columns.map(o=>{let s=!q&&!N&&o.dateOnly===L,m=N?.dateOnly===o.dateOnly;return xe("div",{id:M(o.dateOnly),role:"gridcell",className:ot("aethercal-tg-col",o.dateOnly===G&&"is-today",s&&"is-active",m&&"is-drop-target"),"data-date":o.dateOnly,onDragOver:oe?R=>R.preventDefault():void 0,onDrop:oe?Z(o.dateOnly,!0):void 0,onPointerDown:de?ge(o.dateOnly):void 0,onContextMenu:g?le(o.dateOnly,!0):void 0,children:[x.hourMarks.map(R=>J("div",{className:"aethercal-tg-line",style:{top:be(R.topFraction)},"aria-hidden":"true"},R.hour)),X&&X.dateOnly===o.dateOnly?J("div",{className:"aethercal-tg-select-band",style:{top:be(X.topFraction),height:be(X.heightFraction)},"aria-hidden":"true"}):null,o.timed.map(R=>{let{event:b}=R,p=b.editable!==!1,f=Jn(R,r,u.continues,u.endsAt),ne=Y?.id===b.id?Y:null,ee=ne?He([{...b,start:ne.start,end:ne.end}],o.dateOnly,x.config)[0]:void 0,Ae=ee?ee.topFraction:R.topFraction,Ge=ee?ee.heightFraction:R.heightFraction,Ee=N?.eventId===b.id&&o.dateOnly===L||!N&&q===b.id&&o.dateOnly===L,gt=N?.eventId===b.id&&o.dateOnly===L,hn={top:be(Ae),height:be(Ge),left:be(R.lane/R.laneCount),width:be(1/R.laneCount),...b.color?{"--ac-tg-event-accent":b.color}:{}};return xe("div",{id:E(o.dateOnly,b.id),className:ot("aethercal-tg-event",!p&&"is-locked",C.has(b.id)&&"is-pending",D.has(b.id)&&"is-rolledback",!!ne&&"is-resizing",Ee&&"is-active",gt&&"is-grabbed"),...B(b)?{role:"button"}:{},draggable:p,"data-event-id":b.id,"data-lane":R.lane,"data-lane-count":R.laneCount,"aria-label":`${f} ${b.title}`,title:b.title,style:hn,onDragStart:Oe=>{if(A.current?.kind==="resize"){Oe.preventDefault();return}Oe.dataTransfer.setData("text/plain",b.id),Oe.dataTransfer.effectAllowed="move",S(b.id)},onDragEnd:te,onClick:T?()=>T({id:b.id}):void 0,onContextMenu:g?Oe=>{Oe.preventDefault(),Oe.stopPropagation(),g({id:b.id})}:void 0,children:[J("time",{className:"aethercal-tg-event-time",children:f}),J("span",{className:"aethercal-tg-event-title",children:b.title}),F&&p?xe(vn,{children:[J("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:ke(b,"start")}),J("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:ke(b,"end")})]}):null]},b.id)}),_!==null&&o.dateOnly===G?J("div",{className:"aethercal-now-indicator",style:{top:be(_)},"aria-hidden":"true"}):null]},o.dateOnly)})]})]}),J(nt,{id:k,text:u.keyboardHint}),J(tt,{message:Ie})]})}import{jsx as dt}from"react/jsx-runtime";function Xn(e){return e instanceof Date?e:typeof e=="string"?v(e):new Date}function jn(e){return e instanceof Date?e:typeof e=="string"?v(e):new Date}function lt(e){let{view:t="month",events:n,anchor:a,locale:r="en",theme:i,messages:d,firstDayOfWeek:y=1,maxEventsPerDay:c=3,weekdayLabels:l,formatMore:h,unavailableLabel:T,dayStartHour:g,dayEndHour:C,allDayLabel:D,now:u,continuesLabel:x,formatEndsLabel:_,agendaEmptyLabel:G,onEventDrop:$,onEventResize:K,onRangeSelect:A,onEventClick:Y,onContextMenu:I,pendingIds:X,rolledBackIds:ye}=e;ue.useEffect(()=>{Ye()},[]);let oe=ue.useMemo(()=>Xn(a),[a]),F=ue.useMemo(()=>Ft(i),[i]),de=ue.useMemo(()=>{let le={...D!==void 0?{allDay:D}:{},...x!==void 0?{continues:x}:{},..._!==void 0?{endsAt:_}:{},...G!==void 0?{noEvents:G}:{},...T!==void 0?{unavailable:T}:{},...h!==void 0?{more:h}:{},...d};return Ue(r,le)},[r,D,x,_,G,T,h,d]),[j,Z]=ue.useState(()=>new Date);ue.useEffect(()=>{if(u!==void 0||t!=="week"&&t!=="day")return;let le=setInterval(()=>Z(new Date),6e4);return()=>clearInterval(le)},[u,t]);let S=ue.useMemo(()=>u!==void 0?jn(u):j,[u,j]),te=Number.isInteger(y)&&y>=0&&y<=6?y:1,ke=Number.isInteger(c)&&c>=0?c:3,ge=l&&l.length===7?l:void 0,me=ue.useMemo(()=>({...g!==void 0?{dayStartHour:g}:{},...C!==void 0?{dayEndHour:C}:{}}),[g,C]);if(t==="list")return dt(Xt,{events:n??[],locale:r,messages:de,themeVars:F});if(t==="month")return dt(nn,{events:n??[],anchor:oe,locale:r,messages:de,themeVars:F,firstDayOfWeek:te,maxEventsPerDay:ke,...ge?{weekdayLabels:ge}:{},...$?{onEventDrop:$}:{},...A?{onRangeSelect:A}:{},...Y?{onEventClick:Y}:{},...I?{onContextMenu:I}:{},...X?{pendingIds:X}:{},...ye?{rolledBackIds:ye}:{}});if(t==="week"||t==="day"){let le=t==="week"?ft(oe,te):[re(H(oe))];return dt(_t,{view:t,days:le,events:n??[],locale:r,messages:de,themeVars:F,config:me,now:S,...$?{onEventDrop:$}:{},...K?{onEventResize:K}:{},...A?{onRangeSelect:A}:{},...Y?{onEventClick:Y}:{},...I?{onContextMenu:I}:{},...X?{pendingIds:X}:{},...ye?{rolledBackIds:ye}:{}})}return dt("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,style:F,children:de.unavailable})}var Zn=lt;import*as ie from"react";function qn(){return typeof crypto<"u"&&typeof crypto.randomUUID=="function"?crypto.randomUUID():`cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}var Qn=8e3,ea=900;function $t(e){let{events:t,mutate:n,timeoutMs:a=Qn,rollbackFlashMs:r=ea,generateId:i=qn}=e,[d,y]=ie.useReducer(Mt,Rt),c=ie.useRef(t);c.current=t;let l=ie.useRef(!0),h=ie.useRef(new Map);ie.useEffect(()=>{l.current=!0;let C=h.current;return()=>{l.current=!1;for(let D of C.values())clearTimeout(D);C.clear()}},[]),ie.useEffect(()=>{for(let C of Ct(t,d)){let D=d.overrides[C];y({type:"CLEAR",id:C,...D?{clientMutationId:D.clientMutationId}:{}})}},[t,d]);let T=ie.useCallback((C,D)=>{let u=i(),x=c.current.find(I=>I.id===D.id),_=h.current,G=I=>{let X=_.get(I);X!==void 0&&(clearTimeout(X),_.delete(I))},$=()=>{_.set(`fl:${u}`,setTimeout(()=>{_.delete(`fl:${u}`),l.current&&y({type:"CLEAR",id:D.id,clientMutationId:u})},r))};y({type:"SUBMIT",id:D.id,clientMutationId:u,start:D.start,end:D.end,...x?.revision!==void 0?{baseRevision:x.revision}:{}}),_.set(`to:${u}`,setTimeout(()=>{_.delete(`to:${u}`),l.current&&(y({type:"TIMEOUT",id:D.id,clientMutationId:u}),$())},a));let K=()=>{G(`to:${u}`),l.current&&(y({type:"REJECT",id:D.id,clientMutationId:u}),$())},A={kind:C,clientMutationId:u,payload:{...D,client_mutation_id:u}},Y;try{Y=n(A)}catch(I){Y=Promise.reject(I instanceof Error?I:new Error(String(I)))}Y.then(I=>{if(I.id!==D.id){K();return}G(`to:${u}`),l.current&&y({type:"RESOLVE",id:I.id,clientMutationId:u,start:I.start,end:I.end,revision:I.revision})}).catch(K)},[n,a,r,i]),g=ie.useMemo(()=>wt(t,d),[t,d]);return{events:g.events,pendingIds:g.pendingIds,rolledBackIds:g.rolledBackIds,submit:T}}import{jsx as na}from"react/jsx-runtime";function ta({events:e,mutate:t,timeoutMs:n,rollbackFlashMs:a,generateId:r,...i}){let{events:d,pendingIds:y,rolledBackIds:c,submit:l}=$t({events:e,mutate:t,...n!==void 0?{timeoutMs:n}:{},...a!==void 0?{rollbackFlashMs:a}:{},...r?{generateId:r}:{}});return na(lt,{...i,events:d,pendingIds:y,rolledBackIds:c,onEventDrop:h=>l("drop",h),onEventResize:h=>l("resize",h)})}export{lt as AetherCalendar,cn as CALENDAR_CSS,Ot as DEFAULT_LOCALE_MESSAGES,ta as OptimisticCalendar,it as PRESETS,rn as PRESET_NAMES,fn as TIME_GRID_CSS,_t as TimeGridView,Zn as default,Pt as defaultBaseTokenCss,Lt as defaultTimeGridTokenCss,Ye as ensureCalendarStyles,Nt as ensureTimeGridStyles,sn as isThemePreset,Ue as resolveMessages,Ft as resolveThemeVars,$t as useOptimisticEvents};
