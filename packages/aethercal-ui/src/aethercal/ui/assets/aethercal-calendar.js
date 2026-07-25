function Ee(e){return String(e).padStart(2,"0")}function O(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,n,a,r,i,o,l]=t,s=Number(n),d=Number(a),u=Number(r),g=Number(i??"0"),D=Number(o??"0"),p=Number(l??"0");if(d<1||d>12||u<1||u>31||g>23||D>59||p>59)throw new Error(`out-of-range ISO datetime: ${e}`);let h=new Date(s,d-1,u,g,D,p);if(h.getFullYear()!==s||h.getMonth()!==d-1||h.getDate()!==u)throw new Error(`nonexistent calendar date: ${e}`);return h}function ae(e){return`${e.getFullYear()}-${Ee(e.getMonth()+1)}-${Ee(e.getDate())}T${Ee(e.getHours())}:${Ee(e.getMinutes())}:${Ee(e.getSeconds())}`}function ue(e){let t=O(e);return`${t.getFullYear()}-${Ee(t.getMonth()+1)}-${Ee(t.getDate())}`}function Tn(e){return`${e.getFullYear()}-${Ee(e.getMonth()+1)}-${Ee(e.getDate())}`}function Ve(e){let t=O(e.start),n=O(e.end),a=new Date(t.getFullYear(),t.getMonth(),t.getDate()),r=a;if(n.getTime()>t.getTime()){let i=new Date(n.getTime()-1),o=new Date(i.getFullYear(),i.getMonth(),i.getDate());o.getTime()>a.getTime()&&(r=o)}return{startKey:Tn(a),lastKey:Tn(r)}}function va(e,t){return(e.getDay()-t+7)%7}function Ke(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate());return n.setDate(n.getDate()-va(n,t)),n}function Ht(e,t){return Array.from({length:t},(n,a)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+a);return`${r.getFullYear()}-${Ee(r.getMonth()+1)}-${Ee(r.getDate())}`})}function _t(e,t=1){return Ht(Ke(e,t),7)}function $t(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),1);return Ht(Ke(n,t),42)}function Ut(e,t){return Ht(new Date(e.getFullYear(),e.getMonth(),e.getDate()),t)}function we(e,t){let n=O(`${ue(e)}T00:00:00`),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()+t);return`${a.getFullYear()}-${Ee(a.getMonth()+1)}-${Ee(a.getDate())}`}function Bt(e,t){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()),a=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((a.getTime()-n.getTime())/864e5)}function rt(e,t){let n=O(e.start),a=O(e.end),r=O(t),i=Bt(n,r),o=new Date(n.getFullYear(),n.getMonth(),n.getDate()+i,n.getHours(),n.getMinutes(),n.getSeconds()),l=new Date(a.getFullYear(),a.getMonth(),a.getDate()+i,a.getHours(),a.getMinutes(),a.getSeconds()),s={id:e.id,start:ae(o),end:ae(l)};return e.revision!==void 0&&(s.revision=e.revision),s}var ha=370;function xn(e){return String(e).padStart(2,"0")}function Rn(e){return`${e.getFullYear()}-${xn(e.getMonth()+1)}-${xn(e.getDate())}`}function ya(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function ba(e){let{startKey:t,lastKey:n}=Ve(e),a=[],r=O(t);for(let i=0;i<ha&&Rn(r)<=n;i+=1)a.push(Rn(r)),r=ya(r,1);return{keys:a,startKey:t,lastKey:n}}function Vt(e){let t=new Map;return e.forEach((n,a)=>{let{keys:r,startKey:i,lastKey:o}=ba(n),l=O(n.start).getTime(),s=O(n.end).getTime();for(let d of r){let u={entry:{event:n,isContinuation:d!==i,continuesAfter:d!==o},startMs:l,endMs:s,index:a},g=t.get(d);g?g.push(u):t.set(d,[u])}}),[...t.keys()].sort().map(n=>{let a=t.get(n);return a.sort((r,i)=>r.startMs-i.startMs||r.endMs-i.endMs||r.index-i.index),{date:n,entries:a.map(r=>r.entry)}})}function Ye(e,t,n,a){let r=n*a;if(r<=0)return e;let i=Math.min(Math.max(e,0),r-1),o=i-i%a,l=Math.min(o+a-1,r-1);switch(t){case"ArrowLeft":return i>o?i-1:i;case"ArrowRight":return i<l?i+1:i;case"ArrowUp":{let s=i-a;return s>=0?s:i}case"ArrowDown":{let s=i+a;return s<r?s:i}case"Home":return o;case"End":return l;default:return i}}var We=60,Ce=15;function Yt(e,t,n){return Math.min(n,Math.max(t,e))}function bt(e,t){let n=O(`${e}T00:00:00`);return new Date(n.getFullYear(),n.getMonth(),n.getDate(),0,t,0)}function Wt(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate(),e.getHours(),e.getMinutes()+t,e.getSeconds())}function wt(e,t){return t==null||(e.resourceId=t),e}function Xe(e,t,n=Ce){let a=t.dayStartHour*We,r=t.dayEndHour*We,i=a+Yt(e,0,1)*t.windowMinutes,o=n>0?n:Ce,l=a+Math.round((i-a)/o)*o;return Yt(l,a,r)}function Dt(e,t){return Yt(e,t.dayStartHour*We,t.dayEndHour*We)}var Kt=24*We;function Xt(e,t,n,a){let r=t+n,i=e;for(;r<0;)r+=Kt,i=we(i,-1);for(;r>Kt;)r-=Kt,i=we(i,1);return{dateOnly:i,minuteOfDay:Dt(r,a)}}function Ge(e,t,n,a){if(n===null)return wt(rt(e,t),a);let r=O(e.start),i=O(e.end),o=bt(t,n),l=Bt(r,i),s=r.getHours()*We+r.getMinutes(),u=i.getHours()*We+i.getMinutes()-s,g=new Date(o.getFullYear(),o.getMonth(),o.getDate()+l,o.getHours(),o.getMinutes()+u,0),D={id:e.id,start:ae(o),end:ae(g)};return e.revision!==void 0&&(D.revision=e.revision),wt(D,a)}function Se(e,t,n,a,r={}){let i=r.minDurationMinutes??Ce,o=O(e.start),l=O(e.end),s=bt(n,a),d=o,u=l;if(t==="end"){let D=Wt(o,i);u=s.getTime()>=D.getTime()?s:D}else{let D=Wt(l,-i);d=s.getTime()<=D.getTime()?s:D}let g={id:e.id,start:ae(d),end:ae(u)};return e.revision!==void 0&&(g.revision=e.revision),g}function Pe(e,t,n={}){let a=n.minDurationMinutes??Ce;if(e.minuteOfDay===null||t.minuteOfDay===null){let[u,g]=e.dateOnly<=t.dateOnly?[e.dateOnly,t.dateOnly]:[t.dateOnly,e.dateOnly],D=O(`${u}T00:00:00`),p=O(`${g}T00:00:00`),h=new Date(p.getFullYear(),p.getMonth(),p.getDate()+1),y={start:ae(D),end:ae(h),allDay:!0};return wt(y,e.resourceId)}let i=bt(e.dateOnly,e.minuteOfDay??0),o=bt(t.dateOnly,t.minuteOfDay??0),l=i.getTime()<=o.getTime()?i:o,s=i.getTime()<=o.getTime()?o:i;s.getTime()===l.getTime()&&(s=Wt(l,a));let d={start:ae(l),end:ae(s),allDay:!1};return wt(d,e.resourceId)}var Oe=60,wa=24*Oe,Da=864e5;function Et(e,t,n){return Math.min(n,Math.max(t,e))}function ut(e={}){let t=e.dayStartHour,n=e.dayEndHour,a=Number.isFinite(t)&&t!==void 0?Et(Math.trunc(t),0,23):0,r=Number.isFinite(n)&&n!==void 0?Et(Math.trunc(n),1,24):24,[i,o]=r>a?[a,r]:[0,24];return{dayStartHour:i,dayEndHour:o,windowMinutes:(o-i)*Oe}}function Cn(e){let t=[],n=[];for(let a of e)a.allDay===!0?t.push(a):n.push(a);return{allDay:t,timed:n}}function Je(e,t){let n=O(e),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()),r=Math.round((a.getTime()-t.getTime())/Da),i=n.getHours()*Oe+n.getMinutes()+n.getSeconds()/60;return r*wa+i}function Tt(e,t){let n=e.map(s=>{let[d,u]=t(s);return{item:s,start:d,end:u}});n.sort((s,d)=>s.start!==d.start?s.start-d.start:d.end-s.end);let a=[],r=[],i=[],o=Number.NEGATIVE_INFINITY,l=()=>{let s=r.length;for(let d of i)a[d].laneCount=s;r=[],i=[],o=Number.NEGATIVE_INFINITY};for(let s of n){i.length>0&&s.start>=o&&l();let d=r.findIndex(u=>!(u.start<s.end&&s.start<u.end));d===-1?(d=r.length,r.push({start:s.start,end:s.end})):r[d]={start:s.start,end:s.end},i.push(a.length),a.push({item:s.item,lane:d,laneCount:1}),o=Math.max(o,s.end)}return l(),a}function Mn(e){return Tt(e,t=>[O(t.start).getTime(),O(t.end).getTime()])}function pt(e,t,n){let a=O(`${t}T00:00:00`),r=n.dayStartHour*Oe,i=n.dayEndHour*Oe,o=e.filter(l=>{let s=Je(l.start,a);return!(Je(l.end,a)<=r||s>=i)});return Mn(o).map(({item:l,lane:s,laneCount:d})=>{let u=Je(l.start,a),g=Je(l.end,a),D=Et(u,r,i),p=Et(g,D,i),{startKey:h,lastKey:y}=Ve(l);return{event:l,lane:s,laneCount:d,topFraction:(D-r)/n.windowMinutes,heightFraction:(p-D)/n.windowMinutes,isContinuation:t!==h,continuesAfter:t!==y}})}function Ea(e){let t=[];for(let n=e.dayStartHour;n<e.dayEndHour;n+=1)t.push({hour:n,topFraction:(n-e.dayStartHour)*Oe/e.windowMinutes});return t}function Jt(e,t,n={}){let a="windowMinutes"in n?n:ut(n),{allDay:r,timed:i}=Cn(t),o=i.map(s=>({event:s,startTs:O(s.start).getTime(),endTs:O(s.end).getTime()}));return{columns:e.map(s=>{let d=O(`${s}T00:00:00`),u=d.getTime(),g=new Date(d.getFullYear(),d.getMonth(),d.getDate()+1).getTime(),D=o.filter(h=>h.startTs>=g?!1:h.endTs>u?!0:h.startTs===h.endTs&&h.startTs>=u).map(h=>h.event),p=r.filter(h=>{let{startKey:y,lastKey:C}=Ve(h);return y<=s&&s<=C});return{dateOnly:s,allDay:p,timed:pt(D,s,a)}}),hourMarks:Ea(a),config:a}}function jt(e,t={}){let n="windowMinutes"in t?t:ut(t),a=e.getHours()*Oe+e.getMinutes()+e.getSeconds()/60,r=n.dayStartHour*Oe,i=n.dayEndHour*Oe;return a<r||a>=i?null:(a-r)/n.windowMinutes}var ze=60,xt=7,In=1,kn=31;function gt(e,t,n){return Math.min(n,Math.max(t,e))}function Fe(e){return e===void 0||!Number.isFinite(e)?xt:gt(Math.trunc(e),In,kn)}function Rt(e){return"windowMinutes"in e?e:ut(e)}function Sn(e){if(e.allDay!==!0)return{start:e.start,end:e.end};let{startKey:t,lastKey:n}=Ve(e);return{start:`${t}T00:00:00`,end:`${we(n,1)}T00:00:00`}}function Ta(e,t,n){let a=n.dayStartHour*ze,r=n.dayEndHour*ze,i=[];return t.forEach((o,l)=>{let s=O(`${o}T00:00:00`),d=Je(e.start,s),u=Je(e.end,s);if(u<=a||d>=r)return;let g=gt(d,a,r),D=gt(u,g,r),p=l*n.windowMinutes;i.push({startMin:p+(g-a),endMin:p+(D-a),clippedStart:d<a,clippedEnd:u>r})}),i}function xa(e){let t=[];for(let n of e){let a=t[t.length-1];a&&a.endMin===n.startMin?(a.endMin=n.endMin,a.clippedEnd=n.clippedEnd):t.push({...n})}return t}function An(e,t,n){let a=t.length*n.windowMinutes;if(a<=0)return[];let r=[];for(let o of e){let l=xa(Ta(o,t,n));l.length>0&&r.push({item:o,runs:l})}return Tt(r,o=>[o.runs[0].startMin,o.runs[o.runs.length-1].endMin]).flatMap(({item:o,lane:l,laneCount:s})=>o.runs.map(d=>({event:o.item.event,lane:l,laneCount:s,leftFraction:d.startMin/a,widthFraction:(d.endMin-d.startMin)/a,allDay:o.item.event.allDay===!0,continuesBefore:d.clippedStart,continuesAfter:d.clippedEnd})))}function Zt(e,t,n={}){return An([{event:e,...Sn(e)}],t,Rt(n))}function qt(e,t,n,a={}){let r=Rt(a),i=new Set(a.collapsedGroupIds??[]),o=[],l=new Set;for(let R of e)l.has(R.id)||(l.add(R.id),o.push(R));let s=[],d=new Map;for(let R of o){let L=R.groupId?R.groupId:void 0;if(L===void 0){s.push({kind:"solo",resource:R});continue}let P=d.get(L);P?P.push(R):(d.set(L,[R]),s.push({kind:"group",id:L}))}let u=new Map,g=[];for(let R of t){let L={event:R,...Sn(R)},P=R.resourceId;if(P!==void 0&&l.has(P)){let N=u.get(P);N?N.push(L):u.set(P,[L])}else g.push(L)}let D=(R,L,P)=>{let N=An(P,n,r);return{resource:R,groupId:L,blocks:N,laneCount:N.reduce((w,E)=>Math.max(w,E.laneCount),1)}},p=[];for(let R of s){if(R.kind==="solo"){p.push({kind:"row",row:D(R.resource,null,u.get(R.resource.id)??[])});continue}let L=d.get(R.id)??[],P=i.has(R.id);if(p.push({kind:"group",group:{id:R.id,collapsed:P,resourceCount:L.length}}),!P)for(let N of L)p.push({kind:"row",row:D(N,R.id,u.get(N.id)??[])})}let h=D(null,null,g);h.blocks.length>0&&p.push({kind:"row",row:h});let y=n.length,C=n.map((R,L)=>({dateOnly:R,leftFraction:y>0?L/y:0,widthFraction:y>0?1/y:0})),_=y*r.windowMinutes,Z=[];return _>0&&n.forEach((R,L)=>{let P=L*r.windowMinutes;for(let N=r.dayStartHour;N<r.dayEndHour;N+=1){let w=(N-r.dayStartHour)*ze;Z.push({dateOnly:R,hour:N,leftFraction:(P+w)/_,isDayStart:N===r.dayStartHour})}}),{days:[...n],items:p,dayHeaders:C,ticks:Z,config:r}}function He(e,t,n={},a=Ce){let r=Rt(n);if(t.length===0||r.windowMinutes<=0)return null;let i=t.length*r.windowMinutes,o=gt(e,0,1)*i,l=Math.min(Math.floor(o/r.windowMinutes),t.length-1),s=o-l*r.windowMinutes,d=r.dayStartHour*ze,u=r.dayEndHour*ze,g=a>0?a:Ce,D=d+Math.round(s/g)*g;return{dateOnly:t[l],minuteOfDay:gt(D,d,u)}}function Qt(e,t,n={}){let a=Rt(n),r=t.indexOf(ue(ae(e)));if(r===-1)return null;let i=e.getHours()*ze+e.getMinutes()+e.getSeconds()/60,o=a.dayStartHour*ze,l=a.dayEndHour*ze;if(i<o||i>=l)return null;let s=t.length*a.windowMinutes;return s<=0?null:(r*a.windowMinutes+(i-o))/s}var Ra=1;function je(e,t,n=Ra,a){let r=t.getFullYear(),i=t.getMonth(),o=t.getDate(),l,s;switch(e){case"week":{l=Ke(t,n),s=new Date(l.getFullYear(),l.getMonth(),l.getDate()+7);break}case"day":{l=new Date(r,i,o),s=new Date(r,i,o+1);break}case"timeline":{l=new Date(r,i,o),s=new Date(r,i,o+Fe(a));break}default:{l=new Date(r,i,1),s=new Date(r,i+1,1);break}}return{view:e,from:ae(l),to:ae(s)}}function it(e,t,n,a){let r=e.getFullYear(),i=e.getMonth(),o=e.getDate();switch(t){case"week":return new Date(r,i,o+7*n);case"day":return new Date(r,i,o+n);case"timeline":return new Date(r,i,o+Fe(a)*n);default:return new Date(r,i+n,1)}}var Ct={status:"idle"};function Mt(e){return e.status==="dragging"}function en(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return Ct}}var ot={status:"idle"};function mt(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"RESIZE_START":return{status:"resizing",eventId:t.eventId,edge:t.edge};case"SELECT_START":return{status:"selecting",anchor:t.point,current:t.point};case"SELECT_MOVE":return e.status!=="selecting"?e:{status:"selecting",anchor:e.anchor,current:t.point};case"COMMIT":case"CANCEL":return ot}}var tn={overrides:{},appliedRevision:{}};function Ca(e,t){let n={...e};return delete n[t],n}function nn(e,t){switch(t.type){case"SUBMIT":{let n=t.baseRevision??Number.NEGATIVE_INFINITY,a=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;return{overrides:{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"pending",start:t.start,end:t.end,...t.baseRevision!==void 0?{revision:t.baseRevision}:{},...t.resourceId!==void 0?{resourceId:t.resourceId}:{}}},appliedRevision:{...e.appliedRevision,[t.id]:Math.max(a,n)}}}case"RESOLVE":{let n=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;if(t.revision<=n)return e;let a=e.overrides[t.id],r=a!==void 0&&a.clientMutationId===t.clientMutationId&&a.status==="pending",i=t.resourceId??a?.resourceId;return{overrides:r?{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"committed",start:t.start,end:t.end,revision:t.revision,...i!==void 0?{resourceId:i}:{}}}:e.overrides,appliedRevision:{...e.appliedRevision,[t.id]:t.revision}}}case"REJECT":case"TIMEOUT":{let n=e.overrides[t.id];return!n||n.clientMutationId!==t.clientMutationId||n.status!=="pending"?e:{...e,overrides:{...e.overrides,[t.id]:{...n,status:"rolledback"}}}}case"CLEAR":{let n=e.overrides[t.id];return!n||t.clientMutationId&&n.clientMutationId!==t.clientMutationId?e:{...e,overrides:Ca(e.overrides,t.id)}}}}function an(e,t){let n=new Set,a=new Set,r=o=>o.resourceId!==void 0?{resourceId:o.resourceId}:void 0;return{events:e.map(o=>{let l=t.overrides[o.id];return l?l.status==="pending"?(n.add(o.id),{...o,start:l.start,end:l.end,...r(l)}):l.status==="rolledback"?(a.add(o.id),o):o.revision!==void 0&&l.revision!==void 0&&o.revision>=l.revision?o:{...o,start:l.start,end:l.end,...l.revision!==void 0?{revision:l.revision}:{},...r(l)}:o}),pendingIds:n,rolledBackIds:a}}function rn(e,t){let n=new Map(e.map(r=>[r.id,r])),a=[];for(let[r,i]of Object.entries(t.overrides)){if(i.status!=="committed")continue;let o=n.get(r);o&&o.revision!==void 0&&i.revision!==void 0&&o.revision>=i.revision&&a.push(r)}return a}import*as xe from"react";import*as It from"react";var on=new Date(2023,0,1);function On(e,t){let n=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(a,r)=>{let i=(t+r)%7,o=new Date(on.getFullYear(),on.getMonth(),on.getDate()+i);return n.format(o)})}function sn(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function Pn(e,t,n){let a=new Intl.DateTimeFormat(n,{month:"short",day:"numeric"}).format(e),r=new Intl.DateTimeFormat(n,{month:"short",day:"numeric",year:"numeric"}).format(t);return`${a} \u2013 ${r}`}function Ln(e,t,n,a,r=xt){if(e==="day")return new Intl.DateTimeFormat(n,{dateStyle:"full"}).format(t);if(e==="week"){let i=Ke(t,a),o=new Date(i.getFullYear(),i.getMonth(),i.getDate()+6);return Pn(i,o,n)}if(e==="timeline"){let i=Fe(r),o=new Date(t.getFullYear(),t.getMonth(),t.getDate()),l=new Date(o.getFullYear(),o.getMonth(),o.getDate()+i-1);return i===1?new Intl.DateTimeFormat(n,{dateStyle:"full"}).format(o):Pn(o,l,n)}return sn(t,n)}function ft(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(O(e))}function pe(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(O(e))}function Nn(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(O(e))}import{jsx as _e,jsxs as zn}from"react/jsx-runtime";function Ma(...e){return e.filter(Boolean).join(" ")}function Ia(e,t,n){let{event:a,isContinuation:r,continuesAfter:i}=e;return a.allDay===!0?n.allDay:r?i?n.continues:n.endsAt(pe(a.end,t)):pe(a.start,t)}function ka({entry:e,locale:t,messages:n}){let{event:a,isContinuation:r,continuesAfter:i}=e,o=Ia(e,t,n),l=a.color?{"--ac-event-accent":a.color}:void 0;return zn("li",{className:Ma("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":a.id,"aria-label":`${o} ${a.title}`,style:l,...a.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...i?{"data-continues-after":""}:{},children:[_e("span",{className:"aethercal-agenda-event-time",children:o}),_e("span",{className:"aethercal-agenda-event-title",children:a.title})]})}function Gn({events:e,locale:t,messages:n,themeVars:a}){let r=It.useMemo(()=>Vt(e),[e]),i=It.useId();return r.length===0?_e("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:_e("p",{className:"aethercal-agenda-empty",children:n.noEvents})}):_e("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:r.map(o=>{let l=`${i}-${o.date}`;return zn("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":l,"data-date":o.date,children:[_e("div",{className:"aethercal-agenda-day-title",id:l,children:Nn(o.date,t)}),_e("ul",{className:"aethercal-agenda-day-events",role:"list",children:o.entries.map((s,d)=>_e(ka,{entry:s,locale:t,messages:n},`${s.event.id}-${d}`))})]},o.date)})})}import{jsx as $e,jsxs as Fn}from"react/jsx-runtime";var Sa=["month","week","day","list","timeline"];function ln({view:e,anchor:t,now:n,locale:a,firstDayOfWeek:r,timelineDays:i,messages:o,showViews:l=!0,onRangeChange:s,onViewChange:d}){let u=p=>{s?.(je(e,p,r,i))},g=p=>it(t,e,p,i),D=Ln(e,t,a,r,i);return Fn("div",{className:"aethercal-nav",role:"toolbar","aria-label":o.navToolbar,children:[Fn("div",{className:"aethercal-nav-group",children:[$e("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":o.navPrevious,onClick:()=>u(g(-1)),children:$e("span",{"aria-hidden":"true",children:"\u2039"})}),$e("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-today",onClick:()=>u(n),children:o.navToday}),$e("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":o.navNext,onClick:()=>u(g(1)),children:$e("span",{"aria-hidden":"true",children:"\u203A"})})]}),$e("span",{className:"aethercal-nav-title","aria-live":"polite",children:D}),l?$e("div",{className:"aethercal-nav-views",children:Sa.map(p=>$e("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-view","aria-pressed":p===e,onClick:()=>d?.(je(p,t,r,i)),children:o.viewNames[p]},p))}):null]})}var Aa={allDay:"All day",continues:"Continues",endsAt:e=>`ends ${e}`,more:e=>`+${e} more`,noEvents:"No events",unavailable:"This view is not available yet.",keyboardHint:"Use the arrow keys to move between days. Press Enter on an event to grab it, the arrow keys to move or resize it, Enter to drop, and Escape to cancel.",grabbedMoveHint:e=>`Grabbed ${e}. Use the arrow keys to move it, Enter to drop, Escape to cancel.`,grabbedResizeHint:e=>`Resizing ${e}. Use the up and down arrow keys to change its duration, Enter to confirm, Escape to cancel.`,movedTo:e=>`Moved to ${e}`,resizedTo:e=>`Duration ${e}`,dropped:e=>`Dropped on ${e}`,resized:e=>`Duration set to ${e}`,createHere:e=>`Create an event on ${e}`,cancelled:"Cancelled",navToolbar:"Calendar navigation",navPrevious:"Previous",navNext:"Next",navToday:"Today",viewNames:{month:"Month",week:"Week",day:"Day",list:"Agenda",timeline:"Timeline"},timelineResources:"Resources",timelineUnassigned:"Unassigned",timelineEmpty:"No resources to show",timelineGroupCount:e=>e===1?"1 resource":`${e} resources`,groupExpanded:e=>`${e} expanded`,groupCollapsed:e=>`${e} collapsed`,timelineKeyboardHint:"Use the up and down arrow keys to move between resources and the left and right arrow keys to move between days. Press Enter on a group to expand or collapse it, or on an event to grab it; then use the left and right arrow keys to change its time, the up and down arrow keys to move it to another resource, Enter to drop it, and Escape to cancel."},Pa={allDay:"Todo el d\xEDa",continues:"Contin\xFAa",endsAt:e=>`termina ${e}`,more:e=>`+${e} m\xE1s`,noEvents:"Sin eventos",unavailable:"Esta vista a\xFAn no est\xE1 disponible.",keyboardHint:"Usa las flechas para moverte entre los d\xEDas. Pulsa Enter sobre un evento para agarrarlo, las flechas para moverlo o cambiar su duraci\xF3n, Enter para soltarlo y Escape para cancelar.",grabbedMoveHint:e=>`Agarraste el evento ${e}. Usa las flechas para moverlo, Enter para soltarlo y Escape para cancelar.`,grabbedResizeHint:e=>`Est\xE1s cambiando la duraci\xF3n de ${e}. Usa las flechas hacia arriba y abajo para ajustarla, Enter para confirmar y Escape para cancelar.`,movedTo:e=>`Movido a ${e}`,resizedTo:e=>`Duraci\xF3n ${e}`,dropped:e=>`Soltado en ${e}`,resized:e=>`Duraci\xF3n establecida en ${e}`,createHere:e=>`Crear un evento en ${e}`,cancelled:"Cancelado",navToolbar:"Navegaci\xF3n del calendario",navPrevious:"Anterior",navNext:"Siguiente",navToday:"Hoy",viewNames:{month:"Mes",week:"Semana",day:"D\xEDa",list:"Agenda",timeline:"Cronograma"},timelineResources:"Recursos",timelineUnassigned:"Sin asignar",timelineEmpty:"No hay recursos para mostrar",timelineGroupCount:e=>e===1?"1 recurso":`${e} recursos`,groupExpanded:e=>`${e} desplegado`,groupCollapsed:e=>`${e} plegado`,timelineKeyboardHint:"Usa las flechas hacia arriba y abajo para moverte entre los recursos, y las flechas izquierda y derecha para moverte entre los d\xEDas. Pulsa Enter sobre un grupo para desplegarlo o plegarlo, o sobre un evento para agarrarlo; luego usa las flechas izquierda y derecha para cambiar su hora, las flechas hacia arriba y abajo para moverlo a otro recurso, Enter para soltarlo y Escape para cancelar."},dn={en:Aa,es:Pa};function Oa(e){return e.toLowerCase().split("-")[0]??""}function Ze(e,t,n=dn){let a=e.toLowerCase(),r=n[a]??n[Oa(e)]??n.en??dn.en;return t?{...r,...t}:r}import*as oe from"react";import{jsx as Hn}from"react/jsx-runtime";function st({message:e}){return Hn("div",{className:"aethercal-sr-only","aria-live":"polite","aria-atomic":"true",children:e})}function lt({id:e,text:t}){return Hn("div",{id:e,className:"aethercal-sr-only",children:t})}import{jsx as _n,jsxs as Na}from"react/jsx-runtime";function La(...e){return e.filter(Boolean).join(" ")}function kt({event:e,timeLabel:t,onDragStart:n,onDragEnd:a,canDrag:r=!0,isPending:i,isRolledBack:o,onClick:l,onContextMenu:s,id:d,interactive:u,isActive:g,isGrabbed:D}){let p=e.editable!==!1,h=p&&r,y=e.color?{"--ac-event-accent":e.color}:void 0,C=t?`${t} ${e.title}`:e.title;return Na("div",{className:La("aethercal-event",!p&&"is-locked",i&&"is-pending",o&&"is-rolledback",g&&"is-active",D&&"is-grabbed"),...d?{id:d}:{},...u?{role:"button"}:{},draggable:h,"data-event-id":e.id,"aria-label":C,title:e.title,style:y,onDragStart:_=>{if(!h){_.preventDefault();return}_.dataTransfer.setData("text/plain",e.id),_.dataTransfer.effectAllowed="move",n(e.id)},onDragEnd:a,onClick:l,onContextMenu:s?_=>{_.preventDefault(),_.stopPropagation(),s()}:void 0,children:[t?_n("time",{className:"aethercal-event-time",children:t}):null,t?" ":null,_n("span",{className:"aethercal-event-title",children:e.title})]})}import{Fragment as Ha,jsx as Ae,jsxs as St}from"react/jsx-runtime";var $n=new Set,dt=7,Un=6;function Bn(...e){return e.filter(Boolean).join(" ")}function Ga(e){let t=[];for(let n=0;n<e.length;n+=dt)t.push(e.slice(n,n+dt));return t}function za(e){let t=new Map;for(let n of e){let a=ue(n.start),r=t.get(a);r?r.push(n):t.set(a,[n])}return t}function Fa(e){return{start:`${e}T00:00:00`,end:`${we(e,1)}T00:00:00`,allDay:!0}}function Vn(e){let{events:t,anchor:n,locale:a,firstDayOfWeek:r,messages:i,weekdayLabels:o,maxEventsPerDay:l,themeVars:s,onEventDrop:d,onRangeSelect:u,onEventClick:g,onContextMenu:D,pendingIds:p=$n,rolledBackIds:h=$n}=e,y=oe.useMemo(()=>$t(n,r),[n,r]),C=oe.useMemo(()=>Ga(y),[y]),_=oe.useMemo(()=>o??On(a,r),[o,a,r]),Z=oe.useMemo(()=>za(t),[t]),R=n.getMonth(),L=ue(ae(new Date)),P=oe.useMemo(()=>ue(ae(n)),[n]),[N,w]=oe.useReducer(en,Ct),[E,G]=oe.useState(()=>new Set),$=oe.useId(),[S,Y]=oe.useState(P),[M,q]=oe.useState(null),[U,re]=oe.useState(null),[W,z]=oe.useState("");oe.useEffect(()=>{y.includes(S)||(Y(P),q(null),re(null))},[y,S,P]);let m=oe.useCallback(V=>!!g||V.editable!==!1&&!!d,[g,d]);oe.useEffect(()=>{let V=new Set((Z.get(S)??[]).filter(f=>m(f)).map(f=>f.id));U&&!V.has(U.eventId)?(re(null),q(null)):!U&&M!==null&&!V.has(M)&&q(null)},[Z,S,M,U,m]);let b=V=>`${$}-c-${V}`,Q=(V,f)=>`${$}-e-${V}-${f}`,ee=`${$}-hint`,T=U?Q(S,U.eventId):M?Q(S,M):b(S),F=oe.useCallback(V=>{G(f=>{let J=new Set(f);return J.add(V),J})},[]),x=oe.useCallback(V=>f=>{if(f.preventDefault(),!Mt(N)){w({type:"DROP"});return}let J=N.eventId,se=f.dataTransfer.getData("text/plain");if(w({type:"DROP"}),se&&se!==J||!d)return;let te=t.find(ie=>ie.id===J);!te||te.editable===!1||d(rt(te,V))},[N,t,d]),B=!!d,X=oe.useCallback(V=>{if(!U)return;let f=we(U.targetDate,V),J=y[0],se=y[y.length-1];f<J||f>se||(z(i.movedTo(ft(f,a))),re({...U,targetDate:f,moved:!0}))},[U,y,a,i]),de=oe.useCallback(()=>{if(!U)return;if(!U.moved){q(U.eventId),re(null);return}let V=t.find(f=>f.id===U.eventId);V&&V.editable!==!1&&d&&(d(rt(V,U.targetDate)),z(i.dropped(ft(U.targetDate,a)))),Y(U.targetDate),q(null),re(null)},[U,t,d,i,a]),ke={ArrowLeft:-1,ArrowRight:1,ArrowUp:-dt,ArrowDown:dt},me=oe.useCallback(V=>{let{key:f}=V,J=f==="Enter"||f===" "||f==="Spacebar";if(U){if(f in ke){V.preventDefault(),X(ke[f]);return}if(J){V.preventDefault(),de();return}if(f==="Escape"){V.preventDefault(),re(null),z(i.cancelled);return}return}let se=Z.get(S)??[],te=se.filter(ie=>m(ie));if(M){let ie=te.findIndex(ne=>ne.id===M);if(f==="ArrowDown"){V.preventDefault(),ie>=0&&ie<te.length-1&&q(te[ie+1].id);return}if(f==="ArrowUp"){V.preventDefault(),ie>0?q(te[ie-1].id):q(null);return}if(J){V.preventDefault();let ne=te.find(tt=>tt.id===M);if(!ne)return;ne.editable!==!1&&d?(re({eventId:ne.id,targetDate:S,moved:!1}),z(i.grabbedMoveHint(ne.title))):g&&g({id:ne.id});return}if(f==="Escape"){V.preventDefault(),q(null);return}if(f==="ArrowLeft"||f==="ArrowRight"||f==="Home"||f==="End"){V.preventDefault(),q(null);let ne=Ye(y.indexOf(S),f,Un,dt);Y(y[ne]);return}return}if(f in ke||f==="Home"||f==="End"){V.preventDefault();let ie=Ye(y.indexOf(S),f,Un,dt);Y(y[ie]);return}J&&(te.length>0?(V.preventDefault(),F(S),q(te[0].id)):se.length===0&&u&&(V.preventDefault(),u(Fa(S)),z(i.createHere(ft(S,a)))))},[U,M,S,y,Z,m,d,g,u,X,de,F,i,a,ke]);return St(Ha,{children:[St("div",{className:Bn("aethercal-calendar",Mt(N)&&"is-dragging"),role:"grid","aria-label":sn(n,a),"aria-describedby":ee,"aria-activedescendant":T,tabIndex:0,"data-view":"month",style:s,onKeyDown:me,children:[Ae("div",{className:"aethercal-weekdays",role:"row",children:_.map((V,f)=>Ae("div",{role:"columnheader",className:"aethercal-weekday",children:V},f))}),C.map((V,f)=>Ae("div",{className:"aethercal-week",role:"row",children:V.map(J=>{let se=Z.get(J)??[],te=E.has(J),ie=te?se:se.slice(0,l),ne=se.length-ie.length,tt=new Date(`${J}T00:00:00`).getMonth()!==R,ht=J===L,yt=!M&&!U&&J===S,Gt=U?.targetDate===J;return St("div",{id:b(J),role:"gridcell",className:Bn("aethercal-day",tt&&"is-outside",ht&&"is-today",yt&&"is-active",Gt&&"is-drop-target"),"data-date":J,onDragOver:B?ce=>ce.preventDefault():void 0,onDrop:B?x(J):void 0,onContextMenu:D?ce=>{ce.target.closest("[data-event-id], button")||(ce.preventDefault(),D({start:`${J}T00:00:00`}))}:void 0,children:[Ae("span",{className:"aethercal-sr-only",children:ft(J,a)}),Ae("div",{className:"aethercal-day-head",children:Ae("span",{className:"aethercal-day-number","aria-hidden":"true",children:Number(J.slice(-2))})}),St("div",{className:"aethercal-day-events",children:[ie.map(ce=>{let zt=U?.eventId===ce.id||!U&&M===ce.id;return Ae(kt,{id:Q(J,ce.id),event:ce,interactive:m(ce),isActive:zt,isGrabbed:U?.eventId===ce.id,timeLabel:ce.allDay?null:pe(ce.start,a),canDrag:B,onDragStart:c=>w({type:"DRAG_START",eventId:c}),onDragEnd:()=>w({type:"DRAG_CANCEL"}),isPending:p.has(ce.id),isRolledBack:h.has(ce.id),...g?{onClick:()=>g({id:ce.id})}:{},...D?{onContextMenu:()=>D({id:ce.id})}:{}},ce.id)}),ne>0&&!te?Ae("button",{type:"button",className:"aethercal-more",onClick:()=>F(J),children:i.more(ne)}):null]})]},J)})},f))]}),Ae(lt,{id:ee,text:i.keyboardHint}),Ae(st,{message:W})]})}var Kn={light:{"--ac-fg":"#1f2328","--ac-muted":"#5f6672","--ac-faint":"#676e79","--ac-bg":"#ffffff","--ac-header-fg":"#4b5563","--ac-border":"#e5e7eb","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#fafafa","--ac-today-marker-bg":"#111827","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#eef1f4","--ac-event-fg":"#1f2328","--ac-event-accent":"#64748b","--ac-more-fg":"#4b5563","--ac-focus":"#2563eb","--ac-rollback":"#b91c1c","--ac-tg-now":"#dc2626"},dark:{"--ac-fg":"#e6e8eb","--ac-muted":"#9aa1ab","--ac-faint":"#868e99","--ac-bg":"#14161a","--ac-header-fg":"#b3b9c2","--ac-border":"#2a2e35","--ac-cell-bg":"#171a1f","--ac-cell-bg-outside":"#111318","--ac-today-marker-bg":"#e6e8eb","--ac-today-marker-fg":"#14161a","--ac-event-bg":"#242a32","--ac-event-fg":"#e6e8eb","--ac-event-accent":"#8b98a9","--ac-more-fg":"#b3b9c2","--ac-focus":"#6ea8fe","--ac-rollback":"#f87171","--ac-tg-now":"#f87171"},midnight:{"--ac-fg":"#dfe4ea","--ac-muted":"#8b95a1","--ac-faint":"#828a95","--ac-bg":"#0b0f14","--ac-header-fg":"#a7b0bd","--ac-border":"#1c232c","--ac-cell-bg":"#0e131a","--ac-cell-bg-outside":"#090d12","--ac-today-marker-bg":"#dfe4ea","--ac-today-marker-fg":"#0b0f14","--ac-event-bg":"#17212c","--ac-event-fg":"#dfe4ea","--ac-event-accent":"#7f8ea3","--ac-more-fg":"#a7b0bd","--ac-focus":"#74a9ff","--ac-rollback":"#fb7185","--ac-tg-now":"#fb7185"},high_contrast:{"--ac-fg":"#000000","--ac-muted":"#000000","--ac-faint":"#1a1a1a","--ac-bg":"#ffffff","--ac-header-fg":"#000000","--ac-border":"#000000","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#ffffff","--ac-today-marker-bg":"#000000","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#e0e0e0","--ac-event-fg":"#000000","--ac-event-accent":"#000000","--ac-more-fg":"#000000","--ac-focus":"#0033cc","--ac-rollback":"#b00000","--ac-tg-now":"#d00000"}};var At=Kn,Yn=["light","dark","midnight","high_contrast"],$a=new Set(Yn),Ua={"--ac-font":'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',"--ac-radius":"8px","--ac-cell-min-height":"96px"},Ba={"--ac-tg-gutter":"56px","--ac-tg-body-height":"640px","--ac-tg-hour-min-height":"44px","--ac-tg-line":"var(--ac-border)","--ac-tg-event-bg":"var(--ac-event-bg)","--ac-tg-event-fg":"var(--ac-event-fg)","--ac-tg-event-accent":"var(--ac-event-accent)"},Va={"--ac-tl-rowhead-width":"168px","--ac-tl-lane-height":"30px","--ac-tl-body-height":"560px","--ac-tl-line":"var(--ac-border)","--ac-tl-event-bg":"var(--ac-event-bg)","--ac-tl-event-fg":"var(--ac-event-fg)","--ac-tl-event-accent":"var(--ac-event-accent)","--ac-tl-group-bg":"var(--ac-cell-bg-outside)","--ac-tl-now":"var(--ac-tg-now)"},Wn=["--ac-tg-now"],Ka=/[;{}<>]/;function Xn(e){return typeof e=="string"&&$a.has(e)}function cn(e){return Object.entries(e).map(([t,n])=>`  ${t}: ${n};`).join(`
`)}function Ya(){let e={};for(let[t,n]of Object.entries(At.light))Wn.includes(t)||(e[t]=n);return e}function Jn(){let e={};for(let t of Wn){let n=At.light[t];n!==void 0&&(e[t]=n)}return e}function un(){return cn({...Ua,...Ya()})}function pn(){return cn({...Ba,...Jn()})}function gn(){return cn({...Va,...Jn()})}function Wa(e){let t={};for(let[n,a]of Object.entries(e))n.startsWith("--ac-")&&(typeof a!="string"||a.trim()===""||Ka.test(a)||(t[n]=a));return t}function mn(e){return e===void 0?{}:typeof e=="string"?Xn(e)?{...At[e]}:{}:Wa(e)}var jn="aethercal-calendar-styles",Zn=`
:where(.aethercal-calendar, .aethercal-calendar-shell) {
${un()}
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
/* Locked (editable:false): de-emphasize only the CHROME, never the text. Dimming the whole chip with
   opacity faded the muted time label below WCAG AA (~3.1:1, finding D-1). Instead the fill blends
   toward the surface (reads as ghosted/locked, and moving AWAY from the text luminance keeps the
   muted time + title >= AA in every preset) and the left accent turns dashed \u2014 a non-color "locked"
   cue (WCAG 1.4.1) distinct from an editable chip's solid bar. */
.aethercal-event.is-locked {
  cursor: default;
  border-left-style: dashed;
  background: color-mix(in srgb, var(--ac-event-bg) 55%, var(--ac-bg));
}
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

/* Swipe viewport (U-02): wraps the active view when the toolbar is on, so a touch drag can page the
   period the same way the toolbar's own prev/next buttons do (useSwipeNavigation). 'pan-y' leaves
   vertical scroll (the time-grid's hour body) native while handing horizontal gestures to the
   recognizer \u2014 the same touch-action split every swipeable-carousel pattern uses. The transient
   'is-swiping-*' nudge is a MICRO cue (transform + opacity only, ~120ms) confirming the gesture was
   caught; it never implies the new period's content, which the host still has to fetch and re-render
   the calendar with.

   ASSUMPTION this decision rests on (H2, hardening pass): none of the five views scroll
   HORIZONTALLY inside this wrapper today (verified against the shipped month/week/day/agenda/
   timeline views \u2014 the timeline's resource rows and the time-grid's hour columns both scroll only
   vertically). 'pan-y' is exactly right for that: native vertical scroll stays completely untouched,
   and every horizontal drag is free for the swipe recognizer. If a FUTURE view (or a host's custom
   content slotted in here) ever needs native horizontal scroll of its own, 'pan-y' will fight it \u2014
   the browser will try to hand that gesture to the recognizer too, and NOT because of anything the
   descendant declares: 'touch-action' is the INTERSECTION of an element's own value and every
   ancestor's, so a child cannot relax a restriction this wrapper already set (Crisol caught an
   earlier draft of this comment claiming a plain descendant override would work \u2014 it will not).
   Supporting that case for real needs a CODE change here, not a CSS override further down: a mode
   that switches THIS selector's own touch-action to 'auto' for the affected instance, and teaches
   useSwipeNavigation to ignore drags starting on that region. Whoever adds horizontal scroll inside
   '.aethercal-swipe-viewport' owns that change \u2014 don't assume the current 'pan-y' still holds. */
.aethercal-swipe-viewport {
  touch-action: pan-y;
}
.aethercal-swipe-viewport.is-swiping-next {
  animation: aethercal-swipe-nudge-next 150ms ease-out;
}
.aethercal-swipe-viewport.is-swiping-prev {
  animation: aethercal-swipe-nudge-prev 150ms ease-out;
}
@keyframes aethercal-swipe-nudge-next {
  0% { transform: translateX(0); opacity: 1; }
  40% { transform: translateX(-6px); opacity: 0.85; }
  100% { transform: translateX(0); opacity: 1; }
}
@keyframes aethercal-swipe-nudge-prev {
  0% { transform: translateX(0); opacity: 1; }
  40% { transform: translateX(6px); opacity: 0.85; }
  100% { transform: translateX(0); opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  /* Same belt-and-suspenders stance as the calendar's own reduced-motion rule below: the swipe
     viewport sits OUTSIDE .aethercal-calendar (it wraps it), so it needs its own opt-out rather
     than inheriting that selector. The gesture still navigates \u2014 only the motion is dropped. */
  .aethercal-swipe-viewport,
  .aethercal-swipe-viewport.is-swiping-next,
  .aethercal-swipe-viewport.is-swiping-prev {
    animation: none;
  }
}

/* Mobile tap targets (U-02.2): the toolbar's compact desktop sizing (~24-26px tall) is comfortable
   for a mouse pointer but under the ~44px WCAG 2.5.5 / iOS HIG minimum for a finger. Scoped to small
   viewports OR a coarse pointer (a touch laptop at desktop width still benefits) so the desktop look
   is completely unchanged. */
@media (max-width: 640px), (pointer: coarse) {
  .aethercal-nav-btn {
    min-height: 44px;
    padding: 4px 14px;
  }
  .aethercal-nav-arrow {
    min-width: 44px;
  }
  .aethercal-more {
    /* U-02.2 set min-height here but not min-width \u2014 a launch audit measured the rendered chip at
       ~37px wide (its "+N" text is short), under the 44px minimum on ITS narrow axis even though
       the tall axis already passed. min-width closes that gap the same way .aethercal-nav-arrow
       already does above. */
    min-height: 44px;
    min-width: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 4px 10px;
  }
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
/* 0.9, not lower: the continuation row's muted event-time must stay >= WCAG AA (4.5:1) on white \u2014
   at 0.8 the whole-row opacity dimmed it to 3.71 (finding M-1's dimmed-state sibling). The "continues
   / ends" label already carries the continuation cue, so the lighter dim loses no information. */
.aethercal-agenda-event.is-continuation { opacity: 0.9; }
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
`;function qe(){if(typeof document>"u"||document.getElementById(jn))return;let e=document.createElement("style");e.id=jn,e.textContent=Zn,document.head.appendChild(e)}import*as j from"react";function Te(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(O(e))}function qn(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function Qn(e,t){if(e.length===0)return"";let n=O(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(n);let a=O(e[e.length-1]),r=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${r.format(n)} \u2013 ${r.format(a)}`}var ea="aethercal-timegrid-styles",ta=`
:where(.aethercal-timegrid) {
${pn()}
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
/* Locked (editable:false): de-emphasize the CHROME only \u2014 same root fix as the month chip (finding
   D-1). opacity dimmed the muted time label below WCAG AA; instead the fill blends toward the
   surface and the accent bar turns dashed (a non-color "locked" cue), keeping the time + title text
   at full AA contrast. The --ac-tg-event-bg token resolves to --ac-event-bg, so the blend matches the
   month chip's. */
.aethercal-tg-event.is-locked {
  cursor: default;
  border-left-style: dashed;
  background: color-mix(in srgb, var(--ac-tg-event-bg) 55%, var(--ac-bg));
}
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
`;function fn(){if(typeof document>"u"||document.getElementById(ea))return;let e=document.createElement("style");e.id=ea,e.textContent=ta,document.head.appendChild(e)}import{Fragment as ra,jsx as fe,jsxs as Ue}from"react/jsx-runtime";function Pt(...e){return e.filter(Boolean).join(" ")}var Le=e=>`${e*100}%`,na=new Set;function aa(e){let t=O(e);return t.getHours()*60+t.getMinutes()}function vn(e,t,n){let a=O(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}function Xa(e,t,n,a){let{event:r,isContinuation:i,continuesAfter:o}=e;return i?o?n:a(pe(r.end,t)):pe(r.start,t)}function Ot(e,t){let n=t.getBoundingClientRect();return n.height>0?(e-n.top)/n.height:0}function hn(e){let{view:t,days:n,events:a,locale:r,config:i,now:o,themeVars:l,onEventDrop:s,onEventResize:d,onRangeSelect:u,onEventClick:g,onContextMenu:D,pendingIds:p=na,rolledBackIds:h=na}=e,y=j.useMemo(()=>{if(e.messages)return e.messages;let c={...e.allDayLabel!==void 0?{allDay:e.allDayLabel}:{},...e.continuesLabel!==void 0?{continues:e.continuesLabel}:{},...e.formatEndsLabel!==void 0?{endsAt:e.formatEndsLabel}:{}};return Ze(r,c)},[e.messages,e.allDayLabel,e.continuesLabel,e.formatEndsLabel,r]);j.useEffect(()=>{qe(),fn()},[]);let C=j.useMemo(()=>Jt(n,a,i),[n,a,i]),_=j.useMemo(()=>jt(o,i),[o,i]),Z=j.useMemo(()=>ue(ae(o)),[o]),[R,L]=j.useReducer(mt,ot),P=j.useRef(null),[N,w]=j.useState(null),[E,G]=j.useState(null),$=!!s,S=!!d,Y=!!u,M=R.status==="dragging",q=j.useCallback((c,v)=>I=>{if(I.preventDefault(),R.status!=="dragging"){L({type:"COMMIT"});return}let K=R.eventId,H=I.dataTransfer.getData("text/plain");if(L({type:"COMMIT"}),H&&H!==K||!s)return;let A=a.find(De=>De.id===K);if(!A||A.editable===!1)return;let k=null;if(v&&A.allDay!==!0){let ye=I.currentTarget.getBoundingClientRect();ye.height>0&&Number.isFinite(I.clientY)&&(k=Xe((I.clientY-ye.top)/ye.height,C.config))}s(Ge(A,c,k))},[R,a,s,C.config]),U=j.useCallback(c=>{P.current?.kind!=="resize"&&L({type:"DRAG_START",eventId:c})},[]),re=j.useCallback(()=>L({type:"CANCEL"}),[]),W=j.useCallback((c,v)=>I=>{if(!d||c.editable===!1||I.button!==0||P.current)return;let K=I.currentTarget.closest(".aethercal-tg-col");K?.dataset.date&&(I.preventDefault(),I.stopPropagation(),P.current={kind:"resize",pointerId:I.pointerId,eventId:c.id,edge:v,dateOnly:K.dataset.date,colEl:K,payload:null},I.currentTarget.setPointerCapture?.(I.pointerId),L({type:"RESIZE_START",eventId:c.id,edge:v}))},[d]),z=j.useCallback(c=>v=>{if(!u||v.button!==0||P.current||v.target.closest("[data-event-id], button"))return;let I=v.currentTarget,K=Xe(Ot(v.clientY,I),C.config);P.current={kind:"select",pointerId:v.pointerId,anchorDate:c,anchorCol:I,anchorMinute:K,currentDate:c,currentCol:I,currentMinute:K},I.setPointerCapture?.(v.pointerId),L({type:"SELECT_START",point:{dateOnly:c,minuteOfDay:K}})},[u,C.config]),m=R.status==="resizing"||R.status==="selecting";j.useLayoutEffect(()=>{if(!m)return;let c=A=>{let k=P.current;if(!(!k||A.pointerId!==k.pointerId))if(k.kind==="resize"){let De=document.elementFromPoint(A.clientX,A.clientY)?.closest(".aethercal-tg-col"),ye=De?.dataset.date?De:k.colEl,nt=Xe(Ot(A.clientY,ye),C.config),ct=a.find(Ft=>Ft.id===k.eventId);if(!ct)return;let Ne=Se(ct,k.edge,ye.dataset.date??k.dateOnly,nt);k.payload=Ne,w(Ne)}else{let De=document.elementFromPoint(A.clientX,A.clientY)?.closest(".aethercal-tg-col"),ye=De?.dataset.date?De:k.currentCol;k.currentCol=ye,k.currentDate=ye.dataset.date??k.anchorDate,k.currentMinute=Xe(Ot(A.clientY,ye),C.config);let nt=Pe({dateOnly:k.anchorDate,minuteOfDay:k.anchorMinute},{dateOnly:k.currentDate,minuteOfDay:k.currentMinute}),Ne=(k.currentDate===k.anchorDate?pt([{id:"__sel",title:"",start:nt.start,end:nt.end}],k.anchorDate,C.config):[])[0];G(Ne?{dateOnly:k.anchorDate,topFraction:Ne.topFraction,heightFraction:Ne.heightFraction}:null)}},v=A=>{let k=P.current;P.current=null,w(null),G(null),A&&k&&(k.kind==="resize"&&k.payload&&d&&d(k.payload),k.kind==="select"&&u&&(k.currentDate!==k.anchorDate||k.currentMinute!==k.anchorMinute)&&u(Pe({dateOnly:k.anchorDate,minuteOfDay:k.anchorMinute},{dateOnly:k.currentDate,minuteOfDay:k.currentMinute}))),L({type:A?"COMMIT":"CANCEL"})},I=A=>{P.current&&A.pointerId!==P.current.pointerId||v(!0)},K=A=>{P.current&&A.pointerId!==P.current.pointerId||v(!1)},H=A=>{A.key==="Escape"&&v(!1)};return window.addEventListener("pointermove",c),window.addEventListener("pointerup",I),window.addEventListener("pointercancel",K),window.addEventListener("keydown",H),()=>{window.removeEventListener("pointermove",c),window.removeEventListener("pointerup",I),window.removeEventListener("pointercancel",K),window.removeEventListener("keydown",H)}},[m,a,C.config,d,u]);let b=j.useCallback((c,v)=>I=>{if(!D||I.target.closest("[data-event-id], button"))return;if(I.preventDefault(),!v){D({start:`${c}T00:00:00`});return}let K=Xe(Ot(I.clientY,I.currentTarget),C.config),H=O(`${c}T00:00:00`),A=new Date(H.getFullYear(),H.getMonth(),H.getDate(),0,K,0);D({start:ae(A)})},[D,C.config]),Q=j.useId(),ee=j.useMemo(()=>C.columns.map(c=>c.dateOnly),[C.columns]),[T,F]=j.useState(()=>(ee.includes(Z)?Z:ee[0])??""),[x,B]=j.useState(null),[X,de]=j.useState(null),[ke,me]=j.useState("");j.useEffect(()=>{ee.includes(T)||(F(ee[0]??""),B(null),de(null))},[ee,T]);let V=c=>`${Q}-col-${c}`,f=(c,v)=>`${Q}-e-${c}-${v}`,J=`${Q}-hint`,se=Ce,te=j.useCallback(c=>!!g||c.editable!==!1&&!!(s||d),[g,s,d]),ie=j.useMemo(()=>{let c=C.columns.find(v=>v.dateOnly===T);return c?[...c.allDay,...c.timed.map(v=>v.event)]:[]},[C.columns,T]),ne=j.useMemo(()=>ie.filter(c=>te(c)),[ie,te]);j.useEffect(()=>{let c=new Set(ne.map(v=>v.id));X&&!c.has(X.eventId)?(de(null),B(null)):!X&&x!==null&&!c.has(x)&&B(null)},[ne,x,X]);let tt=X?f(T,X.eventId):x?f(T,x):V(T),ht=j.useCallback(c=>{let v=X;if(!v)return;let I=v.dateOnly,K=v.minute,H=a.find(k=>k.id===v.eventId),A=H?.allDay===!0;if(!A&&(c==="ArrowUp"||c==="ArrowDown")){let k=Xt(I,K,c==="ArrowUp"?-se:se,C.config);I=k.dateOnly,K=k.minuteOfDay}else c==="ArrowLeft"?I=we(I,-1):c==="ArrowRight"&&(I=we(I,1));if(!(I===v.dateOnly&&K===v.minute)){if(H)if(v.kind==="move")me(y.movedTo(A?Te(I,r):`${Te(I,r)} ${vn(I,K,r)}`));else{let k=Se(H,"end",I,K);me(y.resizedTo(`${pe(k.start,r)} \u2013 ${pe(k.end,r)}`))}de({...v,dateOnly:I,minute:K,moved:!0})}},[X,se,C.config,a,y,r]),yt=j.useCallback(()=>{let c=X;if(!c)return;if(!c.moved){B(c.eventId),de(null);return}let v=a.find(I=>I.id===c.eventId);if(v&&v.editable!==!1&&c.kind==="move"&&s){let I=Ge(v,c.dateOnly,v.allDay===!0?null:c.minute);s(I);let K=ue(I.start);F(ee.includes(K)?K:T),B(null),me(y.dropped(v.allDay===!0?Te(c.dateOnly,r):vn(c.dateOnly,c.minute,r)))}else if(v&&v.editable!==!1&&c.kind==="resize"&&d){let I=Se(v,"end",c.dateOnly,c.minute);d(I),B(c.eventId),me(y.resized(`${pe(I.start,r)} \u2013 ${pe(I.end,r)}`))}else B(c.eventId);de(null)},[X,a,s,d,ee,T,y,r]),Gt=j.useCallback(c=>{let{key:v}=c,I=v==="Enter"||v===" "||v==="Spacebar",K=v==="ArrowUp"||v==="ArrowDown"||v==="ArrowLeft"||v==="ArrowRight";if(X){if(K){c.preventDefault(),ht(v);return}if(I){c.preventDefault(),yt();return}if(v==="Escape"){c.preventDefault(),de(null),me(y.cancelled);return}return}if(x){let H=ne.findIndex(A=>A.id===x);if(v==="ArrowDown"){c.preventDefault(),H>=0&&H<ne.length-1&&B(ne[H+1].id);return}if(v==="ArrowUp"){c.preventDefault(),H>0?B(ne[H-1].id):B(null);return}if(v==="ArrowLeft"||v==="ArrowRight"){c.preventDefault(),B(null);let A=ee.indexOf(T);F(ee[Ye(A,v,1,ee.length)]);return}if(I){c.preventDefault();let A=ne.find(k=>k.id===x);if(!A)return;A.editable!==!1&&s?(de({kind:"move",eventId:A.id,dateOnly:ue(A.start),minute:aa(A.start),moved:!1}),me(y.grabbedMoveHint(A.title))):g&&g({id:A.id});return}if((v==="r"||v==="R")&&d){c.preventDefault();let A=ne.find(k=>k.id===x);A&&A.allDay!==!0&&A.editable!==!1&&(de({kind:"resize",eventId:A.id,dateOnly:ue(A.end),minute:aa(A.end),moved:!1}),me(y.grabbedResizeHint(A.title)));return}if(v==="Escape"){c.preventDefault(),B(null);return}return}if(v==="ArrowLeft"||v==="ArrowRight"||v==="Home"||v==="End"){c.preventDefault();let H=ee.indexOf(T);F(ee[Ye(H,v,1,ee.length)]);return}if(v==="ArrowDown"){ne.length>0&&(c.preventDefault(),B(ne[0].id));return}if(I){if(ne.length>0)c.preventDefault(),B(ne[0].id);else if(ie.length===0&&u){let H=C.config.dayEndHour*60,A=Dt(C.config.dayStartHour*60,C.config),k=Math.min(A+60,H);k>A&&(c.preventDefault(),u(Pe({dateOnly:T,minuteOfDay:A},{dateOnly:T,minuteOfDay:k})),me(y.createHere(`${Te(T,r)} ${vn(T,A,r)}`)))}}},[X,x,T,ie,ne,ee,s,d,g,u,ht,yt,C.config,y,r]),ce={"--ac-tg-cols":C.columns.length,"--ac-tg-hours":C.config.dayEndHour-C.config.dayStartHour,...l??{}},zt=y.allDay;return Ue(ra,{children:[Ue("div",{className:Pt("aethercal-calendar","aethercal-timegrid",M&&"is-dragging",R.status==="resizing"&&"is-resizing",R.status==="selecting"&&"is-selecting"),role:"grid","aria-label":Qn(n,r),"aria-describedby":J,"aria-activedescendant":tt,tabIndex:0,"data-view":t,style:ce,onKeyDown:Gt,children:[Ue("div",{className:"aethercal-tg-head",role:"row",children:[fe("div",{className:"aethercal-tg-corner"}),C.columns.map(c=>fe("div",{role:"columnheader",className:Pt("aethercal-tg-colhead",c.dateOnly===Z&&"is-today"),"data-date":c.dateOnly,children:fe("span",{className:"aethercal-tg-colhead-date",children:Te(c.dateOnly,r)})},c.dateOnly))]}),Ue("div",{className:"aethercal-tg-allday",role:"row",children:[fe("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:zt}),C.columns.map(c=>fe("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":c.dateOnly,onDragOver:$?v=>v.preventDefault():void 0,onDrop:$?q(c.dateOnly,!1):void 0,onContextMenu:D?b(c.dateOnly,!1):void 0,children:c.allDay.map(v=>{let I=X?.eventId===v.id&&c.dateOnly===T||!X&&x===v.id&&c.dateOnly===T;return fe(kt,{id:f(c.dateOnly,v.id),event:v,interactive:te(v),isActive:I,isGrabbed:X?.eventId===v.id&&c.dateOnly===T,timeLabel:null,canDrag:$,onDragStart:U,onDragEnd:re,isPending:p.has(v.id),isRolledBack:h.has(v.id),...g?{onClick:()=>g({id:v.id})}:{},...D?{onContextMenu:()=>D({id:v.id})}:{}},v.id)})},c.dateOnly))]}),Ue("div",{className:"aethercal-tg-body",role:"row",tabIndex:0,children:[fe("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:C.hourMarks.map(c=>fe("div",{className:"aethercal-tg-hour",style:{top:Le(c.topFraction)},children:qn(c.hour,r)},c.hour))}),C.columns.map(c=>{let v=!x&&!X&&c.dateOnly===T,I=X?.dateOnly===c.dateOnly;return Ue("div",{id:V(c.dateOnly),role:"gridcell",className:Pt("aethercal-tg-col",c.dateOnly===Z&&"is-today",v&&"is-active",I&&"is-drop-target"),"data-date":c.dateOnly,onDragOver:$?K=>K.preventDefault():void 0,onDrop:$?q(c.dateOnly,!0):void 0,onPointerDown:Y?z(c.dateOnly):void 0,onContextMenu:D?b(c.dateOnly,!0):void 0,children:[C.hourMarks.map(K=>fe("div",{className:"aethercal-tg-line",style:{top:Le(K.topFraction)},"aria-hidden":"true"},K.hour)),E&&E.dateOnly===c.dateOnly?fe("div",{className:"aethercal-tg-select-band",style:{top:Le(E.topFraction),height:Le(E.heightFraction)},"aria-hidden":"true"}):null,c.timed.map(K=>{let{event:H}=K,A=H.editable!==!1,k=Xa(K,r,y.continues,y.endsAt),De=N?.id===H.id?N:null,ye=De?pt([{...H,start:De.start,end:De.end}],c.dateOnly,C.config)[0]:void 0,nt=ye?ye.topFraction:K.topFraction,ct=ye?ye.heightFraction:K.heightFraction,Ne=X?.eventId===H.id&&c.dateOnly===T||!X&&x===H.id&&c.dateOnly===T,Ft=X?.eventId===H.id&&c.dateOnly===T,fa={top:Le(nt),height:Le(ct),left:Le(K.lane/K.laneCount),width:Le(1/K.laneCount),...H.color?{"--ac-tg-event-accent":H.color}:{}};return Ue("div",{id:f(c.dateOnly,H.id),className:Pt("aethercal-tg-event",!A&&"is-locked",p.has(H.id)&&"is-pending",h.has(H.id)&&"is-rolledback",!!De&&"is-resizing",Ne&&"is-active",Ft&&"is-grabbed"),...te(H)?{role:"button"}:{},draggable:A&&$,"data-event-id":H.id,"data-lane":K.lane,"data-lane-count":K.laneCount,"aria-label":`${k} ${H.title}`,title:H.title,style:fa,onDragStart:at=>{if(!$||P.current?.kind==="resize"){at.preventDefault();return}at.dataTransfer.setData("text/plain",H.id),at.dataTransfer.effectAllowed="move",U(H.id)},onDragEnd:re,onClick:g?()=>g({id:H.id}):void 0,onContextMenu:D?at=>{at.preventDefault(),at.stopPropagation(),D({id:H.id})}:void 0,children:[fe("time",{className:"aethercal-tg-event-time",children:k})," ",fe("span",{className:"aethercal-tg-event-title",children:H.title}),S&&A?Ue(ra,{children:[fe("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:W(H,"start")}),fe("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:W(H,"end")})]}):null]},H.id)}),_!==null&&c.dateOnly===Z?fe("div",{className:"aethercal-now-indicator",style:{top:Le(_)},"aria-hidden":"true"}):null]},c.dateOnly)})]})]}),fe(lt,{id:J,text:y.keyboardHint}),fe(st,{message:ke})]})}import*as le from"react";function Me(...e){return e.filter(Boolean).join(" ")}var Ie=e=>`${e*100}%`,yn=new Set,Ja="unassigned",ia=e=>e.resource?`r:${e.resource.id}`:Ja;function vt(e,t){let n=t.getBoundingClientRect();return n.width>0?(e-n.left)/n.width:0}function bn(e){let t=O(e);return t.getHours()*60+t.getMinutes()}function Lt(e,t,n){let a=O(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}import{Fragment as ja,jsx as ge,jsxs as Qe}from"react/jsx-runtime";function oa(e){let{dayHeaders:t,nowDateKey:n,locale:a,resourcesLabel:r}=e;return Qe("div",{className:"aethercal-tl-head",role:"row",children:[ge("div",{className:"aethercal-tl-corner",role:"columnheader",children:r}),ge("div",{className:"aethercal-tl-days",children:t.map(i=>ge("div",{role:"columnheader",className:Me("aethercal-tl-dayhead",i.dateOnly===n&&"is-today"),"data-date":i.dateOnly,style:{left:Ie(i.leftFraction),width:Ie(i.widthFraction)},children:ge("span",{children:Te(i.dateOnly,a)})},i.dateOnly))})]})}function sa(e){let{group:t,domId:n,isActive:a,countLabel:r,onToggle:i}=e;return ge("div",{role:"row",className:Me("aethercal-tl-group",t.collapsed&&"is-collapsed"),children:ge("div",{className:"aethercal-tl-group-head",role:"rowheader",children:Qe("button",{type:"button",id:n,className:Me("aethercal-tl-group-toggle",a&&"is-active"),"aria-expanded":!t.collapsed,tabIndex:-1,onClick:i,children:[ge("span",{className:"aethercal-tl-caret","aria-hidden":"true",children:"\u25BE"}),ge("span",{children:t.id})," ",ge("span",{className:"aethercal-tl-group-count",children:r})]})})})}function la(e){let{row:t,days:n,config:a,ticks:r,nowFraction:i,locale:o,messages:l,rowDomId:s,evtDomId:d,isRowActive:u,isCurrentRow:g,activeEventId:D,kbGrab:p,isKbTarget:h,selectBand:y,resizePreview:C,pendingIds:_,rolledBackIds:Z,dropEnabled:R,resizeEnabled:L,selectEnabled:P,eventInteractive:N,onDrop:w,onPointerDown:E,onTrackContextMenu:G,beginDrag:$,endDrag:S,startResize:Y,onEventClick:M,onEventContextMenu:q}=e,U={"--ac-tl-lanes":t.laneCount},re=t.resource?.color?{"--ac-tl-row-accent":t.resource.color}:{};return Qe("div",{role:"row",className:Me("aethercal-tl-row",!t.resource&&"is-unassigned"),children:[Qe("div",{id:s,role:"rowheader",className:Me("aethercal-tl-rowhead",u&&"is-active"),style:re,children:[t.resource?.color?ge("span",{className:"aethercal-tl-swatch","aria-hidden":"true"}):null,ge("span",{className:"aethercal-tl-rowhead-title",children:t.resource?t.resource.title:l.timelineUnassigned})]}),Qe("div",{role:"gridcell",className:Me("aethercal-tl-track",h&&"is-drop-target"),"data-resource-id":t.resource?.id??"",style:U,onDragOver:R&&t.resource?W=>W.preventDefault():void 0,onDrop:R&&t.resource?w:void 0,onPointerDown:P&&t.resource?E:void 0,onContextMenu:G,children:[r.map(W=>ge("div",{className:Me("aethercal-tl-line",W.isDayStart&&"is-day-start"),style:{left:Ie(W.leftFraction)},"aria-hidden":"true"},`${W.dateOnly}-${W.hour}`)),y&&y.resourceId===t.resource?.id?ge("div",{className:"aethercal-tl-select-band",style:{left:Ie(y.leftFraction),width:Ie(y.widthFraction)},"aria-hidden":"true"}):null,t.blocks.map(W=>{let{event:z}=W,m=z.editable!==!1,b=C?.id===z.id?C:null,Q=p?.eventId===z.id||!p&&D===z.id&&g,ee=W.allDay?l.allDay:pe(b?.start??z.start,o),T=b?Zt({...z,start:b.start,end:b.end},n,a)[0]:void 0,F={left:Ie(T?.leftFraction??W.leftFraction),width:Ie(T?.widthFraction??W.widthFraction),top:Ie(W.lane/W.laneCount),height:Ie(1/W.laneCount),...z.color?{"--ac-tl-event-accent":z.color}:{}};return Qe("div",{id:d(z.id),className:Me("aethercal-tl-event",W.allDay&&"is-allday",!m&&"is-locked",W.continuesBefore&&"continues-before",W.continuesAfter&&"continues-after",_.has(z.id)&&"is-pending",Z.has(z.id)&&"is-rolledback",!!b&&"is-resizing",Q&&"is-active",p?.eventId===z.id&&"is-grabbed"),...N(z)?{role:"button"}:{},draggable:m&&R,"data-event-id":z.id,"data-lane":W.lane,"aria-label":`${ee} ${z.title}`,title:z.title,style:F,onDragStart:x=>{if(!$(z.id)){x.preventDefault();return}x.dataTransfer.setData("text/plain",z.id),x.dataTransfer.effectAllowed="move"},onDragEnd:S,onClick:M?()=>M(z.id):void 0,onContextMenu:q?x=>{x.preventDefault(),x.stopPropagation(),q(z.id)}:void 0,children:[ge("time",{className:"aethercal-tl-event-time",children:ee})," ",ge("span",{className:"aethercal-tl-event-title",children:z.title}),L&&m&&!W.allDay?Qe(ja,{children:[ge("div",{className:"aethercal-tl-resize-handle aethercal-tl-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:Y(z,"start")}),ge("div",{className:"aethercal-tl-resize-handle aethercal-tl-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:Y(z,"end")})]}):null]},z.id)}),i!==null?ge("div",{className:"aethercal-tl-now",style:{left:Ie(i)},"aria-hidden":"true"}):null]})]})}var da="aethercal-timeline-styles",ca=`
:where(.aethercal-timeline) {
${gn()}
}
.aethercal-timeline { display: flex; flex-direction: column; }
.aethercal-tl-head,
.aethercal-tl-row,
.aethercal-tl-group {
  display: grid;
  grid-template-columns: var(--ac-tl-rowhead-width) minmax(0, 1fr);
}
/* The header row lives INSIDE the scroll container, because that container is the ARIA grid (a single
   tab stop \u2014 and columnheaders must sit inside the grid they head). Sticky keeps it in view while the
   rows scroll under it. */
.aethercal-tl-head {
  position: sticky;
  top: 0;
  z-index: 6;
  background: var(--ac-bg);
  border-bottom: 1px solid var(--ac-border);
}
.aethercal-tl-corner { border-right: 1px solid var(--ac-border); }
.aethercal-tl-days { position: relative; height: 32px; }
.aethercal-tl-dayhead {
  position: absolute;
  top: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  border-left: 1px solid var(--ac-border);
  font-size: 12px;
  font-weight: 600;
  color: var(--ac-header-fg);
  white-space: nowrap;
  overflow: hidden;
}
.aethercal-tl-dayhead:first-child { border-left: none; }
.aethercal-tl-dayhead.is-today { color: var(--ac-fg); }
.aethercal-tl-dayhead.is-today > span {
  display: inline-block;
  padding: 0 6px;
  height: 20px;
  line-height: 20px;
  border-radius: 999px;
  background: var(--ac-today-marker-bg);
  color: var(--ac-today-marker-fg);
}
/* The rows scroll vertically (a timeline can have many resources), so the body is a scroll container
   and must be keyboard-focusable \u2014 axe \`scrollable-region-focusable\`. */
.aethercal-tl-body { overflow-y: auto; max-height: var(--ac-tl-body-height); }
.aethercal-tl-group { background: var(--ac-tl-group-bg); border-bottom: 1px solid var(--ac-border); }
.aethercal-tl-group-head { grid-column: 1 / -1; padding: 0; }
.aethercal-tl-group-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  background: none;
  border: none;
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--ac-fg);
  text-align: left;
  cursor: pointer;
}
.aethercal-tl-group-toggle:focus-visible { outline: 2px solid var(--ac-focus); outline-offset: -2px; }
/* A caret that rotates to encode open/closed. The state itself is carried by aria-expanded; this is
   only its visual echo, and it holds still for anyone who asked for less motion. */
.aethercal-tl-caret { display: inline-block; transition: transform 120ms ease; font-size: 10px; }
.aethercal-tl-group.is-collapsed .aethercal-tl-caret { transform: rotate(-90deg); }
.aethercal-tl-group-count { color: var(--ac-faint); font-weight: 500; }
.aethercal-tl-row { border-bottom: 1px solid var(--ac-border); }
.aethercal-tl-rowhead {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  box-sizing: border-box;
  border-right: 1px solid var(--ac-border);
  font-size: 12px;
  color: var(--ac-fg);
  overflow: hidden;
}
.aethercal-tl-rowhead-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.aethercal-tl-rowhead.is-active { outline: 2px solid var(--ac-focus); outline-offset: -2px; }
/* The unassigned row is a real row, but it is not a resource \u2014 mark it as the exception it is. */
.aethercal-tl-row.is-unassigned .aethercal-tl-rowhead { color: var(--ac-muted); font-style: italic; }
.aethercal-tl-swatch {
  flex: none;
  width: 3px;
  align-self: stretch;
  margin: 2px 0;
  border-radius: 2px;
  background: var(--ac-tl-row-accent, transparent);
}
.aethercal-tl-track {
  position: relative;
  box-sizing: border-box;
  min-height: var(--ac-tl-lane-height);
  height: calc(var(--ac-tl-lanes, 1) * var(--ac-tl-lane-height));
}
.aethercal-tl-track.is-drop-target { outline: 2px dashed var(--ac-focus); outline-offset: -2px; }
.aethercal-tl-line {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 0;
  border-left: 1px solid var(--ac-tl-line);
  pointer-events: none;
}
/* A day boundary reads stronger than an hour tick, so the eye can find the day it wants. */
.aethercal-tl-line.is-day-start { border-left-color: var(--ac-border); }
.aethercal-tl-event {
  position: absolute;
  box-sizing: border-box;
  overflow: hidden;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 1px 6px;
  border-radius: calc(var(--ac-radius) - 4px);
  border-left: 3px solid var(--ac-tl-event-accent);
  background: var(--ac-tl-event-bg);
  color: var(--ac-tl-event-fg);
  font-size: 11px;
  line-height: 1.3;
  white-space: nowrap;
  cursor: grab;
}
.aethercal-tl-event-time { color: var(--ac-muted); font-variant-numeric: tabular-nums; }
.aethercal-tl-event-title { overflow: hidden; text-overflow: ellipsis; font-weight: 500; }
/* An all-day bar spans whole days \u2014 a doubled edge separates it from a timed booking without
   inventing a colour. */
.aethercal-tl-event.is-allday { border-left-style: double; }
/* Clipped at a window edge: square off the cut side so the bar reads as "continues", not "ends". */
.aethercal-tl-event.continues-before {
  border-top-left-radius: 0;
  border-bottom-left-radius: 0;
  border-left-style: dotted;
}
.aethercal-tl-event.continues-after { border-top-right-radius: 0; border-bottom-right-radius: 0; }
/* Locked (editable:false): de-emphasize the CHROME only, never the text \u2014 dimming the label would
   drop it below WCAG AA (the same root fix as the month chip / time-grid block). */
.aethercal-tl-event.is-locked {
  cursor: default;
  border-left-style: dashed;
  background: color-mix(in srgb, var(--ac-tl-event-bg) 55%, var(--ac-bg));
}
.aethercal-tl-event.is-pending { opacity: 0.72; }
.aethercal-tl-event.is-rolledback { animation: aethercal-tl-rollback 900ms ease; }
.aethercal-tl-event.is-active { outline: 2px solid var(--ac-focus); outline-offset: 1px; z-index: 3; }
.aethercal-tl-event.is-grabbed { outline: 2px solid var(--ac-focus); outline-offset: 2px; z-index: 4; }
.aethercal-tl-event.is-resizing { outline: 1px dashed var(--ac-focus); outline-offset: -1px; }
/* Resize handles: thin grab strips on the bar's left/right edges \u2014 the axis is horizontal here, so
   they sit where the time actually runs. Only rendered for an editable event with a wired handler. */
.aethercal-tl-resize-handle {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 7px;
  cursor: ew-resize;
  touch-action: none;
  z-index: 5;
}
.aethercal-tl-resize-handle-start { left: -3px; }
.aethercal-tl-resize-handle-end { right: -3px; }
.aethercal-tl-select-band {
  position: absolute;
  top: 2px;
  bottom: 2px;
  min-width: 2px;
  background: color-mix(in srgb, var(--ac-focus) 16%, transparent);
  border: 1px solid var(--ac-focus);
  border-radius: 4px;
  pointer-events: none;
  z-index: 1;
}
.aethercal-tl-now {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 0;
  border-left: 2px solid var(--ac-tl-now);
  pointer-events: none;
  z-index: 2;
}
/* The empty state is a real row, but it has no resource column to align to \u2014 it spans the full width. */
.aethercal-tl-row-empty { grid-template-columns: minmax(0, 1fr); }
.aethercal-tl-empty { padding: 12px 10px; font-size: 12px; color: var(--ac-muted); }
@keyframes aethercal-tl-rollback {
  0% { outline: 2px solid var(--ac-rollback); outline-offset: 1px; }
  100% { outline: 2px solid transparent; outline-offset: 1px; }
}
/* Respect a user who asked for less motion: no caret spin, no rollback flash. The information is
   still carried by aria-expanded and the live region \u2014 never by the animation alone. */
@media (prefers-reduced-motion: reduce) {
  .aethercal-tl-caret { transition: none; }
  .aethercal-tl-event.is-rolledback { animation: none; outline: 2px solid var(--ac-rollback); }
}
`;function wn(){if(typeof document>"u"||document.getElementById(da))return;let e=document.createElement("style");e.id=da,e.textContent=ca,document.head.appendChild(e)}import*as ve from"react";function ua(e){let{timeline:t,days:n,events:a,dropRows:r,locale:i,messages:o,eventInteractive:l,axisFractionOf:s,toggleGroup:d,announce:u,itemDomId:g,evtDomId:D,onEventDrop:p,onEventResize:h,onRangeSelect:y,onEventClick:C}=e,_=Ce,[Z,R]=ve.useState(0),[L,P]=ve.useState(0),[N,w]=ve.useState(null),[E,G]=ve.useState(null);ve.useEffect(()=>{Z>t.items.length-1&&(R(Math.max(0,t.items.length-1)),w(null),G(null))},[t.items.length,Z]),ve.useEffect(()=>{L>n.length-1&&P(Math.max(0,n.length-1))},[n.length,L]);let $=t.items[Z],S=$?.kind==="row"?$.row:void 0,Y=ve.useMemo(()=>(S?.blocks??[]).map(m=>m.event).filter(m=>l(m)),[S,l]),M=ve.useMemo(()=>{let m=t.dayHeaders[L];if(!m||!S)return[];let b=m.leftFraction,Q=m.leftFraction+m.widthFraction,ee=1e-9;return S.blocks.filter(T=>{let F=T.leftFraction,x=T.leftFraction+T.widthFraction;return x>F?F<Q-ee&&x>b+ee:F>=b-ee&&F<Q-ee}).map(T=>T.event).filter(T=>l(T))},[t.dayHeaders,L,S,l]);ve.useEffect(()=>{let m=new Set(Y.map(b=>b.id));E&&!m.has(E.eventId)?(G(null),w(null)):!E&&N!==null&&!m.has(N)&&w(null)},[Y,N,E]);let q=t.items.length===0?void 0:E?D(E.eventId):N?D(N):g(Z),U=ve.useCallback(m=>r.find(b=>b.resource?.id===m)?.resource?.title??m,[r]),re=ve.useCallback(m=>{let b=E;if(!b)return;let Q=a.find(B=>B.id===b.eventId);if(!Q)return;let ee=Q.allDay===!0,T=b.dateOnly,F=b.minute,x=b.kind==="move"?b.resourceId:"";if(m==="ArrowLeft"||m==="ArrowRight")if(ee)T=we(T,m==="ArrowLeft"?-1:1);else{let B=m==="ArrowLeft"?-_:_,X=He(s(T,F+B),n,t.config,_);if(!X)return;T=X.dateOnly,F=X.minuteOfDay??F}else if(b.kind==="move"&&(m==="ArrowUp"||m==="ArrowDown")){let B=r.findIndex(de=>de.resource?.id===x),X=m==="ArrowUp"?B-1:B+1;if(B===-1||X<0||X>=r.length)return;x=r[X].resource.id}else return;if(!(T===b.dateOnly&&F===b.minute&&(b.kind!=="move"||x===b.resourceId)))if(b.kind==="move"){let B=ee?Te(T,i):`${Te(T,i)} ${Lt(T,F,i)}`;u(o.movedTo(`${U(x)} \xB7 ${B}`)),G({...b,dateOnly:T,minute:F,resourceId:x,moved:!0})}else{let B=Se(Q,"end",T,F);u(o.resizedTo(`${pe(B.start,i)} \u2013 ${pe(B.end,i)}`)),G({...b,dateOnly:T,minute:F,moved:!0})}},[E,a,_,n,t.config,r,s,U,u,o,i]),W=ve.useCallback(()=>{let m=E;if(!m)return;if(!m.moved){w(m.eventId),G(null);return}let b=a.find(Q=>Q.id===m.eventId);if(b&&b.editable!==!1&&m.kind==="move"&&p){let Q=b.allDay===!0?null:m.minute;p(Ge(b,m.dateOnly,Q,m.resourceId)),u(o.dropped(`${U(m.resourceId)} \xB7 ${b.allDay===!0?Te(m.dateOnly,i):Lt(m.dateOnly,m.minute,i)}`)),w(null)}else if(b&&b.editable!==!1&&m.kind==="resize"&&h){let Q=Se(b,"end",m.dateOnly,m.minute);h(Q),u(o.resized(`${pe(Q.start,i)} \u2013 ${pe(Q.end,i)}`)),w(m.eventId)}else w(m.eventId);G(null)},[E,a,p,h,U,u,o,i]),z=ve.useCallback(m=>{let{key:b}=m,Q=b==="Enter"||b===" "||b==="Spacebar",ee=b==="ArrowUp"||b==="ArrowDown"||b==="ArrowLeft"||b==="ArrowRight",T=t.items.length-1;if(E){if(ee){m.preventDefault(),re(b);return}if(Q){m.preventDefault(),W();return}b==="Escape"&&(m.preventDefault(),G(null),u(o.cancelled));return}if(N){let F=Y.findIndex(x=>x.id===N);if(b==="ArrowRight"){m.preventDefault(),F>=0&&F<Y.length-1&&w(Y[F+1].id);return}if(b==="ArrowLeft"){m.preventDefault(),F>0?w(Y[F-1].id):w(null);return}if(b==="ArrowUp"||b==="ArrowDown"){m.preventDefault(),w(null),R(x=>Math.min(Math.max(x+(b==="ArrowUp"?-1:1),0),T));return}if(Q){m.preventDefault();let x=Y.find(B=>B.id===N);if(!x)return;x.editable!==!1&&p&&S?.resource?(G({kind:"move",eventId:x.id,dateOnly:ue(x.start),minute:bn(x.start),resourceId:S.resource.id,moved:!1}),u(o.grabbedMoveHint(x.title))):C&&C({id:x.id});return}if((b==="r"||b==="R")&&h){m.preventDefault();let x=Y.find(B=>B.id===N);x&&x.allDay!==!0&&x.editable!==!1&&(G({kind:"resize",eventId:x.id,dateOnly:ue(x.end),minute:bn(x.end),moved:!1}),u(o.grabbedResizeHint(x.title)));return}b==="Escape"&&(m.preventDefault(),w(null));return}if(b==="ArrowUp"||b==="ArrowDown"){m.preventDefault(),R(F=>Math.min(Math.max(F+(b==="ArrowUp"?-1:1),0),T));return}if(b==="ArrowLeft"||b==="ArrowRight"){m.preventDefault(),P(F=>Math.min(Math.max(F+(b==="ArrowLeft"?-1:1),0),Math.max(0,n.length-1)));return}if(b==="Home"||b==="End"){m.preventDefault(),P(b==="Home"?0:Math.max(0,n.length-1));return}if(Q){if($?.kind==="group"){m.preventDefault(),d($.group.id);return}if(M.length>0){m.preventDefault(),w(M[0].id);return}if(S?.resource&&y&&n.length>0){let F=n[Math.min(L,n.length-1)],x=t.config.dayStartHour*60,B=Math.min(x+60,t.config.dayEndHour*60);B>x&&(m.preventDefault(),y(Pe({dateOnly:F,minuteOfDay:x,resourceId:S.resource.id},{dateOnly:F,minuteOfDay:B,resourceId:S.resource.id})),u(o.createHere(`${S.resource.title} \xB7 ${Te(F,i)} ${Lt(F,x,i)}`)))}}},[E,N,Y,M,$,S,t.items.length,t.config,n,L,p,h,C,y,re,W,d,u,o,i]);return{activeItem:Z,activeEventId:N,kbGrab:E,currentRow:S,activeDescendantId:q,handleKeyDown:z}}import*as he from"react";function pa(e){let{days:t,config:n,events:a,axisFractionOf:r,onEventDrop:i,onEventResize:o,onRangeSelect:l,onContextMenu:s}=e,[d,u]=he.useReducer(mt,ot),g=he.useRef(null),[D,p]=he.useState(null),[h,y]=he.useState(null),C=he.useCallback(w=>E=>{if(E.preventDefault(),d.status!=="dragging"){u({type:"COMMIT"});return}let G=d.eventId,$=E.dataTransfer.getData("text/plain");if(u({type:"COMMIT"}),$&&$!==G||!i||!w.resource)return;let S=a.find(q=>q.id===G);if(!S||S.editable===!1)return;let Y=He(vt(E.clientX,E.currentTarget),t,n);if(!Y)return;let M=S.allDay===!0?null:Y.minuteOfDay;i(Ge(S,Y.dateOnly,M,w.resource.id))},[d,a,i,t,n]),_=he.useCallback(w=>!i||g.current?.kind==="resize"?!1:(u({type:"DRAG_START",eventId:w}),!0),[i]),Z=he.useCallback(()=>u({type:"CANCEL"}),[]),R=he.useCallback((w,E)=>G=>{if(!o||w.editable===!1||G.button!==0||g.current)return;let $=G.currentTarget.closest(".aethercal-tl-track");$&&(G.preventDefault(),G.stopPropagation(),g.current={kind:"resize",pointerId:G.pointerId,eventId:w.id,edge:E,trackEl:$,payload:null},G.currentTarget.setPointerCapture?.(G.pointerId),u({type:"RESIZE_START",eventId:w.id,edge:E}))},[o]),L=he.useCallback(w=>E=>{if(!l||E.button!==0||!w.resource||g.current||E.target.closest("[data-event-id], button"))return;let G=E.currentTarget,$=He(vt(E.clientX,G),t,n);if(!$)return;let S=$.minuteOfDay??0;g.current={kind:"select",pointerId:E.pointerId,resourceId:w.resource.id,trackEl:G,anchorDate:$.dateOnly,anchorMinute:S,currentDate:$.dateOnly,currentMinute:S},G.setPointerCapture?.(E.pointerId),u({type:"SELECT_START",point:{dateOnly:$.dateOnly,minuteOfDay:S,resourceId:w.resource.id}})},[l,t,n]),P=d.status==="resizing"||d.status==="selecting";he.useLayoutEffect(()=>{if(!P)return;let w=Y=>{let M=g.current;if(!M||Y.pointerId!==M.pointerId)return;let q=He(vt(Y.clientX,M.trackEl),t,n);if(!q)return;if(M.kind==="resize"){let W=a.find(m=>m.id===M.eventId);if(!W)return;let z=Se(W,M.edge,q.dateOnly,q.minuteOfDay??0);M.payload=z,p(z);return}M.currentDate=q.dateOnly,M.currentMinute=q.minuteOfDay??0;let U=r(M.anchorDate,M.anchorMinute),re=r(M.currentDate,M.currentMinute);y({resourceId:M.resourceId,leftFraction:Math.min(U,re),widthFraction:Math.abs(re-U)})},E=Y=>{let M=g.current;g.current=null,p(null),y(null),Y&&M&&(M.kind==="resize"&&M.payload&&o&&o(M.payload),M.kind==="select"&&l&&(M.currentDate!==M.anchorDate||M.currentMinute!==M.anchorMinute)&&l(Pe({dateOnly:M.anchorDate,minuteOfDay:M.anchorMinute,resourceId:M.resourceId},{dateOnly:M.currentDate,minuteOfDay:M.currentMinute,resourceId:M.resourceId}))),u({type:Y?"COMMIT":"CANCEL"})},G=Y=>{g.current&&Y.pointerId!==g.current.pointerId||E(!0)},$=Y=>{g.current&&Y.pointerId!==g.current.pointerId||E(!1)},S=Y=>{Y.key==="Escape"&&E(!1)};return window.addEventListener("pointermove",w),window.addEventListener("pointerup",G),window.addEventListener("pointercancel",$),window.addEventListener("keydown",S),()=>{window.removeEventListener("pointermove",w),window.removeEventListener("pointerup",G),window.removeEventListener("pointercancel",$),window.removeEventListener("keydown",S)}},[P,a,t,n,r,o,l]);let N=he.useCallback(w=>{if(!s||w.target.closest("[data-event-id], button"))return;let E=He(vt(w.clientX,w.currentTarget),t,n);if(!E)return;w.preventDefault();let G=O(`${E.dateOnly}T00:00:00`),$=new Date(G.getFullYear(),G.getMonth(),G.getDate(),0,E.minuteOfDay??0,0);s({start:ae($)})},[s,t,n]);return{interaction:d,resizePreview:D,selectBand:h,handleDrop:C,beginDrag:_,endDrag:Z,startResize:R,startSelect:L,emptyContextMenu:N}}import{Fragment as Za,jsx as Be,jsxs as ga}from"react/jsx-runtime";function Dn(e){let{days:t,resources:n,events:a,locale:r,config:i,now:o,themeVars:l,defaultCollapsedGroupIds:s,onToggleGroup:d,onEventDrop:u,onEventResize:g,onRangeSelect:D,onEventClick:p,onContextMenu:h,pendingIds:y=yn,rolledBackIds:C=yn}=e,_=le.useMemo(()=>e.messages??Ze(r),[e.messages,r]);le.useEffect(()=>{qe(),wn()},[]);let[Z,R]=le.useState(""),L=le.useCallback(f=>R(f),[]),[P,N]=le.useState(()=>new Set(s??[])),w=le.useMemo(()=>[...P],[P]),E=le.useMemo(()=>qt(n,a,t,{...i,collapsedGroupIds:w}),[n,a,t,i,w]),G=le.useMemo(()=>E.items.flatMap(f=>f.kind==="row"?[f.row]:[]),[E.items]),$=le.useMemo(()=>G.filter(f=>f.resource!==null),[G]),S=le.useMemo(()=>Qt(o,t,E.config),[o,t,E.config]),Y=le.useMemo(()=>ue(ae(o)),[o]),M=!!u,q=!!g,U=!!D,re=le.useCallback((f,J)=>{let{windowMinutes:se,dayStartHour:te}=E.config,ie=t.length*se;if(ie<=0)return 0;let ne=t.indexOf(f);return((ne===-1?0:ne)*se+(J-te*60))/ie},[t,E.config]),W=le.useCallback(f=>{let J=!P.has(f);N(se=>{let te=new Set(se);return te.has(f)?te.delete(f):te.add(f),te}),d?.(f,J),L(J?_.groupCollapsed(f):_.groupExpanded(f))},[P,d,L,_]),z=le.useCallback(f=>!!p||f.editable!==!1&&!!(u||g),[p,u,g]),m=le.useId(),b=`${m}-hint`,Q=le.useCallback(f=>`${m}-i-${f}`,[m]),ee=le.useCallback(f=>`${m}-e-${f}`,[m]),T=pa({days:t,config:E.config,events:a,axisFractionOf:re,...u?{onEventDrop:u}:{},...g?{onEventResize:g}:{},...D?{onRangeSelect:D}:{},...h?{onContextMenu:h}:{}}),F=ua({timeline:E,days:t,events:a,dropRows:$,locale:r,messages:_,eventInteractive:z,axisFractionOf:re,toggleGroup:W,announce:L,itemDomId:Q,evtDomId:ee,...u?{onEventDrop:u}:{},...g?{onEventResize:g}:{},...D?{onRangeSelect:D}:{},...p?{onEventClick:p}:{}}),{interaction:x}=T,{activeItem:B,activeEventId:X,kbGrab:de,currentRow:ke,activeDescendantId:me}=F,V={...l??{}};return ga(Za,{children:[Be("div",{className:Me("aethercal-calendar","aethercal-timeline",x.status==="dragging"&&"is-dragging",x.status==="resizing"&&"is-resizing",x.status==="selecting"&&"is-selecting"),"data-view":"timeline",style:V,children:ga("div",{className:"aethercal-tl-body",role:"grid","aria-label":_.viewNames.timeline,"aria-describedby":b,...me!==void 0?{"aria-activedescendant":me}:{},tabIndex:0,onKeyDown:F.handleKeyDown,children:[Be(oa,{dayHeaders:E.dayHeaders,nowDateKey:Y,locale:r,resourcesLabel:_.timelineResources}),E.items.length===0?Be("div",{className:"aethercal-tl-row aethercal-tl-row-empty",role:"row",children:Be("div",{role:"gridcell",className:"aethercal-tl-empty",children:_.timelineEmpty})}):null,E.items.map((f,J)=>{let se=!X&&!de&&J===B;if(f.kind==="group")return Be(sa,{group:f.group,domId:Q(J),isActive:se,countLabel:_.timelineGroupCount(f.group.resourceCount),onToggle:()=>W(f.group.id)},`g:${f.group.id}`);let{row:te}=f;return Be(la,{row:te,days:t,config:E.config,ticks:E.ticks,nowFraction:S,locale:r,messages:_,rowDomId:Q(J),evtDomId:ee,isRowActive:se,isCurrentRow:ke===te,activeEventId:X,kbGrab:de,isKbTarget:de?.kind==="move"&&te.resource?.id===de.resourceId,selectBand:T.selectBand,resizePreview:T.resizePreview,pendingIds:y,rolledBackIds:C,dropEnabled:M,resizeEnabled:q,selectEnabled:U,eventInteractive:z,onDrop:T.handleDrop(te),onPointerDown:T.startSelect(te),...h?{onTrackContextMenu:T.emptyContextMenu}:{},beginDrag:T.beginDrag,endDrag:T.endDrag,startResize:T.startResize,...p?{onEventClick:ie=>p({id:ie})}:{},...h?{onEventContextMenu:ie=>h({id:ie})}:{}},ia(te))})]})}),Be(lt,{id:b,text:_.timelineKeyboardHint}),Be(st,{message:Z})]})}import*as be from"react";var qa=48,Qa=.5,er=600,tr=150;function nr(e){typeof window>"u"||window.dispatchEvent(new PointerEvent("pointercancel",{pointerId:e,bubbles:!0,cancelable:!0}))}function ma(e){let{enabled:t,onSwipe:n}=e,a=be.useRef(null),[r,i]=be.useState(null),o=be.useRef(void 0),l=be.useRef(t);l.current=t;let s=be.useRef(n);s.current=n,be.useEffect(()=>()=>{o.current!==void 0&&window.clearTimeout(o.current)},[]);let d=be.useCallback(p=>{!l.current||p.pointerType!=="touch"||a.current||(a.current={pointerId:p.pointerId,startX:p.clientX,startY:p.clientY,startTime:p.timeStamp,settled:!1})},[]),u=be.useCallback(p=>{let h=a.current;if(!h||h.settled||p.pointerId!==h.pointerId)return;let y=p.clientX-h.startX,C=p.clientY-h.startY;if(p.timeStamp-h.startTime>er){h.settled=!0;return}if(Math.abs(y)<qa)return;if(Math.abs(C)>Math.abs(y)*Qa){h.settled=!0;return}h.settled=!0;let Z=y<0?"next":"prev";nr(h.pointerId),s.current(Z),i(Z),o.current!==void 0&&window.clearTimeout(o.current),o.current=window.setTimeout(()=>i(null),tr)},[]),g=be.useCallback(p=>{a.current?.pointerId===p.pointerId&&(a.current=null)},[]),D=be.useCallback(p=>g(p),[g]);return be.useEffect(()=>{let p=h=>g(h);return window.addEventListener("pointerup",p),window.addEventListener("pointercancel",p),()=>{window.removeEventListener("pointerup",p),window.removeEventListener("pointercancel",p)}},[g]),{handlers:{onPointerDown:d,onPointerMove:u,onPointerUp:D,onPointerCancel:D},swipeDirection:r}}import{jsx as et,jsxs as sr}from"react/jsx-runtime";function ar(...e){return e.filter(Boolean).join(" ")}function rr(e){if(e instanceof Date)return e;if(typeof e=="string"){let t=e.trim();if(t==="")return new Date;try{return O(t)}catch{return new Date}}return new Date}function ir(e){return e instanceof Date?e:typeof e=="string"?O(e):new Date}function Nt(e){let{view:t="month",events:n,resources:a,timelineDays:r,defaultCollapsedGroupIds:i,onToggleGroup:o,anchor:l,locale:s="en",theme:d,messages:u,firstDayOfWeek:g=1,maxEventsPerDay:D=3,weekdayLabels:p,formatMore:h,unavailableLabel:y,dayStartHour:C,dayEndHour:_,allDayLabel:Z,now:R,continuesLabel:L,formatEndsLabel:P,agendaEmptyLabel:N,onEventDrop:w,onEventResize:E,onRangeSelect:G,onEventClick:$,onContextMenu:S,navigation:Y=!1,navigationViews:M=!0,onRangeChange:q,onViewChange:U,pendingIds:re,rolledBackIds:W}=e;xe.useEffect(()=>{qe()},[]);let z=xe.useMemo(()=>rr(l),[l]),m=xe.useMemo(()=>mn(d),[d]),b=xe.useMemo(()=>{let f={...Z!==void 0?{allDay:Z}:{},...L!==void 0?{continues:L}:{},...P!==void 0?{endsAt:P}:{},...N!==void 0?{noEvents:N}:{},...y!==void 0?{unavailable:y}:{},...h!==void 0?{more:h}:{},...u};return Ze(s,f)},[s,Z,L,P,N,y,h,u]),[Q,ee]=xe.useState(()=>new Date);xe.useEffect(()=>{if(R!==void 0||t!=="week"&&t!=="day"&&t!=="timeline")return;let f=setInterval(()=>ee(new Date),6e4);return()=>clearInterval(f)},[R,t]);let T=xe.useMemo(()=>R!==void 0?ir(R):Q,[R,Q]),F=Number.isInteger(g)&&g>=0&&g<=6?g:1,x=Number.isInteger(D)&&D>=0?D:3,B=p&&p.length===7?p:void 0,X=Fe(r),de=xe.useMemo(()=>({...C!==void 0?{dayStartHour:C}:{},..._!==void 0?{dayEndHour:_}:{}}),[C,_]),ke=xe.useCallback(f=>{if(!q)return;let se=it(z,t,f==="next"?1:-1,X);q(je(t,se,F,X))},[q,z,t,X,F]),me=ma({enabled:Y&&!!q,onSwipe:ke}),V=(()=>{if(t==="list")return et(Gn,{events:n??[],locale:s,messages:b,themeVars:m});if(t==="month")return et(Vn,{events:n??[],anchor:z,locale:s,messages:b,themeVars:m,firstDayOfWeek:F,maxEventsPerDay:x,...B?{weekdayLabels:B}:{},...w?{onEventDrop:w}:{},...G?{onRangeSelect:G}:{},...$?{onEventClick:$}:{},...S?{onContextMenu:S}:{},...re?{pendingIds:re}:{},...W?{rolledBackIds:W}:{}});if(t==="timeline")return et(Dn,{days:Ut(z,X),resources:a??[],events:n??[],locale:s,messages:b,themeVars:m,config:de,now:T,...i?{defaultCollapsedGroupIds:i}:{},...o?{onToggleGroup:o}:{},...w?{onEventDrop:w}:{},...E?{onEventResize:E}:{},...G?{onRangeSelect:G}:{},...$?{onEventClick:$}:{},...S?{onContextMenu:S}:{},...re?{pendingIds:re}:{},...W?{rolledBackIds:W}:{}});if(t==="week"||t==="day"){let f=t==="week"?_t(z,F):[ue(ae(z))];return et(hn,{view:t,days:f,events:n??[],locale:s,messages:b,themeVars:m,config:de,now:T,...w?{onEventDrop:w}:{},...E?{onEventResize:E}:{},...G?{onRangeSelect:G}:{},...$?{onEventClick:$}:{},...S?{onContextMenu:S}:{},...re?{pendingIds:re}:{},...W?{rolledBackIds:W}:{}})}return et("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,style:m,children:b.unavailable})})();return Y?sr("div",{className:"aethercal-calendar-shell",style:m,children:[et(ln,{view:t,anchor:z,now:T,locale:s,firstDayOfWeek:F,timelineDays:X,messages:b,showViews:M,...q?{onRangeChange:q}:{},...U?{onViewChange:U}:{}}),et("div",{className:ar("aethercal-swipe-viewport",me.swipeDirection==="next"&&"is-swiping-next",me.swipeDirection==="prev"&&"is-swiping-prev"),...me.handlers,children:V})]}):V}var or=Nt;import*as Re from"react";function lr(){return typeof crypto<"u"&&typeof crypto.randomUUID=="function"?crypto.randomUUID():`cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}var dr=8e3,cr=900;function En(e){let{events:t,mutate:n,timeoutMs:a=dr,rollbackFlashMs:r=cr,generateId:i=lr}=e,[o,l]=Re.useReducer(nn,tn),s=Re.useRef(t);s.current=t;let d=Re.useRef(!0),u=Re.useRef(new Map);Re.useEffect(()=>{d.current=!0;let p=u.current;return()=>{d.current=!1;for(let h of p.values())clearTimeout(h);p.clear()}},[]),Re.useEffect(()=>{for(let p of rn(t,o)){let h=o.overrides[p];l({type:"CLEAR",id:p,...h?{clientMutationId:h.clientMutationId}:{}})}},[t,o]);let g=Re.useCallback((p,h)=>{let y=i(),C=s.current.find(w=>w.id===h.id),_=u.current,Z=w=>{let E=_.get(w);E!==void 0&&(clearTimeout(E),_.delete(w))},R=()=>{_.set(`fl:${y}`,setTimeout(()=>{_.delete(`fl:${y}`),d.current&&l({type:"CLEAR",id:h.id,clientMutationId:y})},r))};l({type:"SUBMIT",id:h.id,clientMutationId:y,start:h.start,end:h.end,...C?.revision!==void 0?{baseRevision:C.revision}:{},..."resourceId"in h&&h.resourceId!==void 0?{resourceId:h.resourceId}:{}}),_.set(`to:${y}`,setTimeout(()=>{_.delete(`to:${y}`),d.current&&(l({type:"TIMEOUT",id:h.id,clientMutationId:y}),R())},a));let L=()=>{Z(`to:${y}`),d.current&&(l({type:"REJECT",id:h.id,clientMutationId:y}),R())},P={kind:p,clientMutationId:y,payload:{...h,client_mutation_id:y}},N;try{N=n(P)}catch(w){N=Promise.reject(w instanceof Error?w:new Error(String(w)))}N.then(w=>{if(w.id!==h.id){L();return}Z(`to:${y}`),d.current&&l({type:"RESOLVE",id:w.id,clientMutationId:y,start:w.start,end:w.end,revision:w.revision,...w.resourceId!==void 0?{resourceId:w.resourceId}:{}})}).catch(L)},[n,a,r,i]),D=Re.useMemo(()=>an(t,o),[t,o]);return{events:D.events,pendingIds:D.pendingIds,rolledBackIds:D.rolledBackIds,submit:g}}import{jsx as pr}from"react/jsx-runtime";function ur({events:e,mutate:t,timeoutMs:n,rollbackFlashMs:a,generateId:r,...i}){let{events:o,pendingIds:l,rolledBackIds:s,submit:d}=En({events:e,mutate:t,...n!==void 0?{timeoutMs:n}:{},...a!==void 0?{rollbackFlashMs:a}:{},...r?{generateId:r}:{}});return pr(Nt,{...i,events:o,pendingIds:l,rolledBackIds:s,onEventDrop:u=>d("drop",u),onEventResize:u=>d("resize",u)})}export{Nt as AetherCalendar,Zn as CALENDAR_CSS,ln as CalendarNav,dn as DEFAULT_LOCALE_MESSAGES,ur as OptimisticCalendar,At as PRESETS,Yn as PRESET_NAMES,ca as TIMELINE_CSS,ta as TIME_GRID_CSS,hn as TimeGridView,Dn as TimelineView,or as default,un as defaultBaseTokenCss,pn as defaultTimeGridTokenCss,gn as defaultTimelineTokenCss,qe as ensureCalendarStyles,fn as ensureTimeGridStyles,wn as ensureTimelineStyles,je as getVisibleRange,Xn as isThemePreset,O as parseLocalDateTime,Ze as resolveMessages,mn as resolveThemeVars,it as stepAnchor,En as useOptimisticEvents};
