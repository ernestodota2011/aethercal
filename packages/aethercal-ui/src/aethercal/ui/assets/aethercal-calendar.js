function we(e){return String(e).padStart(2,"0")}function L(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,n,a,r,o,i,d]=t,s=Number(n),l=Number(a),u=Number(r),p=Number(o??"0"),D=Number(i??"0"),w=Number(d??"0");if(l<1||l>12||u<1||u>31||p>23||D>59||w>59)throw new Error(`out-of-range ISO datetime: ${e}`);let E=new Date(s,l-1,u,p,D,w);if(E.getFullYear()!==s||E.getMonth()!==l-1||E.getDate()!==u)throw new Error(`nonexistent calendar date: ${e}`);return E}function ae(e){return`${e.getFullYear()}-${we(e.getMonth()+1)}-${we(e.getDate())}T${we(e.getHours())}:${we(e.getMinutes())}:${we(e.getSeconds())}`}function ue(e){let t=L(e);return`${t.getFullYear()}-${we(t.getMonth()+1)}-${we(t.getDate())}`}function En(e){return`${e.getFullYear()}-${we(e.getMonth()+1)}-${we(e.getDate())}`}function Ve(e){let t=L(e.start),n=L(e.end),a=new Date(t.getFullYear(),t.getMonth(),t.getDate()),r=a;if(n.getTime()>t.getTime()){let o=new Date(n.getTime()-1),i=new Date(o.getFullYear(),o.getMonth(),o.getDate());i.getTime()>a.getTime()&&(r=i)}return{startKey:En(a),lastKey:En(r)}}function pa(e,t){return(e.getDay()-t+7)%7}function Ue(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate());return n.setDate(n.getDate()-pa(n,t)),n}function zt(e,t){return Array.from({length:t},(n,a)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+a);return`${r.getFullYear()}-${we(r.getMonth()+1)}-${we(r.getDate())}`})}function Ht(e,t=1){return zt(Ue(e,t),7)}function _t(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),1);return zt(Ue(n,t),42)}function $t(e,t){return zt(new Date(e.getFullYear(),e.getMonth(),e.getDate()),t)}function be(e,t){let n=L(`${ue(e)}T00:00:00`),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()+t);return`${a.getFullYear()}-${we(a.getMonth()+1)}-${we(a.getDate())}`}function Bt(e,t){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()),a=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((a.getTime()-n.getTime())/864e5)}function tt(e,t){let n=L(e.start),a=L(e.end),r=L(t),o=Bt(n,r),i=new Date(n.getFullYear(),n.getMonth(),n.getDate()+o,n.getHours(),n.getMinutes(),n.getSeconds()),d=new Date(a.getFullYear(),a.getMonth(),a.getDate()+o,a.getHours(),a.getMinutes(),a.getSeconds()),s={id:e.id,start:ae(i),end:ae(d)};return e.revision!==void 0&&(s.revision=e.revision),s}var fa=370;function Tn(e){return String(e).padStart(2,"0")}function xn(e){return`${e.getFullYear()}-${Tn(e.getMonth()+1)}-${Tn(e.getDate())}`}function va(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function ya(e){let{startKey:t,lastKey:n}=Ve(e),a=[],r=L(t);for(let o=0;o<fa&&xn(r)<=n;o+=1)a.push(xn(r)),r=va(r,1);return{keys:a,startKey:t,lastKey:n}}function Vt(e){let t=new Map;return e.forEach((n,a)=>{let{keys:r,startKey:o,lastKey:i}=ya(n),d=L(n.start).getTime(),s=L(n.end).getTime();for(let l of r){let u={entry:{event:n,isContinuation:l!==o,continuesAfter:l!==i},startMs:d,endMs:s,index:a},p=t.get(l);p?p.push(u):t.set(l,[u])}}),[...t.keys()].sort().map(n=>{let a=t.get(n);return a.sort((r,o)=>r.startMs-o.startMs||r.endMs-o.endMs||r.index-o.index),{date:n,entries:a.map(r=>r.entry)}})}function Ke(e,t,n,a){let r=n*a;if(r<=0)return e;let o=Math.min(Math.max(e,0),r-1),i=o-o%a,d=Math.min(i+a-1,r-1);switch(t){case"ArrowLeft":return o>i?o-1:o;case"ArrowRight":return o<d?o+1:o;case"ArrowUp":{let s=o-a;return s>=0?s:o}case"ArrowDown":{let s=o+a;return s<r?s:o}case"Home":return i;case"End":return d;default:return o}}var Ye=60,xe=15;function Kt(e,t,n){return Math.min(n,Math.max(t,e))}function yt(e,t){let n=L(`${e}T00:00:00`);return new Date(n.getFullYear(),n.getMonth(),n.getDate(),0,t,0)}function Yt(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate(),e.getHours(),e.getMinutes()+t,e.getSeconds())}function ht(e,t){return t==null||(e.resourceId=t),e}function We(e,t,n=xe){let a=t.dayStartHour*Ye,r=t.dayEndHour*Ye,o=a+Kt(e,0,1)*t.windowMinutes,i=n>0?n:xe,d=a+Math.round((o-a)/i)*i;return Kt(d,a,r)}function bt(e,t){return Kt(e,t.dayStartHour*Ye,t.dayEndHour*Ye)}var Ut=24*Ye;function Wt(e,t,n,a){let r=t+n,o=e;for(;r<0;)r+=Ut,o=be(o,-1);for(;r>Ut;)r-=Ut,o=be(o,1);return{dateOnly:o,minuteOfDay:bt(r,a)}}function Ne(e,t,n,a){if(n===null)return ht(tt(e,t),a);let r=L(e.start),o=L(e.end),i=yt(t,n),d=Bt(r,o),s=r.getHours()*Ye+r.getMinutes(),u=o.getHours()*Ye+o.getMinutes()-s,p=new Date(i.getFullYear(),i.getMonth(),i.getDate()+d,i.getHours(),i.getMinutes()+u,0),D={id:e.id,start:ae(i),end:ae(p)};return e.revision!==void 0&&(D.revision=e.revision),ht(D,a)}function ke(e,t,n,a,r={}){let o=r.minDurationMinutes??xe,i=L(e.start),d=L(e.end),s=yt(n,a),l=i,u=d;if(t==="end"){let D=Yt(i,o);u=s.getTime()>=D.getTime()?s:D}else{let D=Yt(d,-o);l=s.getTime()<=D.getTime()?s:D}let p={id:e.id,start:ae(l),end:ae(u)};return e.revision!==void 0&&(p.revision=e.revision),p}function Ae(e,t,n={}){let a=n.minDurationMinutes??xe;if(e.minuteOfDay===null||t.minuteOfDay===null){let[u,p]=e.dateOnly<=t.dateOnly?[e.dateOnly,t.dateOnly]:[t.dateOnly,e.dateOnly],D=L(`${u}T00:00:00`),w=L(`${p}T00:00:00`),E=new Date(w.getFullYear(),w.getMonth(),w.getDate()+1),v={start:ae(D),end:ae(E),allDay:!0};return ht(v,e.resourceId)}let o=yt(e.dateOnly,e.minuteOfDay??0),i=yt(t.dateOnly,t.minuteOfDay??0),d=o.getTime()<=i.getTime()?o:i,s=o.getTime()<=i.getTime()?i:o;s.getTime()===d.getTime()&&(s=Yt(d,a));let l={start:ae(d),end:ae(s),allDay:!1};return ht(l,e.resourceId)}var Pe=60,ha=24*Pe,ba=864e5;function Dt(e,t,n){return Math.min(n,Math.max(t,e))}function lt(e={}){let t=e.dayStartHour,n=e.dayEndHour,a=Number.isFinite(t)&&t!==void 0?Dt(Math.trunc(t),0,23):0,r=Number.isFinite(n)&&n!==void 0?Dt(Math.trunc(n),1,24):24,[o,i]=r>a?[a,r]:[0,24];return{dayStartHour:o,dayEndHour:i,windowMinutes:(i-o)*Pe}}function Rn(e){let t=[],n=[];for(let a of e)a.allDay===!0?t.push(a):n.push(a);return{allDay:t,timed:n}}function Xe(e,t){let n=L(e),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()),r=Math.round((a.getTime()-t.getTime())/ba),o=n.getHours()*Pe+n.getMinutes()+n.getSeconds()/60;return r*ha+o}function wt(e,t){let n=e.map(s=>{let[l,u]=t(s);return{item:s,start:l,end:u}});n.sort((s,l)=>s.start!==l.start?s.start-l.start:l.end-s.end);let a=[],r=[],o=[],i=Number.NEGATIVE_INFINITY,d=()=>{let s=r.length;for(let l of o)a[l].laneCount=s;r=[],o=[],i=Number.NEGATIVE_INFINITY};for(let s of n){o.length>0&&s.start>=i&&d();let l=r.findIndex(u=>!(u.start<s.end&&s.start<u.end));l===-1?(l=r.length,r.push({start:s.start,end:s.end})):r[l]={start:s.start,end:s.end},o.push(a.length),a.push({item:s.item,lane:l,laneCount:1}),i=Math.max(i,s.end)}return d(),a}function Cn(e){return wt(e,t=>[L(t.start).getTime(),L(t.end).getTime()])}function dt(e,t,n){let a=L(`${t}T00:00:00`),r=n.dayStartHour*Pe,o=n.dayEndHour*Pe,i=e.filter(d=>{let s=Xe(d.start,a);return!(Xe(d.end,a)<=r||s>=o)});return Cn(i).map(({item:d,lane:s,laneCount:l})=>{let u=Xe(d.start,a),p=Xe(d.end,a),D=Dt(u,r,o),w=Dt(p,D,o),{startKey:E,lastKey:v}=Ve(d);return{event:d,lane:s,laneCount:l,topFraction:(D-r)/n.windowMinutes,heightFraction:(w-D)/n.windowMinutes,isContinuation:t!==E,continuesAfter:t!==v}})}function Da(e){let t=[];for(let n=e.dayStartHour;n<e.dayEndHour;n+=1)t.push({hour:n,topFraction:(n-e.dayStartHour)*Pe/e.windowMinutes});return t}function Xt(e,t,n={}){let a="windowMinutes"in n?n:lt(n),{allDay:r,timed:o}=Rn(t),i=o.map(s=>({event:s,startTs:L(s.start).getTime(),endTs:L(s.end).getTime()}));return{columns:e.map(s=>{let l=L(`${s}T00:00:00`),u=l.getTime(),p=new Date(l.getFullYear(),l.getMonth(),l.getDate()+1).getTime(),D=i.filter(E=>E.startTs>=p?!1:E.endTs>u?!0:E.startTs===E.endTs&&E.startTs>=u).map(E=>E.event),w=r.filter(E=>{let{startKey:v,lastKey:k}=Ve(E);return v<=s&&s<=k});return{dateOnly:s,allDay:w,timed:dt(D,s,a)}}),hourMarks:Da(a),config:a}}function Jt(e,t={}){let n="windowMinutes"in t?t:lt(t),a=e.getHours()*Pe+e.getMinutes()+e.getSeconds()/60,r=n.dayStartHour*Pe,o=n.dayEndHour*Pe;return a<r||a>=o?null:(a-r)/n.windowMinutes}var Ge=60,Et=7,Mn=1,In=31;function ct(e,t,n){return Math.min(n,Math.max(t,e))}function Fe(e){return e===void 0||!Number.isFinite(e)?Et:ct(Math.trunc(e),Mn,In)}function Tt(e){return"windowMinutes"in e?e:lt(e)}function kn(e){if(e.allDay!==!0)return{start:e.start,end:e.end};let{startKey:t,lastKey:n}=Ve(e);return{start:`${t}T00:00:00`,end:`${be(n,1)}T00:00:00`}}function wa(e,t,n){let a=n.dayStartHour*Ge,r=n.dayEndHour*Ge,o=[];return t.forEach((i,d)=>{let s=L(`${i}T00:00:00`),l=Xe(e.start,s),u=Xe(e.end,s);if(u<=a||l>=r)return;let p=ct(l,a,r),D=ct(u,p,r),w=d*n.windowMinutes;o.push({startMin:w+(p-a),endMin:w+(D-a),clippedStart:l<a,clippedEnd:u>r})}),o}function Ea(e){let t=[];for(let n of e){let a=t[t.length-1];a&&a.endMin===n.startMin?(a.endMin=n.endMin,a.clippedEnd=n.clippedEnd):t.push({...n})}return t}function Sn(e,t,n){let a=t.length*n.windowMinutes;if(a<=0)return[];let r=[];for(let i of e){let d=Ea(wa(i,t,n));d.length>0&&r.push({item:i,runs:d})}return wt(r,i=>[i.runs[0].startMin,i.runs[i.runs.length-1].endMin]).flatMap(({item:i,lane:d,laneCount:s})=>i.runs.map(l=>({event:i.item.event,lane:d,laneCount:s,leftFraction:l.startMin/a,widthFraction:(l.endMin-l.startMin)/a,allDay:i.item.event.allDay===!0,continuesBefore:l.clippedStart,continuesAfter:l.clippedEnd})))}function jt(e,t,n={}){return Sn([{event:e,...kn(e)}],t,Tt(n))}function qt(e,t,n,a={}){let r=Tt(a),o=new Set(a.collapsedGroupIds??[]),i=[],d=new Set;for(let R of e)d.has(R.id)||(d.add(R.id),i.push(R));let s=[],l=new Map;for(let R of i){let O=R.groupId?R.groupId:void 0;if(O===void 0){s.push({kind:"solo",resource:R});continue}let P=l.get(O);P?P.push(R):(l.set(O,[R]),s.push({kind:"group",id:O}))}let u=new Map,p=[];for(let R of t){let O={event:R,...kn(R)},P=R.resourceId;if(P!==void 0&&d.has(P)){let N=u.get(P);N?N.push(O):u.set(P,[O])}else p.push(O)}let D=(R,O,P)=>{let N=Sn(P,n,r);return{resource:R,groupId:O,blocks:N,laneCount:N.reduce((y,b)=>Math.max(y,b.laneCount),1)}},w=[];for(let R of s){if(R.kind==="solo"){w.push({kind:"row",row:D(R.resource,null,u.get(R.resource.id)??[])});continue}let O=l.get(R.id)??[],P=o.has(R.id);if(w.push({kind:"group",group:{id:R.id,collapsed:P,resourceCount:O.length}}),!P)for(let N of O)w.push({kind:"row",row:D(N,R.id,u.get(N.id)??[])})}let E=D(null,null,p);E.blocks.length>0&&w.push({kind:"row",row:E});let v=n.length,k=n.map((R,O)=>({dateOnly:R,leftFraction:v>0?O/v:0,widthFraction:v>0?1/v:0})),_=v*r.windowMinutes,Q=[];return _>0&&n.forEach((R,O)=>{let P=O*r.windowMinutes;for(let N=r.dayStartHour;N<r.dayEndHour;N+=1){let y=(N-r.dayStartHour)*Ge;Q.push({dateOnly:R,hour:N,leftFraction:(P+y)/_,isDayStart:N===r.dayStartHour})}}),{days:[...n],items:w,dayHeaders:k,ticks:Q,config:r}}function ze(e,t,n={},a=xe){let r=Tt(n);if(t.length===0||r.windowMinutes<=0)return null;let o=t.length*r.windowMinutes,i=ct(e,0,1)*o,d=Math.min(Math.floor(i/r.windowMinutes),t.length-1),s=i-d*r.windowMinutes,l=r.dayStartHour*Ge,u=r.dayEndHour*Ge,p=a>0?a:xe,D=l+Math.round(s/p)*p;return{dateOnly:t[d],minuteOfDay:ct(D,l,u)}}function Zt(e,t,n={}){let a=Tt(n),r=t.indexOf(ue(ae(e)));if(r===-1)return null;let o=e.getHours()*Ge+e.getMinutes()+e.getSeconds()/60,i=a.dayStartHour*Ge,d=a.dayEndHour*Ge;if(o<i||o>=d)return null;let s=t.length*a.windowMinutes;return s<=0?null:(r*a.windowMinutes+(o-i))/s}var Ta=1;function ut(e,t,n=Ta,a){let r=t.getFullYear(),o=t.getMonth(),i=t.getDate(),d,s;switch(e){case"week":{d=Ue(t,n),s=new Date(d.getFullYear(),d.getMonth(),d.getDate()+7);break}case"day":{d=new Date(r,o,i),s=new Date(r,o,i+1);break}case"timeline":{d=new Date(r,o,i),s=new Date(r,o,i+Fe(a));break}default:{d=new Date(r,o,1),s=new Date(r,o+1,1);break}}return{view:e,from:ae(d),to:ae(s)}}function xt(e,t,n,a){let r=e.getFullYear(),o=e.getMonth(),i=e.getDate();switch(t){case"week":return new Date(r,o,i+7*n);case"day":return new Date(r,o,i+n);case"timeline":return new Date(r,o,i+Fe(a)*n);default:return new Date(r,o+n,1)}}var Rt={status:"idle"};function Ct(e){return e.status==="dragging"}function Qt(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return Rt}}var nt={status:"idle"};function gt(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"RESIZE_START":return{status:"resizing",eventId:t.eventId,edge:t.edge};case"SELECT_START":return{status:"selecting",anchor:t.point,current:t.point};case"SELECT_MOVE":return e.status!=="selecting"?e:{status:"selecting",anchor:e.anchor,current:t.point};case"COMMIT":case"CANCEL":return nt}}var en={overrides:{},appliedRevision:{}};function xa(e,t){let n={...e};return delete n[t],n}function tn(e,t){switch(t.type){case"SUBMIT":{let n=t.baseRevision??Number.NEGATIVE_INFINITY,a=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;return{overrides:{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"pending",start:t.start,end:t.end,...t.baseRevision!==void 0?{revision:t.baseRevision}:{},...t.resourceId!==void 0?{resourceId:t.resourceId}:{}}},appliedRevision:{...e.appliedRevision,[t.id]:Math.max(a,n)}}}case"RESOLVE":{let n=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;if(t.revision<=n)return e;let a=e.overrides[t.id],r=a!==void 0&&a.clientMutationId===t.clientMutationId&&a.status==="pending",o=t.resourceId??a?.resourceId;return{overrides:r?{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"committed",start:t.start,end:t.end,revision:t.revision,...o!==void 0?{resourceId:o}:{}}}:e.overrides,appliedRevision:{...e.appliedRevision,[t.id]:t.revision}}}case"REJECT":case"TIMEOUT":{let n=e.overrides[t.id];return!n||n.clientMutationId!==t.clientMutationId||n.status!=="pending"?e:{...e,overrides:{...e.overrides,[t.id]:{...n,status:"rolledback"}}}}case"CLEAR":{let n=e.overrides[t.id];return!n||t.clientMutationId&&n.clientMutationId!==t.clientMutationId?e:{...e,overrides:xa(e.overrides,t.id)}}}}function nn(e,t){let n=new Set,a=new Set,r=i=>i.resourceId!==void 0?{resourceId:i.resourceId}:void 0;return{events:e.map(i=>{let d=t.overrides[i.id];return d?d.status==="pending"?(n.add(i.id),{...i,start:d.start,end:d.end,...r(d)}):d.status==="rolledback"?(a.add(i.id),i):i.revision!==void 0&&d.revision!==void 0&&i.revision>=d.revision?i:{...i,start:d.start,end:d.end,...d.revision!==void 0?{revision:d.revision}:{},...r(d)}:i}),pendingIds:n,rolledBackIds:a}}function an(e,t){let n=new Map(e.map(r=>[r.id,r])),a=[];for(let[r,o]of Object.entries(t.overrides)){if(o.status!=="committed")continue;let i=n.get(r);i&&i.revision!==void 0&&o.revision!==void 0&&i.revision>=o.revision&&a.push(r)}return a}import*as Ie from"react";import*as Mt from"react";var rn=new Date(2023,0,1);function Pn(e,t){let n=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(a,r)=>{let o=(t+r)%7,i=new Date(rn.getFullYear(),rn.getMonth(),rn.getDate()+o);return n.format(i)})}function on(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function An(e,t,n){let a=new Intl.DateTimeFormat(n,{month:"short",day:"numeric"}).format(e),r=new Intl.DateTimeFormat(n,{month:"short",day:"numeric",year:"numeric"}).format(t);return`${a} \u2013 ${r}`}function Ln(e,t,n,a,r=Et){if(e==="day")return new Intl.DateTimeFormat(n,{dateStyle:"full"}).format(t);if(e==="week"){let o=Ue(t,a),i=new Date(o.getFullYear(),o.getMonth(),o.getDate()+6);return An(o,i,n)}if(e==="timeline"){let o=Fe(r),i=new Date(t.getFullYear(),t.getMonth(),t.getDate()),d=new Date(i.getFullYear(),i.getMonth(),i.getDate()+o-1);return o===1?new Intl.DateTimeFormat(n,{dateStyle:"full"}).format(i):An(i,d,n)}return on(t,n)}function mt(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(L(e))}function me(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(L(e))}function On(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(L(e))}import{jsx as He,jsxs as Gn}from"react/jsx-runtime";function Ra(...e){return e.filter(Boolean).join(" ")}function Ca(e,t,n){let{event:a,isContinuation:r,continuesAfter:o}=e;return a.allDay===!0?n.allDay:r?o?n.continues:n.endsAt(me(a.end,t)):me(a.start,t)}function Ma({entry:e,locale:t,messages:n}){let{event:a,isContinuation:r,continuesAfter:o}=e,i=Ca(e,t,n),d=a.color?{"--ac-event-accent":a.color}:void 0;return Gn("li",{className:Ra("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":a.id,"aria-label":`${i} ${a.title}`,style:d,...a.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...o?{"data-continues-after":""}:{},children:[He("span",{className:"aethercal-agenda-event-time",children:i}),He("span",{className:"aethercal-agenda-event-title",children:a.title})]})}function Nn({events:e,locale:t,messages:n,themeVars:a}){let r=Mt.useMemo(()=>Vt(e),[e]),o=Mt.useId();return r.length===0?He("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:He("p",{className:"aethercal-agenda-empty",children:n.noEvents})}):He("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:r.map(i=>{let d=`${o}-${i.date}`;return Gn("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":d,"data-date":i.date,children:[He("div",{className:"aethercal-agenda-day-title",id:d,children:On(i.date,t)}),He("ul",{className:"aethercal-agenda-day-events",role:"list",children:i.entries.map((s,l)=>He(Ma,{entry:s,locale:t,messages:n},`${s.event.id}-${l}`))})]},i.date)})})}import{jsx as _e,jsxs as Fn}from"react/jsx-runtime";var Ia=["month","week","day","list","timeline"];function sn({view:e,anchor:t,now:n,locale:a,firstDayOfWeek:r,timelineDays:o,messages:i,showViews:d=!0,onRangeChange:s,onViewChange:l}){let u=w=>{s?.(ut(e,w,r,o))},p=w=>xt(t,e,w,o),D=Ln(e,t,a,r,o);return Fn("div",{className:"aethercal-nav",role:"toolbar","aria-label":i.navToolbar,children:[Fn("div",{className:"aethercal-nav-group",children:[_e("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":i.navPrevious,onClick:()=>u(p(-1)),children:_e("span",{"aria-hidden":"true",children:"\u2039"})}),_e("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-today",onClick:()=>u(n),children:i.navToday}),_e("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":i.navNext,onClick:()=>u(p(1)),children:_e("span",{"aria-hidden":"true",children:"\u203A"})})]}),_e("span",{className:"aethercal-nav-title","aria-live":"polite",children:D}),d?_e("div",{className:"aethercal-nav-views",children:Ia.map(w=>_e("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-view","aria-pressed":w===e,onClick:()=>l?.(ut(w,t,r,o)),children:i.viewNames[w]},w))}):null]})}var ka={allDay:"All day",continues:"Continues",endsAt:e=>`ends ${e}`,more:e=>`+${e} more`,noEvents:"No events",unavailable:"This view is not available yet.",keyboardHint:"Use the arrow keys to move between days. Press Enter on an event to grab it, the arrow keys to move or resize it, Enter to drop, and Escape to cancel.",grabbedMoveHint:e=>`Grabbed ${e}. Use the arrow keys to move it, Enter to drop, Escape to cancel.`,grabbedResizeHint:e=>`Resizing ${e}. Use the up and down arrow keys to change its duration, Enter to confirm, Escape to cancel.`,movedTo:e=>`Moved to ${e}`,resizedTo:e=>`Duration ${e}`,dropped:e=>`Dropped on ${e}`,resized:e=>`Duration set to ${e}`,createHere:e=>`Create an event on ${e}`,cancelled:"Cancelled",navToolbar:"Calendar navigation",navPrevious:"Previous",navNext:"Next",navToday:"Today",viewNames:{month:"Month",week:"Week",day:"Day",list:"Agenda",timeline:"Timeline"},timelineResources:"Resources",timelineUnassigned:"Unassigned",timelineEmpty:"No resources to show",timelineGroupCount:e=>e===1?"1 resource":`${e} resources`,groupExpanded:e=>`${e} expanded`,groupCollapsed:e=>`${e} collapsed`,timelineKeyboardHint:"Use the up and down arrow keys to move between resources and the left and right arrow keys to move between days. Press Enter on a group to expand or collapse it, or on an event to grab it; then use the left and right arrow keys to change its time, the up and down arrow keys to move it to another resource, Enter to drop it, and Escape to cancel."},Sa={allDay:"Todo el d\xEDa",continues:"Contin\xFAa",endsAt:e=>`termina ${e}`,more:e=>`+${e} m\xE1s`,noEvents:"Sin eventos",unavailable:"Esta vista a\xFAn no est\xE1 disponible.",keyboardHint:"Usa las flechas para moverte entre los d\xEDas. Pulsa Enter sobre un evento para agarrarlo, las flechas para moverlo o cambiar su duraci\xF3n, Enter para soltarlo y Escape para cancelar.",grabbedMoveHint:e=>`Agarraste el evento ${e}. Usa las flechas para moverlo, Enter para soltarlo y Escape para cancelar.`,grabbedResizeHint:e=>`Est\xE1s cambiando la duraci\xF3n de ${e}. Usa las flechas hacia arriba y abajo para ajustarla, Enter para confirmar y Escape para cancelar.`,movedTo:e=>`Movido a ${e}`,resizedTo:e=>`Duraci\xF3n ${e}`,dropped:e=>`Soltado en ${e}`,resized:e=>`Duraci\xF3n establecida en ${e}`,createHere:e=>`Crear un evento en ${e}`,cancelled:"Cancelado",navToolbar:"Navegaci\xF3n del calendario",navPrevious:"Anterior",navNext:"Siguiente",navToday:"Hoy",viewNames:{month:"Mes",week:"Semana",day:"D\xEDa",list:"Agenda",timeline:"Cronograma"},timelineResources:"Recursos",timelineUnassigned:"Sin asignar",timelineEmpty:"No hay recursos para mostrar",timelineGroupCount:e=>e===1?"1 recurso":`${e} recursos`,groupExpanded:e=>`${e} desplegado`,groupCollapsed:e=>`${e} plegado`,timelineKeyboardHint:"Usa las flechas hacia arriba y abajo para moverte entre los recursos, y las flechas izquierda y derecha para moverte entre los d\xEDas. Pulsa Enter sobre un grupo para desplegarlo o plegarlo, o sobre un evento para agarrarlo; luego usa las flechas izquierda y derecha para cambiar su hora, las flechas hacia arriba y abajo para moverlo a otro recurso, Enter para soltarlo y Escape para cancelar."},ln={en:ka,es:Sa};function Aa(e){return e.toLowerCase().split("-")[0]??""}function Je(e,t,n=ln){let a=e.toLowerCase(),r=n[a]??n[Aa(e)]??n.en??ln.en;return t?{...r,...t}:r}import*as ie from"react";import{jsx as zn}from"react/jsx-runtime";function at({message:e}){return zn("div",{className:"aethercal-sr-only","aria-live":"polite","aria-atomic":"true",children:e})}function rt({id:e,text:t}){return zn("div",{id:e,className:"aethercal-sr-only",children:t})}import{jsx as Hn,jsxs as La}from"react/jsx-runtime";function Pa(...e){return e.filter(Boolean).join(" ")}function It({event:e,timeLabel:t,onDragStart:n,onDragEnd:a,canDrag:r=!0,isPending:o,isRolledBack:i,onClick:d,onContextMenu:s,id:l,interactive:u,isActive:p,isGrabbed:D}){let w=e.editable!==!1,E=w&&r,v=e.color?{"--ac-event-accent":e.color}:void 0,k=t?`${t} ${e.title}`:e.title;return La("div",{className:Pa("aethercal-event",!w&&"is-locked",o&&"is-pending",i&&"is-rolledback",p&&"is-active",D&&"is-grabbed"),...l?{id:l}:{},...u?{role:"button"}:{},draggable:E,"data-event-id":e.id,"aria-label":k,title:e.title,style:v,onDragStart:_=>{if(!E){_.preventDefault();return}_.dataTransfer.setData("text/plain",e.id),_.dataTransfer.effectAllowed="move",n(e.id)},onDragEnd:a,onClick:d,onContextMenu:s?_=>{_.preventDefault(),_.stopPropagation(),s()}:void 0,children:[t?Hn("time",{className:"aethercal-event-time",children:t}):null,t?" ":null,Hn("span",{className:"aethercal-event-title",children:e.title})]})}import{Fragment as Fa,jsx as Se,jsxs as kt}from"react/jsx-runtime";var _n=new Set,ot=7,$n=6;function Bn(...e){return e.filter(Boolean).join(" ")}function Oa(e){let t=[];for(let n=0;n<e.length;n+=ot)t.push(e.slice(n,n+ot));return t}function Na(e){let t=new Map;for(let n of e){let a=ue(n.start),r=t.get(a);r?r.push(n):t.set(a,[n])}return t}function Ga(e){return{start:`${e}T00:00:00`,end:`${be(e,1)}T00:00:00`,allDay:!0}}function Vn(e){let{events:t,anchor:n,locale:a,firstDayOfWeek:r,messages:o,weekdayLabels:i,maxEventsPerDay:d,themeVars:s,onEventDrop:l,onRangeSelect:u,onEventClick:p,onContextMenu:D,pendingIds:w=_n,rolledBackIds:E=_n}=e,v=ie.useMemo(()=>_t(n,r),[n,r]),k=ie.useMemo(()=>Oa(v),[v]),_=ie.useMemo(()=>i??Pn(a,r),[i,a,r]),Q=ie.useMemo(()=>Na(t),[t]),R=n.getMonth(),O=ue(ae(new Date)),P=ie.useMemo(()=>ue(ae(n)),[n]),[N,y]=ie.useReducer(Qt,Rt),[b,G]=ie.useState(()=>new Set),$=ie.useId(),[S,W]=ie.useState(P),[C,te]=ie.useState(null),[B,re]=ie.useState(null),[K,z]=ie.useState("");ie.useEffect(()=>{v.includes(S)||(W(P),te(null),re(null))},[v,S,P]);let g=ie.useCallback(Y=>!!p||Y.editable!==!1&&!!l,[p,l]);ie.useEffect(()=>{let Y=new Set((Q.get(S)??[]).filter(h=>g(h)).map(h=>h.id));B&&!Y.has(B.eventId)?(re(null),te(null)):!B&&C!==null&&!Y.has(C)&&te(null)},[Q,S,C,B,g]);let f=Y=>`${$}-c-${Y}`,q=(Y,h)=>`${$}-e-${Y}-${h}`,Z=`${$}-hint`,T=B?q(S,B.eventId):C?q(S,C):f(S),H=ie.useCallback(Y=>{G(h=>{let X=new Set(h);return X.add(Y),X})},[]),x=ie.useCallback(Y=>h=>{if(h.preventDefault(),!Ct(N)){y({type:"DROP"});return}let X=N.eventId,se=h.dataTransfer.getData("text/plain");if(y({type:"DROP"}),se&&se!==X||!l)return;let ee=t.find(oe=>oe.id===X);!ee||ee.editable===!1||l(tt(ee,Y))},[N,t,l]),V=!!l,j=ie.useCallback(Y=>{if(!B)return;let h=be(B.targetDate,Y),X=v[0],se=v[v.length-1];h<X||h>se||(z(o.movedTo(mt(h,a))),re({...B,targetDate:h,moved:!0}))},[B,v,a,o]),de=ie.useCallback(()=>{if(!B)return;if(!B.moved){te(B.eventId),re(null);return}let Y=t.find(h=>h.id===B.eventId);Y&&Y.editable!==!1&&l&&(l(tt(Y,B.targetDate)),z(o.dropped(mt(B.targetDate,a)))),W(B.targetDate),te(null),re(null)},[B,t,l,o,a]),Re={ArrowLeft:-1,ArrowRight:1,ArrowUp:-ot,ArrowDown:ot},ge=ie.useCallback(Y=>{let{key:h}=Y,X=h==="Enter"||h===" "||h==="Spacebar";if(B){if(h in Re){Y.preventDefault(),j(Re[h]);return}if(X){Y.preventDefault(),de();return}if(h==="Escape"){Y.preventDefault(),re(null),z(o.cancelled);return}return}let se=Q.get(S)??[],ee=se.filter(oe=>g(oe));if(C){let oe=ee.findIndex(ne=>ne.id===C);if(h==="ArrowDown"){Y.preventDefault(),oe>=0&&oe<ee.length-1&&te(ee[oe+1].id);return}if(h==="ArrowUp"){Y.preventDefault(),oe>0?te(ee[oe-1].id):te(null);return}if(X){Y.preventDefault();let ne=ee.find(Ze=>Ze.id===C);if(!ne)return;ne.editable!==!1&&l?(re({eventId:ne.id,targetDate:S,moved:!1}),z(o.grabbedMoveHint(ne.title))):p&&p({id:ne.id});return}if(h==="Escape"){Y.preventDefault(),te(null);return}if(h==="ArrowLeft"||h==="ArrowRight"||h==="Home"||h==="End"){Y.preventDefault(),te(null);let ne=Ke(v.indexOf(S),h,$n,ot);W(v[ne]);return}return}if(h in Re||h==="Home"||h==="End"){Y.preventDefault();let oe=Ke(v.indexOf(S),h,$n,ot);W(v[oe]);return}X&&(ee.length>0?(Y.preventDefault(),H(S),te(ee[0].id)):se.length===0&&u&&(Y.preventDefault(),u(Ga(S)),z(o.createHere(mt(S,a)))))},[B,C,S,v,Q,g,l,p,u,j,de,H,o,a,Re]);return kt(Fa,{children:[kt("div",{className:Bn("aethercal-calendar",Ct(N)&&"is-dragging"),role:"grid","aria-label":on(n,a),"aria-describedby":Z,"aria-activedescendant":T,tabIndex:0,"data-view":"month",style:s,onKeyDown:ge,children:[Se("div",{className:"aethercal-weekdays",role:"row",children:_.map((Y,h)=>Se("div",{role:"columnheader",className:"aethercal-weekday",children:Y},h))}),k.map((Y,h)=>Se("div",{className:"aethercal-week",role:"row",children:Y.map(X=>{let se=Q.get(X)??[],ee=b.has(X),oe=ee?se:se.slice(0,d),ne=se.length-oe.length,Ze=new Date(`${X}T00:00:00`).getMonth()!==R,ft=X===O,vt=!C&&!B&&X===S,Nt=B?.targetDate===X;return kt("div",{id:f(X),role:"gridcell",className:Bn("aethercal-day",Ze&&"is-outside",ft&&"is-today",vt&&"is-active",Nt&&"is-drop-target"),"data-date":X,onDragOver:V?ce=>ce.preventDefault():void 0,onDrop:V?x(X):void 0,onContextMenu:D?ce=>{ce.target.closest("[data-event-id], button")||(ce.preventDefault(),D({start:`${X}T00:00:00`}))}:void 0,children:[Se("span",{className:"aethercal-sr-only",children:mt(X,a)}),Se("div",{className:"aethercal-day-head",children:Se("span",{className:"aethercal-day-number","aria-hidden":"true",children:Number(X.slice(-2))})}),kt("div",{className:"aethercal-day-events",children:[oe.map(ce=>{let Gt=B?.eventId===ce.id||!B&&C===ce.id;return Se(It,{id:q(X,ce.id),event:ce,interactive:g(ce),isActive:Gt,isGrabbed:B?.eventId===ce.id,timeLabel:ce.allDay?null:me(ce.start,a),canDrag:V,onDragStart:c=>y({type:"DRAG_START",eventId:c}),onDragEnd:()=>y({type:"DRAG_CANCEL"}),isPending:w.has(ce.id),isRolledBack:E.has(ce.id),...p?{onClick:()=>p({id:ce.id})}:{},...D?{onContextMenu:()=>D({id:ce.id})}:{}},ce.id)}),ne>0&&!ee?Se("button",{type:"button",className:"aethercal-more",onClick:()=>H(X),children:o.more(ne)}):null]})]},X)})},h))]}),Se(rt,{id:Z,text:o.keyboardHint}),Se(at,{message:K})]})}var Un={light:{"--ac-fg":"#1f2328","--ac-muted":"#5f6672","--ac-faint":"#676e79","--ac-bg":"#ffffff","--ac-header-fg":"#4b5563","--ac-border":"#e5e7eb","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#fafafa","--ac-today-marker-bg":"#111827","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#eef1f4","--ac-event-fg":"#1f2328","--ac-event-accent":"#64748b","--ac-more-fg":"#4b5563","--ac-focus":"#2563eb","--ac-rollback":"#b91c1c","--ac-tg-now":"#dc2626"},dark:{"--ac-fg":"#e6e8eb","--ac-muted":"#9aa1ab","--ac-faint":"#868e99","--ac-bg":"#14161a","--ac-header-fg":"#b3b9c2","--ac-border":"#2a2e35","--ac-cell-bg":"#171a1f","--ac-cell-bg-outside":"#111318","--ac-today-marker-bg":"#e6e8eb","--ac-today-marker-fg":"#14161a","--ac-event-bg":"#242a32","--ac-event-fg":"#e6e8eb","--ac-event-accent":"#8b98a9","--ac-more-fg":"#b3b9c2","--ac-focus":"#6ea8fe","--ac-rollback":"#f87171","--ac-tg-now":"#f87171"},midnight:{"--ac-fg":"#dfe4ea","--ac-muted":"#8b95a1","--ac-faint":"#828a95","--ac-bg":"#0b0f14","--ac-header-fg":"#a7b0bd","--ac-border":"#1c232c","--ac-cell-bg":"#0e131a","--ac-cell-bg-outside":"#090d12","--ac-today-marker-bg":"#dfe4ea","--ac-today-marker-fg":"#0b0f14","--ac-event-bg":"#17212c","--ac-event-fg":"#dfe4ea","--ac-event-accent":"#7f8ea3","--ac-more-fg":"#a7b0bd","--ac-focus":"#74a9ff","--ac-rollback":"#fb7185","--ac-tg-now":"#fb7185"},high_contrast:{"--ac-fg":"#000000","--ac-muted":"#000000","--ac-faint":"#1a1a1a","--ac-bg":"#ffffff","--ac-header-fg":"#000000","--ac-border":"#000000","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#ffffff","--ac-today-marker-bg":"#000000","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#e0e0e0","--ac-event-fg":"#000000","--ac-event-accent":"#000000","--ac-more-fg":"#000000","--ac-focus":"#0033cc","--ac-rollback":"#b00000","--ac-tg-now":"#d00000"}};var St=Un,Kn=["light","dark","midnight","high_contrast"],Ha=new Set(Kn),_a={"--ac-font":'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',"--ac-radius":"8px","--ac-cell-min-height":"96px"},$a={"--ac-tg-gutter":"56px","--ac-tg-body-height":"640px","--ac-tg-hour-min-height":"44px","--ac-tg-line":"var(--ac-border)","--ac-tg-event-bg":"var(--ac-event-bg)","--ac-tg-event-fg":"var(--ac-event-fg)","--ac-tg-event-accent":"var(--ac-event-accent)"},Ba={"--ac-tl-rowhead-width":"168px","--ac-tl-lane-height":"30px","--ac-tl-body-height":"560px","--ac-tl-line":"var(--ac-border)","--ac-tl-event-bg":"var(--ac-event-bg)","--ac-tl-event-fg":"var(--ac-event-fg)","--ac-tl-event-accent":"var(--ac-event-accent)","--ac-tl-group-bg":"var(--ac-cell-bg-outside)","--ac-tl-now":"var(--ac-tg-now)"},Yn=["--ac-tg-now"],Va=/[;{}<>]/;function Wn(e){return typeof e=="string"&&Ha.has(e)}function dn(e){return Object.entries(e).map(([t,n])=>`  ${t}: ${n};`).join(`
`)}function Ua(){let e={};for(let[t,n]of Object.entries(St.light))Yn.includes(t)||(e[t]=n);return e}function Xn(){let e={};for(let t of Yn){let n=St.light[t];n!==void 0&&(e[t]=n)}return e}function cn(){return dn({..._a,...Ua()})}function un(){return dn({...$a,...Xn()})}function gn(){return dn({...Ba,...Xn()})}function Ka(e){let t={};for(let[n,a]of Object.entries(e))n.startsWith("--ac-")&&(typeof a!="string"||a.trim()===""||Va.test(a)||(t[n]=a));return t}function mn(e){return e===void 0?{}:typeof e=="string"?Wn(e)?{...St[e]}:{}:Ka(e)}var Jn="aethercal-calendar-styles",jn=`
:where(.aethercal-calendar, .aethercal-calendar-shell) {
${cn()}
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
`;function je(){if(typeof document>"u"||document.getElementById(Jn))return;let e=document.createElement("style");e.id=Jn,e.textContent=jn,document.head.appendChild(e)}import*as J from"react";function Ee(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(L(e))}function qn(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function Zn(e,t){if(e.length===0)return"";let n=L(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(n);let a=L(e[e.length-1]),r=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${r.format(n)} \u2013 ${r.format(a)}`}var Qn="aethercal-timegrid-styles",ea=`
:where(.aethercal-timegrid) {
${un()}
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
`;function pn(){if(typeof document>"u"||document.getElementById(Qn))return;let e=document.createElement("style");e.id=Qn,e.textContent=ea,document.head.appendChild(e)}import{Fragment as aa,jsx as fe,jsxs as $e}from"react/jsx-runtime";function At(...e){return e.filter(Boolean).join(" ")}var Le=e=>`${e*100}%`,ta=new Set;function na(e){let t=L(e);return t.getHours()*60+t.getMinutes()}function fn(e,t,n){let a=L(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}function Ya(e,t,n,a){let{event:r,isContinuation:o,continuesAfter:i}=e;return o?i?n:a(me(r.end,t)):me(r.start,t)}function Pt(e,t){let n=t.getBoundingClientRect();return n.height>0?(e-n.top)/n.height:0}function vn(e){let{view:t,days:n,events:a,locale:r,config:o,now:i,themeVars:d,onEventDrop:s,onEventResize:l,onRangeSelect:u,onEventClick:p,onContextMenu:D,pendingIds:w=ta,rolledBackIds:E=ta}=e,v=J.useMemo(()=>{if(e.messages)return e.messages;let c={...e.allDayLabel!==void 0?{allDay:e.allDayLabel}:{},...e.continuesLabel!==void 0?{continues:e.continuesLabel}:{},...e.formatEndsLabel!==void 0?{endsAt:e.formatEndsLabel}:{}};return Je(r,c)},[e.messages,e.allDayLabel,e.continuesLabel,e.formatEndsLabel,r]);J.useEffect(()=>{je(),pn()},[]);let k=J.useMemo(()=>Xt(n,a,o),[n,a,o]),_=J.useMemo(()=>Jt(i,o),[i,o]),Q=J.useMemo(()=>ue(ae(i)),[i]),[R,O]=J.useReducer(gt,nt),P=J.useRef(null),[N,y]=J.useState(null),[b,G]=J.useState(null),$=!!s,S=!!l,W=!!u,C=R.status==="dragging",te=J.useCallback((c,m)=>M=>{if(M.preventDefault(),R.status!=="dragging"){O({type:"COMMIT"});return}let U=R.eventId,F=M.dataTransfer.getData("text/plain");if(O({type:"COMMIT"}),F&&F!==U||!s)return;let A=a.find(De=>De.id===U);if(!A||A.editable===!1)return;let I=null;if(m&&A.allDay!==!0){let he=M.currentTarget.getBoundingClientRect();he.height>0&&Number.isFinite(M.clientY)&&(I=We((M.clientY-he.top)/he.height,k.config))}s(Ne(A,c,I))},[R,a,s,k.config]),B=J.useCallback(c=>{P.current?.kind!=="resize"&&O({type:"DRAG_START",eventId:c})},[]),re=J.useCallback(()=>O({type:"CANCEL"}),[]),K=J.useCallback((c,m)=>M=>{if(!l||c.editable===!1||M.button!==0||P.current)return;let U=M.currentTarget.closest(".aethercal-tg-col");U?.dataset.date&&(M.preventDefault(),M.stopPropagation(),P.current={kind:"resize",pointerId:M.pointerId,eventId:c.id,edge:m,dateOnly:U.dataset.date,colEl:U,payload:null},M.currentTarget.setPointerCapture?.(M.pointerId),O({type:"RESIZE_START",eventId:c.id,edge:m}))},[l]),z=J.useCallback(c=>m=>{if(!u||m.button!==0||P.current||m.target.closest("[data-event-id], button"))return;let M=m.currentTarget,U=We(Pt(m.clientY,M),k.config);P.current={kind:"select",pointerId:m.pointerId,anchorDate:c,anchorCol:M,anchorMinute:U,currentDate:c,currentCol:M,currentMinute:U},M.setPointerCapture?.(m.pointerId),O({type:"SELECT_START",point:{dateOnly:c,minuteOfDay:U}})},[u,k.config]),g=R.status==="resizing"||R.status==="selecting";J.useLayoutEffect(()=>{if(!g)return;let c=A=>{let I=P.current;if(!(!I||A.pointerId!==I.pointerId))if(I.kind==="resize"){let De=document.elementFromPoint(A.clientX,A.clientY)?.closest(".aethercal-tg-col"),he=De?.dataset.date?De:I.colEl,Qe=We(Pt(A.clientY,he),k.config),st=a.find(Ft=>Ft.id===I.eventId);if(!st)return;let Oe=ke(st,I.edge,he.dataset.date??I.dateOnly,Qe);I.payload=Oe,y(Oe)}else{let De=document.elementFromPoint(A.clientX,A.clientY)?.closest(".aethercal-tg-col"),he=De?.dataset.date?De:I.currentCol;I.currentCol=he,I.currentDate=he.dataset.date??I.anchorDate,I.currentMinute=We(Pt(A.clientY,he),k.config);let Qe=Ae({dateOnly:I.anchorDate,minuteOfDay:I.anchorMinute},{dateOnly:I.currentDate,minuteOfDay:I.currentMinute}),Oe=(I.currentDate===I.anchorDate?dt([{id:"__sel",title:"",start:Qe.start,end:Qe.end}],I.anchorDate,k.config):[])[0];G(Oe?{dateOnly:I.anchorDate,topFraction:Oe.topFraction,heightFraction:Oe.heightFraction}:null)}},m=A=>{let I=P.current;P.current=null,y(null),G(null),A&&I&&(I.kind==="resize"&&I.payload&&l&&l(I.payload),I.kind==="select"&&u&&(I.currentDate!==I.anchorDate||I.currentMinute!==I.anchorMinute)&&u(Ae({dateOnly:I.anchorDate,minuteOfDay:I.anchorMinute},{dateOnly:I.currentDate,minuteOfDay:I.currentMinute}))),O({type:A?"COMMIT":"CANCEL"})},M=A=>{P.current&&A.pointerId!==P.current.pointerId||m(!0)},U=A=>{P.current&&A.pointerId!==P.current.pointerId||m(!1)},F=A=>{A.key==="Escape"&&m(!1)};return window.addEventListener("pointermove",c),window.addEventListener("pointerup",M),window.addEventListener("pointercancel",U),window.addEventListener("keydown",F),()=>{window.removeEventListener("pointermove",c),window.removeEventListener("pointerup",M),window.removeEventListener("pointercancel",U),window.removeEventListener("keydown",F)}},[g,a,k.config,l,u]);let f=J.useCallback((c,m)=>M=>{if(!D||M.target.closest("[data-event-id], button"))return;if(M.preventDefault(),!m){D({start:`${c}T00:00:00`});return}let U=We(Pt(M.clientY,M.currentTarget),k.config),F=L(`${c}T00:00:00`),A=new Date(F.getFullYear(),F.getMonth(),F.getDate(),0,U,0);D({start:ae(A)})},[D,k.config]),q=J.useId(),Z=J.useMemo(()=>k.columns.map(c=>c.dateOnly),[k.columns]),[T,H]=J.useState(()=>(Z.includes(Q)?Q:Z[0])??""),[x,V]=J.useState(null),[j,de]=J.useState(null),[Re,ge]=J.useState("");J.useEffect(()=>{Z.includes(T)||(H(Z[0]??""),V(null),de(null))},[Z,T]);let Y=c=>`${q}-col-${c}`,h=(c,m)=>`${q}-e-${c}-${m}`,X=`${q}-hint`,se=xe,ee=J.useCallback(c=>!!p||c.editable!==!1&&!!(s||l),[p,s,l]),oe=J.useMemo(()=>{let c=k.columns.find(m=>m.dateOnly===T);return c?[...c.allDay,...c.timed.map(m=>m.event)]:[]},[k.columns,T]),ne=J.useMemo(()=>oe.filter(c=>ee(c)),[oe,ee]);J.useEffect(()=>{let c=new Set(ne.map(m=>m.id));j&&!c.has(j.eventId)?(de(null),V(null)):!j&&x!==null&&!c.has(x)&&V(null)},[ne,x,j]);let Ze=j?h(T,j.eventId):x?h(T,x):Y(T),ft=J.useCallback(c=>{let m=j;if(!m)return;let M=m.dateOnly,U=m.minute,F=a.find(I=>I.id===m.eventId),A=F?.allDay===!0;if(!A&&(c==="ArrowUp"||c==="ArrowDown")){let I=Wt(M,U,c==="ArrowUp"?-se:se,k.config);M=I.dateOnly,U=I.minuteOfDay}else c==="ArrowLeft"?M=be(M,-1):c==="ArrowRight"&&(M=be(M,1));if(!(M===m.dateOnly&&U===m.minute)){if(F)if(m.kind==="move")ge(v.movedTo(A?Ee(M,r):`${Ee(M,r)} ${fn(M,U,r)}`));else{let I=ke(F,"end",M,U);ge(v.resizedTo(`${me(I.start,r)} \u2013 ${me(I.end,r)}`))}de({...m,dateOnly:M,minute:U,moved:!0})}},[j,se,k.config,a,v,r]),vt=J.useCallback(()=>{let c=j;if(!c)return;if(!c.moved){V(c.eventId),de(null);return}let m=a.find(M=>M.id===c.eventId);if(m&&m.editable!==!1&&c.kind==="move"&&s){let M=Ne(m,c.dateOnly,m.allDay===!0?null:c.minute);s(M);let U=ue(M.start);H(Z.includes(U)?U:T),V(null),ge(v.dropped(m.allDay===!0?Ee(c.dateOnly,r):fn(c.dateOnly,c.minute,r)))}else if(m&&m.editable!==!1&&c.kind==="resize"&&l){let M=ke(m,"end",c.dateOnly,c.minute);l(M),V(c.eventId),ge(v.resized(`${me(M.start,r)} \u2013 ${me(M.end,r)}`))}else V(c.eventId);de(null)},[j,a,s,l,Z,T,v,r]),Nt=J.useCallback(c=>{let{key:m}=c,M=m==="Enter"||m===" "||m==="Spacebar",U=m==="ArrowUp"||m==="ArrowDown"||m==="ArrowLeft"||m==="ArrowRight";if(j){if(U){c.preventDefault(),ft(m);return}if(M){c.preventDefault(),vt();return}if(m==="Escape"){c.preventDefault(),de(null),ge(v.cancelled);return}return}if(x){let F=ne.findIndex(A=>A.id===x);if(m==="ArrowDown"){c.preventDefault(),F>=0&&F<ne.length-1&&V(ne[F+1].id);return}if(m==="ArrowUp"){c.preventDefault(),F>0?V(ne[F-1].id):V(null);return}if(m==="ArrowLeft"||m==="ArrowRight"){c.preventDefault(),V(null);let A=Z.indexOf(T);H(Z[Ke(A,m,1,Z.length)]);return}if(M){c.preventDefault();let A=ne.find(I=>I.id===x);if(!A)return;A.editable!==!1&&s?(de({kind:"move",eventId:A.id,dateOnly:ue(A.start),minute:na(A.start),moved:!1}),ge(v.grabbedMoveHint(A.title))):p&&p({id:A.id});return}if((m==="r"||m==="R")&&l){c.preventDefault();let A=ne.find(I=>I.id===x);A&&A.allDay!==!0&&A.editable!==!1&&(de({kind:"resize",eventId:A.id,dateOnly:ue(A.end),minute:na(A.end),moved:!1}),ge(v.grabbedResizeHint(A.title)));return}if(m==="Escape"){c.preventDefault(),V(null);return}return}if(m==="ArrowLeft"||m==="ArrowRight"||m==="Home"||m==="End"){c.preventDefault();let F=Z.indexOf(T);H(Z[Ke(F,m,1,Z.length)]);return}if(m==="ArrowDown"){ne.length>0&&(c.preventDefault(),V(ne[0].id));return}if(M){if(ne.length>0)c.preventDefault(),V(ne[0].id);else if(oe.length===0&&u){let F=k.config.dayEndHour*60,A=bt(k.config.dayStartHour*60,k.config),I=Math.min(A+60,F);I>A&&(c.preventDefault(),u(Ae({dateOnly:T,minuteOfDay:A},{dateOnly:T,minuteOfDay:I})),ge(v.createHere(`${Ee(T,r)} ${fn(T,A,r)}`)))}}},[j,x,T,oe,ne,Z,s,l,p,u,ft,vt,k.config,v,r]),ce={"--ac-tg-cols":k.columns.length,"--ac-tg-hours":k.config.dayEndHour-k.config.dayStartHour,...d??{}},Gt=v.allDay;return $e(aa,{children:[$e("div",{className:At("aethercal-calendar","aethercal-timegrid",C&&"is-dragging",R.status==="resizing"&&"is-resizing",R.status==="selecting"&&"is-selecting"),role:"grid","aria-label":Zn(n,r),"aria-describedby":X,"aria-activedescendant":Ze,tabIndex:0,"data-view":t,style:ce,onKeyDown:Nt,children:[$e("div",{className:"aethercal-tg-head",role:"row",children:[fe("div",{className:"aethercal-tg-corner"}),k.columns.map(c=>fe("div",{role:"columnheader",className:At("aethercal-tg-colhead",c.dateOnly===Q&&"is-today"),"data-date":c.dateOnly,children:fe("span",{className:"aethercal-tg-colhead-date",children:Ee(c.dateOnly,r)})},c.dateOnly))]}),$e("div",{className:"aethercal-tg-allday",role:"row",children:[fe("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:Gt}),k.columns.map(c=>fe("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":c.dateOnly,onDragOver:$?m=>m.preventDefault():void 0,onDrop:$?te(c.dateOnly,!1):void 0,onContextMenu:D?f(c.dateOnly,!1):void 0,children:c.allDay.map(m=>{let M=j?.eventId===m.id&&c.dateOnly===T||!j&&x===m.id&&c.dateOnly===T;return fe(It,{id:h(c.dateOnly,m.id),event:m,interactive:ee(m),isActive:M,isGrabbed:j?.eventId===m.id&&c.dateOnly===T,timeLabel:null,canDrag:$,onDragStart:B,onDragEnd:re,isPending:w.has(m.id),isRolledBack:E.has(m.id),...p?{onClick:()=>p({id:m.id})}:{},...D?{onContextMenu:()=>D({id:m.id})}:{}},m.id)})},c.dateOnly))]}),$e("div",{className:"aethercal-tg-body",role:"row",tabIndex:0,children:[fe("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:k.hourMarks.map(c=>fe("div",{className:"aethercal-tg-hour",style:{top:Le(c.topFraction)},children:qn(c.hour,r)},c.hour))}),k.columns.map(c=>{let m=!x&&!j&&c.dateOnly===T,M=j?.dateOnly===c.dateOnly;return $e("div",{id:Y(c.dateOnly),role:"gridcell",className:At("aethercal-tg-col",c.dateOnly===Q&&"is-today",m&&"is-active",M&&"is-drop-target"),"data-date":c.dateOnly,onDragOver:$?U=>U.preventDefault():void 0,onDrop:$?te(c.dateOnly,!0):void 0,onPointerDown:W?z(c.dateOnly):void 0,onContextMenu:D?f(c.dateOnly,!0):void 0,children:[k.hourMarks.map(U=>fe("div",{className:"aethercal-tg-line",style:{top:Le(U.topFraction)},"aria-hidden":"true"},U.hour)),b&&b.dateOnly===c.dateOnly?fe("div",{className:"aethercal-tg-select-band",style:{top:Le(b.topFraction),height:Le(b.heightFraction)},"aria-hidden":"true"}):null,c.timed.map(U=>{let{event:F}=U,A=F.editable!==!1,I=Ya(U,r,v.continues,v.endsAt),De=N?.id===F.id?N:null,he=De?dt([{...F,start:De.start,end:De.end}],c.dateOnly,k.config)[0]:void 0,Qe=he?he.topFraction:U.topFraction,st=he?he.heightFraction:U.heightFraction,Oe=j?.eventId===F.id&&c.dateOnly===T||!j&&x===F.id&&c.dateOnly===T,Ft=j?.eventId===F.id&&c.dateOnly===T,ma={top:Le(Qe),height:Le(st),left:Le(U.lane/U.laneCount),width:Le(1/U.laneCount),...F.color?{"--ac-tg-event-accent":F.color}:{}};return $e("div",{id:h(c.dateOnly,F.id),className:At("aethercal-tg-event",!A&&"is-locked",w.has(F.id)&&"is-pending",E.has(F.id)&&"is-rolledback",!!De&&"is-resizing",Oe&&"is-active",Ft&&"is-grabbed"),...ee(F)?{role:"button"}:{},draggable:A&&$,"data-event-id":F.id,"data-lane":U.lane,"data-lane-count":U.laneCount,"aria-label":`${I} ${F.title}`,title:F.title,style:ma,onDragStart:et=>{if(!$||P.current?.kind==="resize"){et.preventDefault();return}et.dataTransfer.setData("text/plain",F.id),et.dataTransfer.effectAllowed="move",B(F.id)},onDragEnd:re,onClick:p?()=>p({id:F.id}):void 0,onContextMenu:D?et=>{et.preventDefault(),et.stopPropagation(),D({id:F.id})}:void 0,children:[fe("time",{className:"aethercal-tg-event-time",children:I})," ",fe("span",{className:"aethercal-tg-event-title",children:F.title}),S&&A?$e(aa,{children:[fe("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:K(F,"start")}),fe("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:K(F,"end")})]}):null]},F.id)}),_!==null&&c.dateOnly===Q?fe("div",{className:"aethercal-now-indicator",style:{top:Le(_)},"aria-hidden":"true"}):null]},c.dateOnly)})]})]}),fe(rt,{id:X,text:v.keyboardHint}),fe(at,{message:Re})]})}import*as le from"react";function Ce(...e){return e.filter(Boolean).join(" ")}var Me=e=>`${e*100}%`,yn=new Set,Wa="unassigned",ra=e=>e.resource?`r:${e.resource.id}`:Wa;function pt(e,t){let n=t.getBoundingClientRect();return n.width>0?(e-n.left)/n.width:0}function hn(e){let t=L(e);return t.getHours()*60+t.getMinutes()}function Lt(e,t,n){let a=L(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}import{Fragment as Xa,jsx as pe,jsxs as qe}from"react/jsx-runtime";function oa(e){let{dayHeaders:t,nowDateKey:n,locale:a,resourcesLabel:r}=e;return qe("div",{className:"aethercal-tl-head",role:"row",children:[pe("div",{className:"aethercal-tl-corner",role:"columnheader",children:r}),pe("div",{className:"aethercal-tl-days",children:t.map(o=>pe("div",{role:"columnheader",className:Ce("aethercal-tl-dayhead",o.dateOnly===n&&"is-today"),"data-date":o.dateOnly,style:{left:Me(o.leftFraction),width:Me(o.widthFraction)},children:pe("span",{children:Ee(o.dateOnly,a)})},o.dateOnly))})]})}function ia(e){let{group:t,domId:n,isActive:a,countLabel:r,onToggle:o}=e;return pe("div",{role:"row",className:Ce("aethercal-tl-group",t.collapsed&&"is-collapsed"),children:pe("div",{className:"aethercal-tl-group-head",role:"rowheader",children:qe("button",{type:"button",id:n,className:Ce("aethercal-tl-group-toggle",a&&"is-active"),"aria-expanded":!t.collapsed,tabIndex:-1,onClick:o,children:[pe("span",{className:"aethercal-tl-caret","aria-hidden":"true",children:"\u25BE"}),pe("span",{children:t.id})," ",pe("span",{className:"aethercal-tl-group-count",children:r})]})})})}function sa(e){let{row:t,days:n,config:a,ticks:r,nowFraction:o,locale:i,messages:d,rowDomId:s,evtDomId:l,isRowActive:u,isCurrentRow:p,activeEventId:D,kbGrab:w,isKbTarget:E,selectBand:v,resizePreview:k,pendingIds:_,rolledBackIds:Q,dropEnabled:R,resizeEnabled:O,selectEnabled:P,eventInteractive:N,onDrop:y,onPointerDown:b,onTrackContextMenu:G,beginDrag:$,endDrag:S,startResize:W,onEventClick:C,onEventContextMenu:te}=e,B={"--ac-tl-lanes":t.laneCount},re=t.resource?.color?{"--ac-tl-row-accent":t.resource.color}:{};return qe("div",{role:"row",className:Ce("aethercal-tl-row",!t.resource&&"is-unassigned"),children:[qe("div",{id:s,role:"rowheader",className:Ce("aethercal-tl-rowhead",u&&"is-active"),style:re,children:[t.resource?.color?pe("span",{className:"aethercal-tl-swatch","aria-hidden":"true"}):null,pe("span",{className:"aethercal-tl-rowhead-title",children:t.resource?t.resource.title:d.timelineUnassigned})]}),qe("div",{role:"gridcell",className:Ce("aethercal-tl-track",E&&"is-drop-target"),"data-resource-id":t.resource?.id??"",style:B,onDragOver:R&&t.resource?K=>K.preventDefault():void 0,onDrop:R&&t.resource?y:void 0,onPointerDown:P&&t.resource?b:void 0,onContextMenu:G,children:[r.map(K=>pe("div",{className:Ce("aethercal-tl-line",K.isDayStart&&"is-day-start"),style:{left:Me(K.leftFraction)},"aria-hidden":"true"},`${K.dateOnly}-${K.hour}`)),v&&v.resourceId===t.resource?.id?pe("div",{className:"aethercal-tl-select-band",style:{left:Me(v.leftFraction),width:Me(v.widthFraction)},"aria-hidden":"true"}):null,t.blocks.map(K=>{let{event:z}=K,g=z.editable!==!1,f=k?.id===z.id?k:null,q=w?.eventId===z.id||!w&&D===z.id&&p,Z=K.allDay?d.allDay:me(f?.start??z.start,i),T=f?jt({...z,start:f.start,end:f.end},n,a)[0]:void 0,H={left:Me(T?.leftFraction??K.leftFraction),width:Me(T?.widthFraction??K.widthFraction),top:Me(K.lane/K.laneCount),height:Me(1/K.laneCount),...z.color?{"--ac-tl-event-accent":z.color}:{}};return qe("div",{id:l(z.id),className:Ce("aethercal-tl-event",K.allDay&&"is-allday",!g&&"is-locked",K.continuesBefore&&"continues-before",K.continuesAfter&&"continues-after",_.has(z.id)&&"is-pending",Q.has(z.id)&&"is-rolledback",!!f&&"is-resizing",q&&"is-active",w?.eventId===z.id&&"is-grabbed"),...N(z)?{role:"button"}:{},draggable:g&&R,"data-event-id":z.id,"data-lane":K.lane,"aria-label":`${Z} ${z.title}`,title:z.title,style:H,onDragStart:x=>{if(!$(z.id)){x.preventDefault();return}x.dataTransfer.setData("text/plain",z.id),x.dataTransfer.effectAllowed="move"},onDragEnd:S,onClick:C?()=>C(z.id):void 0,onContextMenu:te?x=>{x.preventDefault(),x.stopPropagation(),te(z.id)}:void 0,children:[pe("time",{className:"aethercal-tl-event-time",children:Z})," ",pe("span",{className:"aethercal-tl-event-title",children:z.title}),O&&g&&!K.allDay?qe(Xa,{children:[pe("div",{className:"aethercal-tl-resize-handle aethercal-tl-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:W(z,"start")}),pe("div",{className:"aethercal-tl-resize-handle aethercal-tl-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:W(z,"end")})]}):null]},z.id)}),o!==null?pe("div",{className:"aethercal-tl-now",style:{left:Me(o)},"aria-hidden":"true"}):null]})]})}var la="aethercal-timeline-styles",da=`
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
`;function bn(){if(typeof document>"u"||document.getElementById(la))return;let e=document.createElement("style");e.id=la,e.textContent=da,document.head.appendChild(e)}import*as ve from"react";function ca(e){let{timeline:t,days:n,events:a,dropRows:r,locale:o,messages:i,eventInteractive:d,axisFractionOf:s,toggleGroup:l,announce:u,itemDomId:p,evtDomId:D,onEventDrop:w,onEventResize:E,onRangeSelect:v,onEventClick:k}=e,_=xe,[Q,R]=ve.useState(0),[O,P]=ve.useState(0),[N,y]=ve.useState(null),[b,G]=ve.useState(null);ve.useEffect(()=>{Q>t.items.length-1&&(R(Math.max(0,t.items.length-1)),y(null),G(null))},[t.items.length,Q]),ve.useEffect(()=>{O>n.length-1&&P(Math.max(0,n.length-1))},[n.length,O]);let $=t.items[Q],S=$?.kind==="row"?$.row:void 0,W=ve.useMemo(()=>(S?.blocks??[]).map(g=>g.event).filter(g=>d(g)),[S,d]),C=ve.useMemo(()=>{let g=t.dayHeaders[O];if(!g||!S)return[];let f=g.leftFraction,q=g.leftFraction+g.widthFraction,Z=1e-9;return S.blocks.filter(T=>{let H=T.leftFraction,x=T.leftFraction+T.widthFraction;return x>H?H<q-Z&&x>f+Z:H>=f-Z&&H<q-Z}).map(T=>T.event).filter(T=>d(T))},[t.dayHeaders,O,S,d]);ve.useEffect(()=>{let g=new Set(W.map(f=>f.id));b&&!g.has(b.eventId)?(G(null),y(null)):!b&&N!==null&&!g.has(N)&&y(null)},[W,N,b]);let te=t.items.length===0?void 0:b?D(b.eventId):N?D(N):p(Q),B=ve.useCallback(g=>r.find(f=>f.resource?.id===g)?.resource?.title??g,[r]),re=ve.useCallback(g=>{let f=b;if(!f)return;let q=a.find(V=>V.id===f.eventId);if(!q)return;let Z=q.allDay===!0,T=f.dateOnly,H=f.minute,x=f.kind==="move"?f.resourceId:"";if(g==="ArrowLeft"||g==="ArrowRight")if(Z)T=be(T,g==="ArrowLeft"?-1:1);else{let V=g==="ArrowLeft"?-_:_,j=ze(s(T,H+V),n,t.config,_);if(!j)return;T=j.dateOnly,H=j.minuteOfDay??H}else if(f.kind==="move"&&(g==="ArrowUp"||g==="ArrowDown")){let V=r.findIndex(de=>de.resource?.id===x),j=g==="ArrowUp"?V-1:V+1;if(V===-1||j<0||j>=r.length)return;x=r[j].resource.id}else return;if(!(T===f.dateOnly&&H===f.minute&&(f.kind!=="move"||x===f.resourceId)))if(f.kind==="move"){let V=Z?Ee(T,o):`${Ee(T,o)} ${Lt(T,H,o)}`;u(i.movedTo(`${B(x)} \xB7 ${V}`)),G({...f,dateOnly:T,minute:H,resourceId:x,moved:!0})}else{let V=ke(q,"end",T,H);u(i.resizedTo(`${me(V.start,o)} \u2013 ${me(V.end,o)}`)),G({...f,dateOnly:T,minute:H,moved:!0})}},[b,a,_,n,t.config,r,s,B,u,i,o]),K=ve.useCallback(()=>{let g=b;if(!g)return;if(!g.moved){y(g.eventId),G(null);return}let f=a.find(q=>q.id===g.eventId);if(f&&f.editable!==!1&&g.kind==="move"&&w){let q=f.allDay===!0?null:g.minute;w(Ne(f,g.dateOnly,q,g.resourceId)),u(i.dropped(`${B(g.resourceId)} \xB7 ${f.allDay===!0?Ee(g.dateOnly,o):Lt(g.dateOnly,g.minute,o)}`)),y(null)}else if(f&&f.editable!==!1&&g.kind==="resize"&&E){let q=ke(f,"end",g.dateOnly,g.minute);E(q),u(i.resized(`${me(q.start,o)} \u2013 ${me(q.end,o)}`)),y(g.eventId)}else y(g.eventId);G(null)},[b,a,w,E,B,u,i,o]),z=ve.useCallback(g=>{let{key:f}=g,q=f==="Enter"||f===" "||f==="Spacebar",Z=f==="ArrowUp"||f==="ArrowDown"||f==="ArrowLeft"||f==="ArrowRight",T=t.items.length-1;if(b){if(Z){g.preventDefault(),re(f);return}if(q){g.preventDefault(),K();return}f==="Escape"&&(g.preventDefault(),G(null),u(i.cancelled));return}if(N){let H=W.findIndex(x=>x.id===N);if(f==="ArrowRight"){g.preventDefault(),H>=0&&H<W.length-1&&y(W[H+1].id);return}if(f==="ArrowLeft"){g.preventDefault(),H>0?y(W[H-1].id):y(null);return}if(f==="ArrowUp"||f==="ArrowDown"){g.preventDefault(),y(null),R(x=>Math.min(Math.max(x+(f==="ArrowUp"?-1:1),0),T));return}if(q){g.preventDefault();let x=W.find(V=>V.id===N);if(!x)return;x.editable!==!1&&w&&S?.resource?(G({kind:"move",eventId:x.id,dateOnly:ue(x.start),minute:hn(x.start),resourceId:S.resource.id,moved:!1}),u(i.grabbedMoveHint(x.title))):k&&k({id:x.id});return}if((f==="r"||f==="R")&&E){g.preventDefault();let x=W.find(V=>V.id===N);x&&x.allDay!==!0&&x.editable!==!1&&(G({kind:"resize",eventId:x.id,dateOnly:ue(x.end),minute:hn(x.end),moved:!1}),u(i.grabbedResizeHint(x.title)));return}f==="Escape"&&(g.preventDefault(),y(null));return}if(f==="ArrowUp"||f==="ArrowDown"){g.preventDefault(),R(H=>Math.min(Math.max(H+(f==="ArrowUp"?-1:1),0),T));return}if(f==="ArrowLeft"||f==="ArrowRight"){g.preventDefault(),P(H=>Math.min(Math.max(H+(f==="ArrowLeft"?-1:1),0),Math.max(0,n.length-1)));return}if(f==="Home"||f==="End"){g.preventDefault(),P(f==="Home"?0:Math.max(0,n.length-1));return}if(q){if($?.kind==="group"){g.preventDefault(),l($.group.id);return}if(C.length>0){g.preventDefault(),y(C[0].id);return}if(S?.resource&&v&&n.length>0){let H=n[Math.min(O,n.length-1)],x=t.config.dayStartHour*60,V=Math.min(x+60,t.config.dayEndHour*60);V>x&&(g.preventDefault(),v(Ae({dateOnly:H,minuteOfDay:x,resourceId:S.resource.id},{dateOnly:H,minuteOfDay:V,resourceId:S.resource.id})),u(i.createHere(`${S.resource.title} \xB7 ${Ee(H,o)} ${Lt(H,x,o)}`)))}}},[b,N,W,C,$,S,t.items.length,t.config,n,O,w,E,k,v,re,K,l,u,i,o]);return{activeItem:Q,activeEventId:N,kbGrab:b,currentRow:S,activeDescendantId:te,handleKeyDown:z}}import*as ye from"react";function ua(e){let{days:t,config:n,events:a,axisFractionOf:r,onEventDrop:o,onEventResize:i,onRangeSelect:d,onContextMenu:s}=e,[l,u]=ye.useReducer(gt,nt),p=ye.useRef(null),[D,w]=ye.useState(null),[E,v]=ye.useState(null),k=ye.useCallback(y=>b=>{if(b.preventDefault(),l.status!=="dragging"){u({type:"COMMIT"});return}let G=l.eventId,$=b.dataTransfer.getData("text/plain");if(u({type:"COMMIT"}),$&&$!==G||!o||!y.resource)return;let S=a.find(te=>te.id===G);if(!S||S.editable===!1)return;let W=ze(pt(b.clientX,b.currentTarget),t,n);if(!W)return;let C=S.allDay===!0?null:W.minuteOfDay;o(Ne(S,W.dateOnly,C,y.resource.id))},[l,a,o,t,n]),_=ye.useCallback(y=>!o||p.current?.kind==="resize"?!1:(u({type:"DRAG_START",eventId:y}),!0),[o]),Q=ye.useCallback(()=>u({type:"CANCEL"}),[]),R=ye.useCallback((y,b)=>G=>{if(!i||y.editable===!1||G.button!==0||p.current)return;let $=G.currentTarget.closest(".aethercal-tl-track");$&&(G.preventDefault(),G.stopPropagation(),p.current={kind:"resize",pointerId:G.pointerId,eventId:y.id,edge:b,trackEl:$,payload:null},G.currentTarget.setPointerCapture?.(G.pointerId),u({type:"RESIZE_START",eventId:y.id,edge:b}))},[i]),O=ye.useCallback(y=>b=>{if(!d||b.button!==0||!y.resource||p.current||b.target.closest("[data-event-id], button"))return;let G=b.currentTarget,$=ze(pt(b.clientX,G),t,n);if(!$)return;let S=$.minuteOfDay??0;p.current={kind:"select",pointerId:b.pointerId,resourceId:y.resource.id,trackEl:G,anchorDate:$.dateOnly,anchorMinute:S,currentDate:$.dateOnly,currentMinute:S},G.setPointerCapture?.(b.pointerId),u({type:"SELECT_START",point:{dateOnly:$.dateOnly,minuteOfDay:S,resourceId:y.resource.id}})},[d,t,n]),P=l.status==="resizing"||l.status==="selecting";ye.useLayoutEffect(()=>{if(!P)return;let y=W=>{let C=p.current;if(!C||W.pointerId!==C.pointerId)return;let te=ze(pt(W.clientX,C.trackEl),t,n);if(!te)return;if(C.kind==="resize"){let K=a.find(g=>g.id===C.eventId);if(!K)return;let z=ke(K,C.edge,te.dateOnly,te.minuteOfDay??0);C.payload=z,w(z);return}C.currentDate=te.dateOnly,C.currentMinute=te.minuteOfDay??0;let B=r(C.anchorDate,C.anchorMinute),re=r(C.currentDate,C.currentMinute);v({resourceId:C.resourceId,leftFraction:Math.min(B,re),widthFraction:Math.abs(re-B)})},b=W=>{let C=p.current;p.current=null,w(null),v(null),W&&C&&(C.kind==="resize"&&C.payload&&i&&i(C.payload),C.kind==="select"&&d&&(C.currentDate!==C.anchorDate||C.currentMinute!==C.anchorMinute)&&d(Ae({dateOnly:C.anchorDate,minuteOfDay:C.anchorMinute,resourceId:C.resourceId},{dateOnly:C.currentDate,minuteOfDay:C.currentMinute,resourceId:C.resourceId}))),u({type:W?"COMMIT":"CANCEL"})},G=W=>{p.current&&W.pointerId!==p.current.pointerId||b(!0)},$=W=>{p.current&&W.pointerId!==p.current.pointerId||b(!1)},S=W=>{W.key==="Escape"&&b(!1)};return window.addEventListener("pointermove",y),window.addEventListener("pointerup",G),window.addEventListener("pointercancel",$),window.addEventListener("keydown",S),()=>{window.removeEventListener("pointermove",y),window.removeEventListener("pointerup",G),window.removeEventListener("pointercancel",$),window.removeEventListener("keydown",S)}},[P,a,t,n,r,i,d]);let N=ye.useCallback(y=>{if(!s||y.target.closest("[data-event-id], button"))return;let b=ze(pt(y.clientX,y.currentTarget),t,n);if(!b)return;y.preventDefault();let G=L(`${b.dateOnly}T00:00:00`),$=new Date(G.getFullYear(),G.getMonth(),G.getDate(),0,b.minuteOfDay??0,0);s({start:ae($)})},[s,t,n]);return{interaction:l,resizePreview:D,selectBand:E,handleDrop:k,beginDrag:_,endDrag:Q,startResize:R,startSelect:O,emptyContextMenu:N}}import{Fragment as Ja,jsx as Be,jsxs as ga}from"react/jsx-runtime";function Dn(e){let{days:t,resources:n,events:a,locale:r,config:o,now:i,themeVars:d,defaultCollapsedGroupIds:s,onToggleGroup:l,onEventDrop:u,onEventResize:p,onRangeSelect:D,onEventClick:w,onContextMenu:E,pendingIds:v=yn,rolledBackIds:k=yn}=e,_=le.useMemo(()=>e.messages??Je(r),[e.messages,r]);le.useEffect(()=>{je(),bn()},[]);let[Q,R]=le.useState(""),O=le.useCallback(h=>R(h),[]),[P,N]=le.useState(()=>new Set(s??[])),y=le.useMemo(()=>[...P],[P]),b=le.useMemo(()=>qt(n,a,t,{...o,collapsedGroupIds:y}),[n,a,t,o,y]),G=le.useMemo(()=>b.items.flatMap(h=>h.kind==="row"?[h.row]:[]),[b.items]),$=le.useMemo(()=>G.filter(h=>h.resource!==null),[G]),S=le.useMemo(()=>Zt(i,t,b.config),[i,t,b.config]),W=le.useMemo(()=>ue(ae(i)),[i]),C=!!u,te=!!p,B=!!D,re=le.useCallback((h,X)=>{let{windowMinutes:se,dayStartHour:ee}=b.config,oe=t.length*se;if(oe<=0)return 0;let ne=t.indexOf(h);return((ne===-1?0:ne)*se+(X-ee*60))/oe},[t,b.config]),K=le.useCallback(h=>{let X=!P.has(h);N(se=>{let ee=new Set(se);return ee.has(h)?ee.delete(h):ee.add(h),ee}),l?.(h,X),O(X?_.groupCollapsed(h):_.groupExpanded(h))},[P,l,O,_]),z=le.useCallback(h=>!!w||h.editable!==!1&&!!(u||p),[w,u,p]),g=le.useId(),f=`${g}-hint`,q=le.useCallback(h=>`${g}-i-${h}`,[g]),Z=le.useCallback(h=>`${g}-e-${h}`,[g]),T=ua({days:t,config:b.config,events:a,axisFractionOf:re,...u?{onEventDrop:u}:{},...p?{onEventResize:p}:{},...D?{onRangeSelect:D}:{},...E?{onContextMenu:E}:{}}),H=ca({timeline:b,days:t,events:a,dropRows:$,locale:r,messages:_,eventInteractive:z,axisFractionOf:re,toggleGroup:K,announce:O,itemDomId:q,evtDomId:Z,...u?{onEventDrop:u}:{},...p?{onEventResize:p}:{},...D?{onRangeSelect:D}:{},...w?{onEventClick:w}:{}}),{interaction:x}=T,{activeItem:V,activeEventId:j,kbGrab:de,currentRow:Re,activeDescendantId:ge}=H,Y={...d??{}};return ga(Ja,{children:[Be("div",{className:Ce("aethercal-calendar","aethercal-timeline",x.status==="dragging"&&"is-dragging",x.status==="resizing"&&"is-resizing",x.status==="selecting"&&"is-selecting"),"data-view":"timeline",style:Y,children:ga("div",{className:"aethercal-tl-body",role:"grid","aria-label":_.viewNames.timeline,"aria-describedby":f,...ge!==void 0?{"aria-activedescendant":ge}:{},tabIndex:0,onKeyDown:H.handleKeyDown,children:[Be(oa,{dayHeaders:b.dayHeaders,nowDateKey:W,locale:r,resourcesLabel:_.timelineResources}),b.items.length===0?Be("div",{className:"aethercal-tl-row aethercal-tl-row-empty",role:"row",children:Be("div",{role:"gridcell",className:"aethercal-tl-empty",children:_.timelineEmpty})}):null,b.items.map((h,X)=>{let se=!j&&!de&&X===V;if(h.kind==="group")return Be(ia,{group:h.group,domId:q(X),isActive:se,countLabel:_.timelineGroupCount(h.group.resourceCount),onToggle:()=>K(h.group.id)},`g:${h.group.id}`);let{row:ee}=h;return Be(sa,{row:ee,days:t,config:b.config,ticks:b.ticks,nowFraction:S,locale:r,messages:_,rowDomId:q(X),evtDomId:Z,isRowActive:se,isCurrentRow:Re===ee,activeEventId:j,kbGrab:de,isKbTarget:de?.kind==="move"&&ee.resource?.id===de.resourceId,selectBand:T.selectBand,resizePreview:T.resizePreview,pendingIds:v,rolledBackIds:k,dropEnabled:C,resizeEnabled:te,selectEnabled:B,eventInteractive:z,onDrop:T.handleDrop(ee),onPointerDown:T.startSelect(ee),...E?{onTrackContextMenu:T.emptyContextMenu}:{},beginDrag:T.beginDrag,endDrag:T.endDrag,startResize:T.startResize,...w?{onEventClick:oe=>w({id:oe})}:{},...E?{onEventContextMenu:oe=>E({id:oe})}:{}},ra(ee))})]})}),Be(rt,{id:f,text:_.timelineKeyboardHint}),Be(at,{message:Q})]})}import{jsx as it,jsxs as Qa}from"react/jsx-runtime";function ja(e){if(e instanceof Date)return e;if(typeof e=="string"){let t=e.trim();if(t==="")return new Date;try{return L(t)}catch{return new Date}}return new Date}function qa(e){return e instanceof Date?e:typeof e=="string"?L(e):new Date}function Ot(e){let{view:t="month",events:n,resources:a,timelineDays:r,defaultCollapsedGroupIds:o,onToggleGroup:i,anchor:d,locale:s="en",theme:l,messages:u,firstDayOfWeek:p=1,maxEventsPerDay:D=3,weekdayLabels:w,formatMore:E,unavailableLabel:v,dayStartHour:k,dayEndHour:_,allDayLabel:Q,now:R,continuesLabel:O,formatEndsLabel:P,agendaEmptyLabel:N,onEventDrop:y,onEventResize:b,onRangeSelect:G,onEventClick:$,onContextMenu:S,navigation:W=!1,navigationViews:C=!0,onRangeChange:te,onViewChange:B,pendingIds:re,rolledBackIds:K}=e;Ie.useEffect(()=>{je()},[]);let z=Ie.useMemo(()=>ja(d),[d]),g=Ie.useMemo(()=>mn(l),[l]),f=Ie.useMemo(()=>{let ge={...Q!==void 0?{allDay:Q}:{},...O!==void 0?{continues:O}:{},...P!==void 0?{endsAt:P}:{},...N!==void 0?{noEvents:N}:{},...v!==void 0?{unavailable:v}:{},...E!==void 0?{more:E}:{},...u};return Je(s,ge)},[s,Q,O,P,N,v,E,u]),[q,Z]=Ie.useState(()=>new Date);Ie.useEffect(()=>{if(R!==void 0||t!=="week"&&t!=="day"&&t!=="timeline")return;let ge=setInterval(()=>Z(new Date),6e4);return()=>clearInterval(ge)},[R,t]);let T=Ie.useMemo(()=>R!==void 0?qa(R):q,[R,q]),H=Number.isInteger(p)&&p>=0&&p<=6?p:1,x=Number.isInteger(D)&&D>=0?D:3,V=w&&w.length===7?w:void 0,j=Fe(r),de=Ie.useMemo(()=>({...k!==void 0?{dayStartHour:k}:{},..._!==void 0?{dayEndHour:_}:{}}),[k,_]),Re=(()=>{if(t==="list")return it(Nn,{events:n??[],locale:s,messages:f,themeVars:g});if(t==="month")return it(Vn,{events:n??[],anchor:z,locale:s,messages:f,themeVars:g,firstDayOfWeek:H,maxEventsPerDay:x,...V?{weekdayLabels:V}:{},...y?{onEventDrop:y}:{},...G?{onRangeSelect:G}:{},...$?{onEventClick:$}:{},...S?{onContextMenu:S}:{},...re?{pendingIds:re}:{},...K?{rolledBackIds:K}:{}});if(t==="timeline")return it(Dn,{days:$t(z,j),resources:a??[],events:n??[],locale:s,messages:f,themeVars:g,config:de,now:T,...o?{defaultCollapsedGroupIds:o}:{},...i?{onToggleGroup:i}:{},...y?{onEventDrop:y}:{},...b?{onEventResize:b}:{},...G?{onRangeSelect:G}:{},...$?{onEventClick:$}:{},...S?{onContextMenu:S}:{},...re?{pendingIds:re}:{},...K?{rolledBackIds:K}:{}});if(t==="week"||t==="day"){let ge=t==="week"?Ht(z,H):[ue(ae(z))];return it(vn,{view:t,days:ge,events:n??[],locale:s,messages:f,themeVars:g,config:de,now:T,...y?{onEventDrop:y}:{},...b?{onEventResize:b}:{},...G?{onRangeSelect:G}:{},...$?{onEventClick:$}:{},...S?{onContextMenu:S}:{},...re?{pendingIds:re}:{},...K?{rolledBackIds:K}:{}})}return it("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,style:g,children:f.unavailable})})();return W?Qa("div",{className:"aethercal-calendar-shell",style:g,children:[it(sn,{view:t,anchor:z,now:T,locale:s,firstDayOfWeek:H,timelineDays:j,messages:f,showViews:C,...te?{onRangeChange:te}:{},...B?{onViewChange:B}:{}}),Re]}):Re}var Za=Ot;import*as Te from"react";function er(){return typeof crypto<"u"&&typeof crypto.randomUUID=="function"?crypto.randomUUID():`cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}var tr=8e3,nr=900;function wn(e){let{events:t,mutate:n,timeoutMs:a=tr,rollbackFlashMs:r=nr,generateId:o=er}=e,[i,d]=Te.useReducer(tn,en),s=Te.useRef(t);s.current=t;let l=Te.useRef(!0),u=Te.useRef(new Map);Te.useEffect(()=>{l.current=!0;let w=u.current;return()=>{l.current=!1;for(let E of w.values())clearTimeout(E);w.clear()}},[]),Te.useEffect(()=>{for(let w of an(t,i)){let E=i.overrides[w];d({type:"CLEAR",id:w,...E?{clientMutationId:E.clientMutationId}:{}})}},[t,i]);let p=Te.useCallback((w,E)=>{let v=o(),k=s.current.find(y=>y.id===E.id),_=u.current,Q=y=>{let b=_.get(y);b!==void 0&&(clearTimeout(b),_.delete(y))},R=()=>{_.set(`fl:${v}`,setTimeout(()=>{_.delete(`fl:${v}`),l.current&&d({type:"CLEAR",id:E.id,clientMutationId:v})},r))};d({type:"SUBMIT",id:E.id,clientMutationId:v,start:E.start,end:E.end,...k?.revision!==void 0?{baseRevision:k.revision}:{},..."resourceId"in E&&E.resourceId!==void 0?{resourceId:E.resourceId}:{}}),_.set(`to:${v}`,setTimeout(()=>{_.delete(`to:${v}`),l.current&&(d({type:"TIMEOUT",id:E.id,clientMutationId:v}),R())},a));let O=()=>{Q(`to:${v}`),l.current&&(d({type:"REJECT",id:E.id,clientMutationId:v}),R())},P={kind:w,clientMutationId:v,payload:{...E,client_mutation_id:v}},N;try{N=n(P)}catch(y){N=Promise.reject(y instanceof Error?y:new Error(String(y)))}N.then(y=>{if(y.id!==E.id){O();return}Q(`to:${v}`),l.current&&d({type:"RESOLVE",id:y.id,clientMutationId:v,start:y.start,end:y.end,revision:y.revision,...y.resourceId!==void 0?{resourceId:y.resourceId}:{}})}).catch(O)},[n,a,r,o]),D=Te.useMemo(()=>nn(t,i),[t,i]);return{events:D.events,pendingIds:D.pendingIds,rolledBackIds:D.rolledBackIds,submit:p}}import{jsx as rr}from"react/jsx-runtime";function ar({events:e,mutate:t,timeoutMs:n,rollbackFlashMs:a,generateId:r,...o}){let{events:i,pendingIds:d,rolledBackIds:s,submit:l}=wn({events:e,mutate:t,...n!==void 0?{timeoutMs:n}:{},...a!==void 0?{rollbackFlashMs:a}:{},...r?{generateId:r}:{}});return rr(Ot,{...o,events:i,pendingIds:d,rolledBackIds:s,onEventDrop:u=>l("drop",u),onEventResize:u=>l("resize",u)})}export{Ot as AetherCalendar,jn as CALENDAR_CSS,sn as CalendarNav,ln as DEFAULT_LOCALE_MESSAGES,ar as OptimisticCalendar,St as PRESETS,Kn as PRESET_NAMES,da as TIMELINE_CSS,ea as TIME_GRID_CSS,vn as TimeGridView,Dn as TimelineView,Za as default,cn as defaultBaseTokenCss,un as defaultTimeGridTokenCss,gn as defaultTimelineTokenCss,je as ensureCalendarStyles,pn as ensureTimeGridStyles,bn as ensureTimelineStyles,ut as getVisibleRange,Wn as isThemePreset,L as parseLocalDateTime,Je as resolveMessages,mn as resolveThemeVars,xt as stepAnchor,wn as useOptimisticEvents};
