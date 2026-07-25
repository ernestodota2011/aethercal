function De(e){return String(e).padStart(2,"0")}function O(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,n,a,r,i,o,l]=t,s=Number(n),d=Number(a),u=Number(r),g=Number(i??"0"),v=Number(o??"0"),y=Number(l??"0");if(d<1||d>12||u<1||u>31||g>23||v>59||y>59)throw new Error(`out-of-range ISO datetime: ${e}`);let D=new Date(s,d-1,u,g,v,y);if(D.getFullYear()!==s||D.getMonth()!==d-1||D.getDate()!==u)throw new Error(`nonexistent calendar date: ${e}`);return D}function ae(e){return`${e.getFullYear()}-${De(e.getMonth()+1)}-${De(e.getDate())}T${De(e.getHours())}:${De(e.getMinutes())}:${De(e.getSeconds())}`}function ue(e){let t=O(e);return`${t.getFullYear()}-${De(t.getMonth()+1)}-${De(t.getDate())}`}function Tn(e){return`${e.getFullYear()}-${De(e.getMonth()+1)}-${De(e.getDate())}`}function Ve(e){let t=O(e.start),n=O(e.end),a=new Date(t.getFullYear(),t.getMonth(),t.getDate()),r=a;if(n.getTime()>t.getTime()){let i=new Date(n.getTime()-1),o=new Date(i.getFullYear(),i.getMonth(),i.getDate());o.getTime()>a.getTime()&&(r=o)}return{startKey:Tn(a),lastKey:Tn(r)}}function va(e,t){return(e.getDay()-t+7)%7}function Ke(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate());return n.setDate(n.getDate()-va(n,t)),n}function Ht(e,t){return Array.from({length:t},(n,a)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+a);return`${r.getFullYear()}-${De(r.getMonth()+1)}-${De(r.getDate())}`})}function _t(e,t=1){return Ht(Ke(e,t),7)}function $t(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),1);return Ht(Ke(n,t),42)}function Ut(e,t){return Ht(new Date(e.getFullYear(),e.getMonth(),e.getDate()),t)}function be(e,t){let n=O(`${ue(e)}T00:00:00`),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()+t);return`${a.getFullYear()}-${De(a.getMonth()+1)}-${De(a.getDate())}`}function Bt(e,t){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()),a=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((a.getTime()-n.getTime())/864e5)}function rt(e,t){let n=O(e.start),a=O(e.end),r=O(t),i=Bt(n,r),o=new Date(n.getFullYear(),n.getMonth(),n.getDate()+i,n.getHours(),n.getMinutes(),n.getSeconds()),l=new Date(a.getFullYear(),a.getMonth(),a.getDate()+i,a.getHours(),a.getMinutes(),a.getSeconds()),s={id:e.id,start:ae(o),end:ae(l)};return e.revision!==void 0&&(s.revision=e.revision),s}var ya=370;function xn(e){return String(e).padStart(2,"0")}function Rn(e){return`${e.getFullYear()}-${xn(e.getMonth()+1)}-${xn(e.getDate())}`}function ha(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function ba(e){let{startKey:t,lastKey:n}=Ve(e),a=[],r=O(t);for(let i=0;i<ya&&Rn(r)<=n;i+=1)a.push(Rn(r)),r=ha(r,1);return{keys:a,startKey:t,lastKey:n}}function Vt(e){let t=new Map;return e.forEach((n,a)=>{let{keys:r,startKey:i,lastKey:o}=ba(n),l=O(n.start).getTime(),s=O(n.end).getTime();for(let d of r){let u={entry:{event:n,isContinuation:d!==i,continuesAfter:d!==o},startMs:l,endMs:s,index:a},g=t.get(d);g?g.push(u):t.set(d,[u])}}),[...t.keys()].sort().map(n=>{let a=t.get(n);return a.sort((r,i)=>r.startMs-i.startMs||r.endMs-i.endMs||r.index-i.index),{date:n,entries:a.map(r=>r.entry)}})}function Ye(e,t,n,a){let r=n*a;if(r<=0)return e;let i=Math.min(Math.max(e,0),r-1),o=i-i%a,l=Math.min(o+a-1,r-1);switch(t){case"ArrowLeft":return i>o?i-1:i;case"ArrowRight":return i<l?i+1:i;case"ArrowUp":{let s=i-a;return s>=0?s:i}case"ArrowDown":{let s=i+a;return s<r?s:i}case"Home":return o;case"End":return l;default:return i}}var We=60,Ce=15;function Yt(e,t,n){return Math.min(n,Math.max(t,e))}function bt(e,t){let n=O(`${e}T00:00:00`);return new Date(n.getFullYear(),n.getMonth(),n.getDate(),0,t,0)}function Wt(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate(),e.getHours(),e.getMinutes()+t,e.getSeconds())}function wt(e,t){return t==null||(e.resourceId=t),e}function Xe(e,t,n=Ce){let a=t.dayStartHour*We,r=t.dayEndHour*We,i=a+Yt(e,0,1)*t.windowMinutes,o=n>0?n:Ce,l=a+Math.round((i-a)/o)*o;return Yt(l,a,r)}function Dt(e,t){return Yt(e,t.dayStartHour*We,t.dayEndHour*We)}var Kt=24*We;function Xt(e,t,n,a){let r=t+n,i=e;for(;r<0;)r+=Kt,i=be(i,-1);for(;r>Kt;)r-=Kt,i=be(i,1);return{dateOnly:i,minuteOfDay:Dt(r,a)}}function Ge(e,t,n,a){if(n===null)return wt(rt(e,t),a);let r=O(e.start),i=O(e.end),o=bt(t,n),l=Bt(r,i),s=r.getHours()*We+r.getMinutes(),u=i.getHours()*We+i.getMinutes()-s,g=new Date(o.getFullYear(),o.getMonth(),o.getDate()+l,o.getHours(),o.getMinutes()+u,0),v={id:e.id,start:ae(o),end:ae(g)};return e.revision!==void 0&&(v.revision=e.revision),wt(v,a)}function Se(e,t,n,a,r={}){let i=r.minDurationMinutes??Ce,o=O(e.start),l=O(e.end),s=bt(n,a),d=o,u=l;if(t==="end"){let v=Wt(o,i);u=s.getTime()>=v.getTime()?s:v}else{let v=Wt(l,-i);d=s.getTime()<=v.getTime()?s:v}let g={id:e.id,start:ae(d),end:ae(u)};return e.revision!==void 0&&(g.revision=e.revision),g}function Pe(e,t,n={}){let a=n.minDurationMinutes??Ce;if(e.minuteOfDay===null||t.minuteOfDay===null){let[u,g]=e.dateOnly<=t.dateOnly?[e.dateOnly,t.dateOnly]:[t.dateOnly,e.dateOnly],v=O(`${u}T00:00:00`),y=O(`${g}T00:00:00`),D=new Date(y.getFullYear(),y.getMonth(),y.getDate()+1),b={start:ae(v),end:ae(D),allDay:!0};return wt(b,e.resourceId)}let i=bt(e.dateOnly,e.minuteOfDay??0),o=bt(t.dateOnly,t.minuteOfDay??0),l=i.getTime()<=o.getTime()?i:o,s=i.getTime()<=o.getTime()?o:i;s.getTime()===l.getTime()&&(s=Wt(l,a));let d={start:ae(l),end:ae(s),allDay:!1};return wt(d,e.resourceId)}var Oe=60,wa=24*Oe,Da=864e5;function Et(e,t,n){return Math.min(n,Math.max(t,e))}function ut(e={}){let t=e.dayStartHour,n=e.dayEndHour,a=Number.isFinite(t)&&t!==void 0?Et(Math.trunc(t),0,23):0,r=Number.isFinite(n)&&n!==void 0?Et(Math.trunc(n),1,24):24,[i,o]=r>a?[a,r]:[0,24];return{dayStartHour:i,dayEndHour:o,windowMinutes:(o-i)*Oe}}function Cn(e){let t=[],n=[];for(let a of e)a.allDay===!0?t.push(a):n.push(a);return{allDay:t,timed:n}}function Je(e,t){let n=O(e),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()),r=Math.round((a.getTime()-t.getTime())/Da),i=n.getHours()*Oe+n.getMinutes()+n.getSeconds()/60;return r*wa+i}function Tt(e,t){let n=e.map(s=>{let[d,u]=t(s);return{item:s,start:d,end:u}});n.sort((s,d)=>s.start!==d.start?s.start-d.start:d.end-s.end);let a=[],r=[],i=[],o=Number.NEGATIVE_INFINITY,l=()=>{let s=r.length;for(let d of i)a[d].laneCount=s;r=[],i=[],o=Number.NEGATIVE_INFINITY};for(let s of n){i.length>0&&s.start>=o&&l();let d=r.findIndex(u=>!(u.start<s.end&&s.start<u.end));d===-1?(d=r.length,r.push({start:s.start,end:s.end})):r[d]={start:s.start,end:s.end},i.push(a.length),a.push({item:s.item,lane:d,laneCount:1}),o=Math.max(o,s.end)}return l(),a}function Mn(e){return Tt(e,t=>[O(t.start).getTime(),O(t.end).getTime()])}function pt(e,t,n){let a=O(`${t}T00:00:00`),r=n.dayStartHour*Oe,i=n.dayEndHour*Oe,o=e.filter(l=>{let s=Je(l.start,a);return!(Je(l.end,a)<=r||s>=i)});return Mn(o).map(({item:l,lane:s,laneCount:d})=>{let u=Je(l.start,a),g=Je(l.end,a),v=Et(u,r,i),y=Et(g,v,i),{startKey:D,lastKey:b}=Ve(l);return{event:l,lane:s,laneCount:d,topFraction:(v-r)/n.windowMinutes,heightFraction:(y-v)/n.windowMinutes,isContinuation:t!==D,continuesAfter:t!==b}})}function Ea(e){let t=[];for(let n=e.dayStartHour;n<e.dayEndHour;n+=1)t.push({hour:n,topFraction:(n-e.dayStartHour)*Oe/e.windowMinutes});return t}function Jt(e,t,n={}){let a="windowMinutes"in n?n:ut(n),{allDay:r,timed:i}=Cn(t),o=i.map(s=>({event:s,startTs:O(s.start).getTime(),endTs:O(s.end).getTime()}));return{columns:e.map(s=>{let d=O(`${s}T00:00:00`),u=d.getTime(),g=new Date(d.getFullYear(),d.getMonth(),d.getDate()+1).getTime(),v=o.filter(D=>D.startTs>=g?!1:D.endTs>u?!0:D.startTs===D.endTs&&D.startTs>=u).map(D=>D.event),y=r.filter(D=>{let{startKey:b,lastKey:M}=Ve(D);return b<=s&&s<=M});return{dateOnly:s,allDay:y,timed:pt(v,s,a)}}),hourMarks:Ea(a),config:a}}function jt(e,t={}){let n="windowMinutes"in t?t:ut(t),a=e.getHours()*Oe+e.getMinutes()+e.getSeconds()/60,r=n.dayStartHour*Oe,i=n.dayEndHour*Oe;return a<r||a>=i?null:(a-r)/n.windowMinutes}var Fe=60,xt=7,In=1,kn=31;function gt(e,t,n){return Math.min(n,Math.max(t,e))}function ze(e){return e===void 0||!Number.isFinite(e)?xt:gt(Math.trunc(e),In,kn)}function Rt(e){return"windowMinutes"in e?e:ut(e)}function Sn(e){if(e.allDay!==!0)return{start:e.start,end:e.end};let{startKey:t,lastKey:n}=Ve(e);return{start:`${t}T00:00:00`,end:`${be(n,1)}T00:00:00`}}function Ta(e,t,n){let a=n.dayStartHour*Fe,r=n.dayEndHour*Fe,i=[];return t.forEach((o,l)=>{let s=O(`${o}T00:00:00`),d=Je(e.start,s),u=Je(e.end,s);if(u<=a||d>=r)return;let g=gt(d,a,r),v=gt(u,g,r),y=l*n.windowMinutes;i.push({startMin:y+(g-a),endMin:y+(v-a),clippedStart:d<a,clippedEnd:u>r})}),i}function xa(e){let t=[];for(let n of e){let a=t[t.length-1];a&&a.endMin===n.startMin?(a.endMin=n.endMin,a.clippedEnd=n.clippedEnd):t.push({...n})}return t}function An(e,t,n){let a=t.length*n.windowMinutes;if(a<=0)return[];let r=[];for(let o of e){let l=xa(Ta(o,t,n));l.length>0&&r.push({item:o,runs:l})}return Tt(r,o=>[o.runs[0].startMin,o.runs[o.runs.length-1].endMin]).flatMap(({item:o,lane:l,laneCount:s})=>o.runs.map(d=>({event:o.item.event,lane:l,laneCount:s,leftFraction:d.startMin/a,widthFraction:(d.endMin-d.startMin)/a,allDay:o.item.event.allDay===!0,continuesBefore:d.clippedStart,continuesAfter:d.clippedEnd})))}function qt(e,t,n={}){return An([{event:e,...Sn(e)}],t,Rt(n))}function Zt(e,t,n,a={}){let r=Rt(a),i=new Set(a.collapsedGroupIds??[]),o=[],l=new Set;for(let R of e)l.has(R.id)||(l.add(R.id),o.push(R));let s=[],d=new Map;for(let R of o){let L=R.groupId?R.groupId:void 0;if(L===void 0){s.push({kind:"solo",resource:R});continue}let P=d.get(L);P?P.push(R):(d.set(L,[R]),s.push({kind:"group",id:L}))}let u=new Map,g=[];for(let R of t){let L={event:R,...Sn(R)},P=R.resourceId;if(P!==void 0&&l.has(P)){let G=u.get(P);G?G.push(L):u.set(P,[L])}else g.push(L)}let v=(R,L,P)=>{let G=An(P,n,r);return{resource:R,groupId:L,blocks:G,laneCount:G.reduce((w,E)=>Math.max(w,E.laneCount),1)}},y=[];for(let R of s){if(R.kind==="solo"){y.push({kind:"row",row:v(R.resource,null,u.get(R.resource.id)??[])});continue}let L=d.get(R.id)??[],P=i.has(R.id);if(y.push({kind:"group",group:{id:R.id,collapsed:P,resourceCount:L.length}}),!P)for(let G of L)y.push({kind:"row",row:v(G,R.id,u.get(G.id)??[])})}let D=v(null,null,g);D.blocks.length>0&&y.push({kind:"row",row:D});let b=n.length,M=n.map((R,L)=>({dateOnly:R,leftFraction:b>0?L/b:0,widthFraction:b>0?1/b:0})),N=b*r.windowMinutes,ee=[];return N>0&&n.forEach((R,L)=>{let P=L*r.windowMinutes;for(let G=r.dayStartHour;G<r.dayEndHour;G+=1){let w=(G-r.dayStartHour)*Fe;ee.push({dateOnly:R,hour:G,leftFraction:(P+w)/N,isDayStart:G===r.dayStartHour})}}),{days:[...n],items:y,dayHeaders:M,ticks:ee,config:r}}function He(e,t,n={},a=Ce){let r=Rt(n);if(t.length===0||r.windowMinutes<=0)return null;let i=t.length*r.windowMinutes,o=gt(e,0,1)*i,l=Math.min(Math.floor(o/r.windowMinutes),t.length-1),s=o-l*r.windowMinutes,d=r.dayStartHour*Fe,u=r.dayEndHour*Fe,g=a>0?a:Ce,v=d+Math.round(s/g)*g;return{dateOnly:t[l],minuteOfDay:gt(v,d,u)}}function Qt(e,t,n={}){let a=Rt(n),r=t.indexOf(ue(ae(e)));if(r===-1)return null;let i=e.getHours()*Fe+e.getMinutes()+e.getSeconds()/60,o=a.dayStartHour*Fe,l=a.dayEndHour*Fe;if(i<o||i>=l)return null;let s=t.length*a.windowMinutes;return s<=0?null:(r*a.windowMinutes+(i-o))/s}var Ra=1;function je(e,t,n=Ra,a){let r=t.getFullYear(),i=t.getMonth(),o=t.getDate(),l,s;switch(e){case"week":{l=Ke(t,n),s=new Date(l.getFullYear(),l.getMonth(),l.getDate()+7);break}case"day":{l=new Date(r,i,o),s=new Date(r,i,o+1);break}case"timeline":{l=new Date(r,i,o),s=new Date(r,i,o+ze(a));break}default:{l=new Date(r,i,1),s=new Date(r,i+1,1);break}}return{view:e,from:ae(l),to:ae(s)}}function it(e,t,n,a){let r=e.getFullYear(),i=e.getMonth(),o=e.getDate();switch(t){case"week":return new Date(r,i,o+7*n);case"day":return new Date(r,i,o+n);case"timeline":return new Date(r,i,o+ze(a)*n);default:return new Date(r,i+n,1)}}var Ct={status:"idle"};function Mt(e){return e.status==="dragging"}function en(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return Ct}}var ot={status:"idle"};function mt(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"RESIZE_START":return{status:"resizing",eventId:t.eventId,edge:t.edge};case"SELECT_START":return{status:"selecting",anchor:t.point,current:t.point};case"SELECT_MOVE":return e.status!=="selecting"?e:{status:"selecting",anchor:e.anchor,current:t.point};case"COMMIT":case"CANCEL":return ot}}var tn={overrides:{},appliedRevision:{}};function Ca(e,t){let n={...e};return delete n[t],n}function nn(e,t){switch(t.type){case"SUBMIT":{let n=t.baseRevision??Number.NEGATIVE_INFINITY,a=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;return{overrides:{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"pending",start:t.start,end:t.end,...t.baseRevision!==void 0?{revision:t.baseRevision}:{},...t.resourceId!==void 0?{resourceId:t.resourceId}:{}}},appliedRevision:{...e.appliedRevision,[t.id]:Math.max(a,n)}}}case"RESOLVE":{let n=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;if(t.revision<=n)return e;let a=e.overrides[t.id],r=a!==void 0&&a.clientMutationId===t.clientMutationId&&a.status==="pending",i=t.resourceId??a?.resourceId;return{overrides:r?{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"committed",start:t.start,end:t.end,revision:t.revision,...i!==void 0?{resourceId:i}:{}}}:e.overrides,appliedRevision:{...e.appliedRevision,[t.id]:t.revision}}}case"REJECT":case"TIMEOUT":{let n=e.overrides[t.id];return!n||n.clientMutationId!==t.clientMutationId||n.status!=="pending"?e:{...e,overrides:{...e.overrides,[t.id]:{...n,status:"rolledback"}}}}case"CLEAR":{let n=e.overrides[t.id];return!n||t.clientMutationId&&n.clientMutationId!==t.clientMutationId?e:{...e,overrides:Ca(e.overrides,t.id)}}}}function an(e,t){let n=new Set,a=new Set,r=o=>o.resourceId!==void 0?{resourceId:o.resourceId}:void 0;return{events:e.map(o=>{let l=t.overrides[o.id];return l?l.status==="pending"?(n.add(o.id),{...o,start:l.start,end:l.end,...r(l)}):l.status==="rolledback"?(a.add(o.id),o):o.revision!==void 0&&l.revision!==void 0&&o.revision>=l.revision?o:{...o,start:l.start,end:l.end,...l.revision!==void 0?{revision:l.revision}:{},...r(l)}:o}),pendingIds:n,rolledBackIds:a}}function rn(e,t){let n=new Map(e.map(r=>[r.id,r])),a=[];for(let[r,i]of Object.entries(t.overrides)){if(i.status!=="committed")continue;let o=n.get(r);o&&o.revision!==void 0&&i.revision!==void 0&&o.revision>=i.revision&&a.push(r)}return a}import*as xe from"react";import*as It from"react";var on=new Date(2023,0,1);function On(e,t){let n=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(a,r)=>{let i=(t+r)%7,o=new Date(on.getFullYear(),on.getMonth(),on.getDate()+i);return n.format(o)})}function sn(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function Pn(e,t,n){let a=new Intl.DateTimeFormat(n,{month:"short",day:"numeric"}).format(e),r=new Intl.DateTimeFormat(n,{month:"short",day:"numeric",year:"numeric"}).format(t);return`${a} \u2013 ${r}`}function Ln(e,t,n,a,r=xt){if(e==="day")return new Intl.DateTimeFormat(n,{dateStyle:"full"}).format(t);if(e==="week"){let i=Ke(t,a),o=new Date(i.getFullYear(),i.getMonth(),i.getDate()+6);return Pn(i,o,n)}if(e==="timeline"){let i=ze(r),o=new Date(t.getFullYear(),t.getMonth(),t.getDate()),l=new Date(o.getFullYear(),o.getMonth(),o.getDate()+i-1);return i===1?new Intl.DateTimeFormat(n,{dateStyle:"full"}).format(o):Pn(o,l,n)}return sn(t,n)}function ft(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(O(e))}function pe(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(O(e))}function Nn(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(O(e))}import{jsx as _e,jsxs as Fn}from"react/jsx-runtime";function Ma(...e){return e.filter(Boolean).join(" ")}function Ia(e,t,n){let{event:a,isContinuation:r,continuesAfter:i}=e;return a.allDay===!0?n.allDay:r?i?n.continues:n.endsAt(pe(a.end,t)):pe(a.start,t)}function ka({entry:e,locale:t,messages:n}){let{event:a,isContinuation:r,continuesAfter:i}=e,o=Ia(e,t,n),l=a.color?{"--ac-event-accent":a.color}:void 0;return Fn("li",{className:Ma("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":a.id,"aria-label":`${o} ${a.title}`,style:l,...a.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...i?{"data-continues-after":""}:{},children:[_e("span",{className:"aethercal-agenda-event-time",children:o}),_e("span",{className:"aethercal-agenda-event-title",children:a.title})]})}function Gn({events:e,locale:t,messages:n,themeVars:a}){let r=It.useMemo(()=>Vt(e),[e]),i=It.useId();return r.length===0?_e("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:_e("p",{className:"aethercal-agenda-empty",children:n.noEvents})}):_e("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:r.map(o=>{let l=`${i}-${o.date}`;return Fn("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":l,"data-date":o.date,children:[_e("div",{className:"aethercal-agenda-day-title",id:l,children:Nn(o.date,t)}),_e("ul",{className:"aethercal-agenda-day-events",role:"list",children:o.entries.map((s,d)=>_e(ka,{entry:s,locale:t,messages:n},`${s.event.id}-${d}`))})]},o.date)})})}import{jsx as $e,jsxs as zn}from"react/jsx-runtime";var Sa=["month","week","day","list","timeline"];function ln({view:e,anchor:t,now:n,locale:a,firstDayOfWeek:r,timelineDays:i,messages:o,showViews:l=!0,onRangeChange:s,onViewChange:d}){let u=y=>{s?.(je(e,y,r,i))},g=y=>it(t,e,y,i),v=Ln(e,t,a,r,i);return zn("div",{className:"aethercal-nav",role:"toolbar","aria-label":o.navToolbar,children:[zn("div",{className:"aethercal-nav-group",children:[$e("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":o.navPrevious,onClick:()=>u(g(-1)),children:$e("span",{"aria-hidden":"true",children:"\u2039"})}),$e("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-today",onClick:()=>u(n),children:o.navToday}),$e("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":o.navNext,onClick:()=>u(g(1)),children:$e("span",{"aria-hidden":"true",children:"\u203A"})})]}),$e("span",{className:"aethercal-nav-title","aria-live":"polite",children:v}),l?$e("div",{className:"aethercal-nav-views",children:Sa.map(y=>$e("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-view","aria-pressed":y===e,onClick:()=>d?.(je(y,t,r,i)),children:o.viewNames[y]},y))}):null]})}var Aa={allDay:"All day",continues:"Continues",endsAt:e=>`ends ${e}`,more:e=>`+${e} more`,noEvents:"No events",unavailable:"This view is not available yet.",keyboardHint:"Use the arrow keys to move between days. Press Enter on an event to grab it, the arrow keys to move or resize it, Enter to drop, and Escape to cancel.",grabbedMoveHint:e=>`Grabbed ${e}. Use the arrow keys to move it, Enter to drop, Escape to cancel.`,grabbedResizeHint:e=>`Resizing ${e}. Use the up and down arrow keys to change its duration, Enter to confirm, Escape to cancel.`,movedTo:e=>`Moved to ${e}`,resizedTo:e=>`Duration ${e}`,dropped:e=>`Dropped on ${e}`,resized:e=>`Duration set to ${e}`,createHere:e=>`Create an event on ${e}`,cancelled:"Cancelled",navToolbar:"Calendar navigation",navPrevious:"Previous",navNext:"Next",navToday:"Today",viewNames:{month:"Month",week:"Week",day:"Day",list:"Agenda",timeline:"Timeline"},timelineResources:"Resources",timelineUnassigned:"Unassigned",timelineEmpty:"No resources to show",timelineGroupCount:e=>e===1?"1 resource":`${e} resources`,groupExpanded:e=>`${e} expanded`,groupCollapsed:e=>`${e} collapsed`,timelineKeyboardHint:"Use the up and down arrow keys to move between resources and the left and right arrow keys to move between days. Press Enter on a group to expand or collapse it, or on an event to grab it; then use the left and right arrow keys to change its time, the up and down arrow keys to move it to another resource, Enter to drop it, and Escape to cancel."},Pa={allDay:"Todo el d\xEDa",continues:"Contin\xFAa",endsAt:e=>`termina ${e}`,more:e=>`+${e} m\xE1s`,noEvents:"Sin eventos",unavailable:"Esta vista a\xFAn no est\xE1 disponible.",keyboardHint:"Usa las flechas para moverte entre los d\xEDas. Pulsa Enter sobre un evento para agarrarlo, las flechas para moverlo o cambiar su duraci\xF3n, Enter para soltarlo y Escape para cancelar.",grabbedMoveHint:e=>`Agarraste el evento ${e}. Usa las flechas para moverlo, Enter para soltarlo y Escape para cancelar.`,grabbedResizeHint:e=>`Est\xE1s cambiando la duraci\xF3n de ${e}. Usa las flechas hacia arriba y abajo para ajustarla, Enter para confirmar y Escape para cancelar.`,movedTo:e=>`Movido a ${e}`,resizedTo:e=>`Duraci\xF3n ${e}`,dropped:e=>`Soltado en ${e}`,resized:e=>`Duraci\xF3n establecida en ${e}`,createHere:e=>`Crear un evento en ${e}`,cancelled:"Cancelado",navToolbar:"Navegaci\xF3n del calendario",navPrevious:"Anterior",navNext:"Siguiente",navToday:"Hoy",viewNames:{month:"Mes",week:"Semana",day:"D\xEDa",list:"Agenda",timeline:"Cronograma"},timelineResources:"Recursos",timelineUnassigned:"Sin asignar",timelineEmpty:"No hay recursos para mostrar",timelineGroupCount:e=>e===1?"1 recurso":`${e} recursos`,groupExpanded:e=>`${e} desplegado`,groupCollapsed:e=>`${e} plegado`,timelineKeyboardHint:"Usa las flechas hacia arriba y abajo para moverte entre los recursos, y las flechas izquierda y derecha para moverte entre los d\xEDas. Pulsa Enter sobre un grupo para desplegarlo o plegarlo, o sobre un evento para agarrarlo; luego usa las flechas izquierda y derecha para cambiar su hora, las flechas hacia arriba y abajo para moverlo a otro recurso, Enter para soltarlo y Escape para cancelar."},dn={en:Aa,es:Pa};function Oa(e){return e.toLowerCase().split("-")[0]??""}function qe(e,t,n=dn){let a=e.toLowerCase(),r=n[a]??n[Oa(e)]??n.en??dn.en;return t?{...r,...t}:r}import*as oe from"react";import{jsx as Hn}from"react/jsx-runtime";function st({message:e}){return Hn("div",{className:"aethercal-sr-only","aria-live":"polite","aria-atomic":"true",children:e})}function lt({id:e,text:t}){return Hn("div",{id:e,className:"aethercal-sr-only",children:t})}import{jsx as _n,jsxs as Na}from"react/jsx-runtime";function La(...e){return e.filter(Boolean).join(" ")}function kt({event:e,timeLabel:t,onDragStart:n,onDragEnd:a,canDrag:r=!0,isPending:i,isRolledBack:o,onClick:l,onContextMenu:s,id:d,interactive:u,isActive:g,isGrabbed:v}){let y=e.editable!==!1,D=y&&r,b=e.color?{"--ac-event-accent":e.color}:void 0,M=t?`${t} ${e.title}`:e.title;return Na("div",{className:La("aethercal-event",!y&&"is-locked",i&&"is-pending",o&&"is-rolledback",g&&"is-active",v&&"is-grabbed"),...d?{id:d}:{},...u?{role:"button"}:{},draggable:D,"data-event-id":e.id,"aria-label":M,title:e.title,style:b,onDragStart:N=>{if(!D){N.preventDefault();return}N.dataTransfer.setData("text/plain",e.id),N.dataTransfer.effectAllowed="move",n(e.id)},onDragEnd:a,onClick:l,onContextMenu:s?N=>{N.preventDefault(),N.stopPropagation(),s()}:void 0,children:[t?_n("time",{className:"aethercal-event-time",children:t}):null,t?" ":null,_n("span",{className:"aethercal-event-title",children:e.title})]})}import{Fragment as Ha,jsx as Ae,jsxs as St}from"react/jsx-runtime";var $n=new Set,dt=7,Un=6;function Bn(...e){return e.filter(Boolean).join(" ")}function Ga(e){let t=[];for(let n=0;n<e.length;n+=dt)t.push(e.slice(n,n+dt));return t}function Fa(e){let t=new Map;for(let n of e){let a=ue(n.start),r=t.get(a);r?r.push(n):t.set(a,[n])}return t}function za(e){return{start:`${e}T00:00:00`,end:`${be(e,1)}T00:00:00`,allDay:!0}}function Vn(e){let{events:t,anchor:n,locale:a,firstDayOfWeek:r,messages:i,weekdayLabels:o,maxEventsPerDay:l,themeVars:s,onEventDrop:d,onRangeSelect:u,onEventClick:g,onContextMenu:v,pendingIds:y=$n,rolledBackIds:D=$n}=e,b=oe.useMemo(()=>$t(n,r),[n,r]),M=oe.useMemo(()=>Ga(b),[b]),N=oe.useMemo(()=>o??On(a,r),[o,a,r]),ee=oe.useMemo(()=>Fa(t),[t]),R=n.getMonth(),L=ue(ae(new Date)),P=oe.useMemo(()=>ue(ae(n)),[n]),[G,w]=oe.useReducer(en,Ct),[E,F]=oe.useState(()=>new Set),$=oe.useId(),[S,Y]=oe.useState(P),[C,q]=oe.useState(null),[U,re]=oe.useState(null),[W,z]=oe.useState("");oe.useEffect(()=>{b.includes(S)||(Y(P),q(null),re(null))},[b,S,P]);let p=oe.useCallback(V=>!!g||V.editable!==!1&&!!d,[g,d]);oe.useEffect(()=>{let V=new Set((ee.get(S)??[]).filter(m=>p(m)).map(m=>m.id));U&&!V.has(U.eventId)?(re(null),q(null)):!U&&C!==null&&!V.has(C)&&q(null)},[ee,S,C,U,p]);let h=V=>`${$}-c-${V}`,Z=(V,m)=>`${$}-e-${V}-${m}`,Q=`${$}-hint`,T=U?Z(S,U.eventId):C?Z(S,C):h(S),H=oe.useCallback(V=>{F(m=>{let J=new Set(m);return J.add(V),J})},[]),x=oe.useCallback(V=>m=>{if(m.preventDefault(),!Mt(G)){w({type:"DROP"});return}let J=G.eventId,se=m.dataTransfer.getData("text/plain");if(w({type:"DROP"}),se&&se!==J||!d)return;let te=t.find(ie=>ie.id===J);!te||te.editable===!1||d(rt(te,V))},[G,t,d]),B=!!d,X=oe.useCallback(V=>{if(!U)return;let m=be(U.targetDate,V),J=b[0],se=b[b.length-1];m<J||m>se||(z(i.movedTo(ft(m,a))),re({...U,targetDate:m,moved:!0}))},[U,b,a,i]),de=oe.useCallback(()=>{if(!U)return;if(!U.moved){q(U.eventId),re(null);return}let V=t.find(m=>m.id===U.eventId);V&&V.editable!==!1&&d&&(d(rt(V,U.targetDate)),z(i.dropped(ft(U.targetDate,a)))),Y(U.targetDate),q(null),re(null)},[U,t,d,i,a]),ke={ArrowLeft:-1,ArrowRight:1,ArrowUp:-dt,ArrowDown:dt},me=oe.useCallback(V=>{let{key:m}=V,J=m==="Enter"||m===" "||m==="Spacebar";if(U){if(m in ke){V.preventDefault(),X(ke[m]);return}if(J){V.preventDefault(),de();return}if(m==="Escape"){V.preventDefault(),re(null),z(i.cancelled);return}return}let se=ee.get(S)??[],te=se.filter(ie=>p(ie));if(C){let ie=te.findIndex(ne=>ne.id===C);if(m==="ArrowDown"){V.preventDefault(),ie>=0&&ie<te.length-1&&q(te[ie+1].id);return}if(m==="ArrowUp"){V.preventDefault(),ie>0?q(te[ie-1].id):q(null);return}if(J){V.preventDefault();let ne=te.find(tt=>tt.id===C);if(!ne)return;ne.editable!==!1&&d?(re({eventId:ne.id,targetDate:S,moved:!1}),z(i.grabbedMoveHint(ne.title))):g&&g({id:ne.id});return}if(m==="Escape"){V.preventDefault(),q(null);return}if(m==="ArrowLeft"||m==="ArrowRight"||m==="Home"||m==="End"){V.preventDefault(),q(null);let ne=Ye(b.indexOf(S),m,Un,dt);Y(b[ne]);return}return}if(m in ke||m==="Home"||m==="End"){V.preventDefault();let ie=Ye(b.indexOf(S),m,Un,dt);Y(b[ie]);return}J&&(te.length>0?(V.preventDefault(),H(S),q(te[0].id)):se.length===0&&u&&(V.preventDefault(),u(za(S)),z(i.createHere(ft(S,a)))))},[U,C,S,b,ee,p,d,g,u,X,de,H,i,a,ke]);return St(Ha,{children:[St("div",{className:Bn("aethercal-calendar",Mt(G)&&"is-dragging"),role:"grid","aria-label":sn(n,a),"aria-describedby":Q,"aria-activedescendant":T,tabIndex:0,"data-view":"month",style:s,onKeyDown:me,children:[Ae("div",{className:"aethercal-weekdays",role:"row",children:N.map((V,m)=>Ae("div",{role:"columnheader",className:"aethercal-weekday",children:V},m))}),M.map((V,m)=>Ae("div",{className:"aethercal-week",role:"row",children:V.map(J=>{let se=ee.get(J)??[],te=E.has(J),ie=te?se:se.slice(0,l),ne=se.length-ie.length,tt=new Date(`${J}T00:00:00`).getMonth()!==R,yt=J===L,ht=!C&&!U&&J===S,Gt=U?.targetDate===J;return St("div",{id:h(J),role:"gridcell",className:Bn("aethercal-day",tt&&"is-outside",yt&&"is-today",ht&&"is-active",Gt&&"is-drop-target"),"data-date":J,onDragOver:B?ce=>ce.preventDefault():void 0,onDrop:B?x(J):void 0,onContextMenu:v?ce=>{ce.target.closest("[data-event-id], button")||(ce.preventDefault(),v({start:`${J}T00:00:00`}))}:void 0,children:[Ae("span",{className:"aethercal-sr-only",children:ft(J,a)}),Ae("div",{className:"aethercal-day-head",children:Ae("span",{className:"aethercal-day-number","aria-hidden":"true",children:Number(J.slice(-2))})}),St("div",{className:"aethercal-day-events",children:[ie.map(ce=>{let Ft=U?.eventId===ce.id||!U&&C===ce.id;return Ae(kt,{id:Z(J,ce.id),event:ce,interactive:p(ce),isActive:Ft,isGrabbed:U?.eventId===ce.id,timeLabel:ce.allDay?null:pe(ce.start,a),canDrag:B,onDragStart:c=>w({type:"DRAG_START",eventId:c}),onDragEnd:()=>w({type:"DRAG_CANCEL"}),isPending:y.has(ce.id),isRolledBack:D.has(ce.id),...g?{onClick:()=>g({id:ce.id})}:{},...v?{onContextMenu:()=>v({id:ce.id})}:{}},ce.id)}),ne>0&&!te?Ae("button",{type:"button",className:"aethercal-more",onClick:()=>H(J),children:i.more(ne)}):null]})]},J)})},m))]}),Ae(lt,{id:Q,text:i.keyboardHint}),Ae(st,{message:W})]})}var Kn={light:{"--ac-fg":"#1f2328","--ac-muted":"#5f6672","--ac-faint":"#676e79","--ac-bg":"#ffffff","--ac-header-fg":"#4b5563","--ac-border":"#e5e7eb","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#fafafa","--ac-today-marker-bg":"#111827","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#eef1f4","--ac-event-fg":"#1f2328","--ac-event-accent":"#64748b","--ac-more-fg":"#4b5563","--ac-focus":"#2563eb","--ac-rollback":"#b91c1c","--ac-tg-now":"#dc2626"},dark:{"--ac-fg":"#e6e8eb","--ac-muted":"#9aa1ab","--ac-faint":"#868e99","--ac-bg":"#14161a","--ac-header-fg":"#b3b9c2","--ac-border":"#2a2e35","--ac-cell-bg":"#171a1f","--ac-cell-bg-outside":"#111318","--ac-today-marker-bg":"#e6e8eb","--ac-today-marker-fg":"#14161a","--ac-event-bg":"#242a32","--ac-event-fg":"#e6e8eb","--ac-event-accent":"#8b98a9","--ac-more-fg":"#b3b9c2","--ac-focus":"#6ea8fe","--ac-rollback":"#f87171","--ac-tg-now":"#f87171"},midnight:{"--ac-fg":"#dfe4ea","--ac-muted":"#8b95a1","--ac-faint":"#828a95","--ac-bg":"#0b0f14","--ac-header-fg":"#a7b0bd","--ac-border":"#1c232c","--ac-cell-bg":"#0e131a","--ac-cell-bg-outside":"#090d12","--ac-today-marker-bg":"#dfe4ea","--ac-today-marker-fg":"#0b0f14","--ac-event-bg":"#17212c","--ac-event-fg":"#dfe4ea","--ac-event-accent":"#7f8ea3","--ac-more-fg":"#a7b0bd","--ac-focus":"#74a9ff","--ac-rollback":"#fb7185","--ac-tg-now":"#fb7185"},high_contrast:{"--ac-fg":"#000000","--ac-muted":"#000000","--ac-faint":"#1a1a1a","--ac-bg":"#ffffff","--ac-header-fg":"#000000","--ac-border":"#000000","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#ffffff","--ac-today-marker-bg":"#000000","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#e0e0e0","--ac-event-fg":"#000000","--ac-event-accent":"#000000","--ac-more-fg":"#000000","--ac-focus":"#0033cc","--ac-rollback":"#b00000","--ac-tg-now":"#d00000"}};var At=Kn,Yn=["light","dark","midnight","high_contrast"],$a=new Set(Yn),Ua={"--ac-font":'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',"--ac-radius":"8px","--ac-cell-min-height":"96px"},Ba={"--ac-tg-gutter":"56px","--ac-tg-body-height":"640px","--ac-tg-hour-min-height":"44px","--ac-tg-line":"var(--ac-border)","--ac-tg-event-bg":"var(--ac-event-bg)","--ac-tg-event-fg":"var(--ac-event-fg)","--ac-tg-event-accent":"var(--ac-event-accent)"},Va={"--ac-tl-rowhead-width":"168px","--ac-tl-lane-height":"30px","--ac-tl-body-height":"560px","--ac-tl-line":"var(--ac-border)","--ac-tl-event-bg":"var(--ac-event-bg)","--ac-tl-event-fg":"var(--ac-event-fg)","--ac-tl-event-accent":"var(--ac-event-accent)","--ac-tl-group-bg":"var(--ac-cell-bg-outside)","--ac-tl-now":"var(--ac-tg-now)"},Wn=["--ac-tg-now"],Ka=/[;{}<>]/;function Xn(e){return typeof e=="string"&&$a.has(e)}function cn(e){return Object.entries(e).map(([t,n])=>`  ${t}: ${n};`).join(`
`)}function Ya(){let e={};for(let[t,n]of Object.entries(At.light))Wn.includes(t)||(e[t]=n);return e}function Jn(){let e={};for(let t of Wn){let n=At.light[t];n!==void 0&&(e[t]=n)}return e}function un(){return cn({...Ua,...Ya()})}function pn(){return cn({...Ba,...Jn()})}function gn(){return cn({...Va,...Jn()})}function Wa(e){let t={};for(let[n,a]of Object.entries(e))n.startsWith("--ac-")&&(typeof a!="string"||a.trim()===""||Ka.test(a)||(t[n]=a));return t}function mn(e){return e===void 0?{}:typeof e=="string"?Xn(e)?{...At[e]}:{}:Wa(e)}var jn="aethercal-calendar-styles",qn=`
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
   the calendar with. */
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
    min-height: 44px;
    display: flex;
    align-items: center;
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
`;function Ze(){if(typeof document>"u"||document.getElementById(jn))return;let e=document.createElement("style");e.id=jn,e.textContent=qn,document.head.appendChild(e)}import*as j from"react";function Ee(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(O(e))}function Zn(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function Qn(e,t){if(e.length===0)return"";let n=O(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(n);let a=O(e[e.length-1]),r=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${r.format(n)} \u2013 ${r.format(a)}`}var ea="aethercal-timegrid-styles",ta=`
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
`;function fn(){if(typeof document>"u"||document.getElementById(ea))return;let e=document.createElement("style");e.id=ea,e.textContent=ta,document.head.appendChild(e)}import{Fragment as ra,jsx as fe,jsxs as Ue}from"react/jsx-runtime";function Pt(...e){return e.filter(Boolean).join(" ")}var Le=e=>`${e*100}%`,na=new Set;function aa(e){let t=O(e);return t.getHours()*60+t.getMinutes()}function vn(e,t,n){let a=O(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}function Xa(e,t,n,a){let{event:r,isContinuation:i,continuesAfter:o}=e;return i?o?n:a(pe(r.end,t)):pe(r.start,t)}function Ot(e,t){let n=t.getBoundingClientRect();return n.height>0?(e-n.top)/n.height:0}function yn(e){let{view:t,days:n,events:a,locale:r,config:i,now:o,themeVars:l,onEventDrop:s,onEventResize:d,onRangeSelect:u,onEventClick:g,onContextMenu:v,pendingIds:y=na,rolledBackIds:D=na}=e,b=j.useMemo(()=>{if(e.messages)return e.messages;let c={...e.allDayLabel!==void 0?{allDay:e.allDayLabel}:{},...e.continuesLabel!==void 0?{continues:e.continuesLabel}:{},...e.formatEndsLabel!==void 0?{endsAt:e.formatEndsLabel}:{}};return qe(r,c)},[e.messages,e.allDayLabel,e.continuesLabel,e.formatEndsLabel,r]);j.useEffect(()=>{Ze(),fn()},[]);let M=j.useMemo(()=>Jt(n,a,i),[n,a,i]),N=j.useMemo(()=>jt(o,i),[o,i]),ee=j.useMemo(()=>ue(ae(o)),[o]),[R,L]=j.useReducer(mt,ot),P=j.useRef(null),[G,w]=j.useState(null),[E,F]=j.useState(null),$=!!s,S=!!d,Y=!!u,C=R.status==="dragging",q=j.useCallback((c,f)=>I=>{if(I.preventDefault(),R.status!=="dragging"){L({type:"COMMIT"});return}let K=R.eventId,_=I.dataTransfer.getData("text/plain");if(L({type:"COMMIT"}),_&&_!==K||!s)return;let A=a.find(we=>we.id===K);if(!A||A.editable===!1)return;let k=null;if(f&&A.allDay!==!0){let he=I.currentTarget.getBoundingClientRect();he.height>0&&Number.isFinite(I.clientY)&&(k=Xe((I.clientY-he.top)/he.height,M.config))}s(Ge(A,c,k))},[R,a,s,M.config]),U=j.useCallback(c=>{P.current?.kind!=="resize"&&L({type:"DRAG_START",eventId:c})},[]),re=j.useCallback(()=>L({type:"CANCEL"}),[]),W=j.useCallback((c,f)=>I=>{if(!d||c.editable===!1||I.button!==0||P.current)return;let K=I.currentTarget.closest(".aethercal-tg-col");K?.dataset.date&&(I.preventDefault(),I.stopPropagation(),P.current={kind:"resize",pointerId:I.pointerId,eventId:c.id,edge:f,dateOnly:K.dataset.date,colEl:K,payload:null},I.currentTarget.setPointerCapture?.(I.pointerId),L({type:"RESIZE_START",eventId:c.id,edge:f}))},[d]),z=j.useCallback(c=>f=>{if(!u||f.button!==0||P.current||f.target.closest("[data-event-id], button"))return;let I=f.currentTarget,K=Xe(Ot(f.clientY,I),M.config);P.current={kind:"select",pointerId:f.pointerId,anchorDate:c,anchorCol:I,anchorMinute:K,currentDate:c,currentCol:I,currentMinute:K},I.setPointerCapture?.(f.pointerId),L({type:"SELECT_START",point:{dateOnly:c,minuteOfDay:K}})},[u,M.config]),p=R.status==="resizing"||R.status==="selecting";j.useLayoutEffect(()=>{if(!p)return;let c=A=>{let k=P.current;if(!(!k||A.pointerId!==k.pointerId))if(k.kind==="resize"){let we=document.elementFromPoint(A.clientX,A.clientY)?.closest(".aethercal-tg-col"),he=we?.dataset.date?we:k.colEl,nt=Xe(Ot(A.clientY,he),M.config),ct=a.find(zt=>zt.id===k.eventId);if(!ct)return;let Ne=Se(ct,k.edge,he.dataset.date??k.dateOnly,nt);k.payload=Ne,w(Ne)}else{let we=document.elementFromPoint(A.clientX,A.clientY)?.closest(".aethercal-tg-col"),he=we?.dataset.date?we:k.currentCol;k.currentCol=he,k.currentDate=he.dataset.date??k.anchorDate,k.currentMinute=Xe(Ot(A.clientY,he),M.config);let nt=Pe({dateOnly:k.anchorDate,minuteOfDay:k.anchorMinute},{dateOnly:k.currentDate,minuteOfDay:k.currentMinute}),Ne=(k.currentDate===k.anchorDate?pt([{id:"__sel",title:"",start:nt.start,end:nt.end}],k.anchorDate,M.config):[])[0];F(Ne?{dateOnly:k.anchorDate,topFraction:Ne.topFraction,heightFraction:Ne.heightFraction}:null)}},f=A=>{let k=P.current;P.current=null,w(null),F(null),A&&k&&(k.kind==="resize"&&k.payload&&d&&d(k.payload),k.kind==="select"&&u&&(k.currentDate!==k.anchorDate||k.currentMinute!==k.anchorMinute)&&u(Pe({dateOnly:k.anchorDate,minuteOfDay:k.anchorMinute},{dateOnly:k.currentDate,minuteOfDay:k.currentMinute}))),L({type:A?"COMMIT":"CANCEL"})},I=A=>{P.current&&A.pointerId!==P.current.pointerId||f(!0)},K=A=>{P.current&&A.pointerId!==P.current.pointerId||f(!1)},_=A=>{A.key==="Escape"&&f(!1)};return window.addEventListener("pointermove",c),window.addEventListener("pointerup",I),window.addEventListener("pointercancel",K),window.addEventListener("keydown",_),()=>{window.removeEventListener("pointermove",c),window.removeEventListener("pointerup",I),window.removeEventListener("pointercancel",K),window.removeEventListener("keydown",_)}},[p,a,M.config,d,u]);let h=j.useCallback((c,f)=>I=>{if(!v||I.target.closest("[data-event-id], button"))return;if(I.preventDefault(),!f){v({start:`${c}T00:00:00`});return}let K=Xe(Ot(I.clientY,I.currentTarget),M.config),_=O(`${c}T00:00:00`),A=new Date(_.getFullYear(),_.getMonth(),_.getDate(),0,K,0);v({start:ae(A)})},[v,M.config]),Z=j.useId(),Q=j.useMemo(()=>M.columns.map(c=>c.dateOnly),[M.columns]),[T,H]=j.useState(()=>(Q.includes(ee)?ee:Q[0])??""),[x,B]=j.useState(null),[X,de]=j.useState(null),[ke,me]=j.useState("");j.useEffect(()=>{Q.includes(T)||(H(Q[0]??""),B(null),de(null))},[Q,T]);let V=c=>`${Z}-col-${c}`,m=(c,f)=>`${Z}-e-${c}-${f}`,J=`${Z}-hint`,se=Ce,te=j.useCallback(c=>!!g||c.editable!==!1&&!!(s||d),[g,s,d]),ie=j.useMemo(()=>{let c=M.columns.find(f=>f.dateOnly===T);return c?[...c.allDay,...c.timed.map(f=>f.event)]:[]},[M.columns,T]),ne=j.useMemo(()=>ie.filter(c=>te(c)),[ie,te]);j.useEffect(()=>{let c=new Set(ne.map(f=>f.id));X&&!c.has(X.eventId)?(de(null),B(null)):!X&&x!==null&&!c.has(x)&&B(null)},[ne,x,X]);let tt=X?m(T,X.eventId):x?m(T,x):V(T),yt=j.useCallback(c=>{let f=X;if(!f)return;let I=f.dateOnly,K=f.minute,_=a.find(k=>k.id===f.eventId),A=_?.allDay===!0;if(!A&&(c==="ArrowUp"||c==="ArrowDown")){let k=Xt(I,K,c==="ArrowUp"?-se:se,M.config);I=k.dateOnly,K=k.minuteOfDay}else c==="ArrowLeft"?I=be(I,-1):c==="ArrowRight"&&(I=be(I,1));if(!(I===f.dateOnly&&K===f.minute)){if(_)if(f.kind==="move")me(b.movedTo(A?Ee(I,r):`${Ee(I,r)} ${vn(I,K,r)}`));else{let k=Se(_,"end",I,K);me(b.resizedTo(`${pe(k.start,r)} \u2013 ${pe(k.end,r)}`))}de({...f,dateOnly:I,minute:K,moved:!0})}},[X,se,M.config,a,b,r]),ht=j.useCallback(()=>{let c=X;if(!c)return;if(!c.moved){B(c.eventId),de(null);return}let f=a.find(I=>I.id===c.eventId);if(f&&f.editable!==!1&&c.kind==="move"&&s){let I=Ge(f,c.dateOnly,f.allDay===!0?null:c.minute);s(I);let K=ue(I.start);H(Q.includes(K)?K:T),B(null),me(b.dropped(f.allDay===!0?Ee(c.dateOnly,r):vn(c.dateOnly,c.minute,r)))}else if(f&&f.editable!==!1&&c.kind==="resize"&&d){let I=Se(f,"end",c.dateOnly,c.minute);d(I),B(c.eventId),me(b.resized(`${pe(I.start,r)} \u2013 ${pe(I.end,r)}`))}else B(c.eventId);de(null)},[X,a,s,d,Q,T,b,r]),Gt=j.useCallback(c=>{let{key:f}=c,I=f==="Enter"||f===" "||f==="Spacebar",K=f==="ArrowUp"||f==="ArrowDown"||f==="ArrowLeft"||f==="ArrowRight";if(X){if(K){c.preventDefault(),yt(f);return}if(I){c.preventDefault(),ht();return}if(f==="Escape"){c.preventDefault(),de(null),me(b.cancelled);return}return}if(x){let _=ne.findIndex(A=>A.id===x);if(f==="ArrowDown"){c.preventDefault(),_>=0&&_<ne.length-1&&B(ne[_+1].id);return}if(f==="ArrowUp"){c.preventDefault(),_>0?B(ne[_-1].id):B(null);return}if(f==="ArrowLeft"||f==="ArrowRight"){c.preventDefault(),B(null);let A=Q.indexOf(T);H(Q[Ye(A,f,1,Q.length)]);return}if(I){c.preventDefault();let A=ne.find(k=>k.id===x);if(!A)return;A.editable!==!1&&s?(de({kind:"move",eventId:A.id,dateOnly:ue(A.start),minute:aa(A.start),moved:!1}),me(b.grabbedMoveHint(A.title))):g&&g({id:A.id});return}if((f==="r"||f==="R")&&d){c.preventDefault();let A=ne.find(k=>k.id===x);A&&A.allDay!==!0&&A.editable!==!1&&(de({kind:"resize",eventId:A.id,dateOnly:ue(A.end),minute:aa(A.end),moved:!1}),me(b.grabbedResizeHint(A.title)));return}if(f==="Escape"){c.preventDefault(),B(null);return}return}if(f==="ArrowLeft"||f==="ArrowRight"||f==="Home"||f==="End"){c.preventDefault();let _=Q.indexOf(T);H(Q[Ye(_,f,1,Q.length)]);return}if(f==="ArrowDown"){ne.length>0&&(c.preventDefault(),B(ne[0].id));return}if(I){if(ne.length>0)c.preventDefault(),B(ne[0].id);else if(ie.length===0&&u){let _=M.config.dayEndHour*60,A=Dt(M.config.dayStartHour*60,M.config),k=Math.min(A+60,_);k>A&&(c.preventDefault(),u(Pe({dateOnly:T,minuteOfDay:A},{dateOnly:T,minuteOfDay:k})),me(b.createHere(`${Ee(T,r)} ${vn(T,A,r)}`)))}}},[X,x,T,ie,ne,Q,s,d,g,u,yt,ht,M.config,b,r]),ce={"--ac-tg-cols":M.columns.length,"--ac-tg-hours":M.config.dayEndHour-M.config.dayStartHour,...l??{}},Ft=b.allDay;return Ue(ra,{children:[Ue("div",{className:Pt("aethercal-calendar","aethercal-timegrid",C&&"is-dragging",R.status==="resizing"&&"is-resizing",R.status==="selecting"&&"is-selecting"),role:"grid","aria-label":Qn(n,r),"aria-describedby":J,"aria-activedescendant":tt,tabIndex:0,"data-view":t,style:ce,onKeyDown:Gt,children:[Ue("div",{className:"aethercal-tg-head",role:"row",children:[fe("div",{className:"aethercal-tg-corner"}),M.columns.map(c=>fe("div",{role:"columnheader",className:Pt("aethercal-tg-colhead",c.dateOnly===ee&&"is-today"),"data-date":c.dateOnly,children:fe("span",{className:"aethercal-tg-colhead-date",children:Ee(c.dateOnly,r)})},c.dateOnly))]}),Ue("div",{className:"aethercal-tg-allday",role:"row",children:[fe("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:Ft}),M.columns.map(c=>fe("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":c.dateOnly,onDragOver:$?f=>f.preventDefault():void 0,onDrop:$?q(c.dateOnly,!1):void 0,onContextMenu:v?h(c.dateOnly,!1):void 0,children:c.allDay.map(f=>{let I=X?.eventId===f.id&&c.dateOnly===T||!X&&x===f.id&&c.dateOnly===T;return fe(kt,{id:m(c.dateOnly,f.id),event:f,interactive:te(f),isActive:I,isGrabbed:X?.eventId===f.id&&c.dateOnly===T,timeLabel:null,canDrag:$,onDragStart:U,onDragEnd:re,isPending:y.has(f.id),isRolledBack:D.has(f.id),...g?{onClick:()=>g({id:f.id})}:{},...v?{onContextMenu:()=>v({id:f.id})}:{}},f.id)})},c.dateOnly))]}),Ue("div",{className:"aethercal-tg-body",role:"row",tabIndex:0,children:[fe("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:M.hourMarks.map(c=>fe("div",{className:"aethercal-tg-hour",style:{top:Le(c.topFraction)},children:Zn(c.hour,r)},c.hour))}),M.columns.map(c=>{let f=!x&&!X&&c.dateOnly===T,I=X?.dateOnly===c.dateOnly;return Ue("div",{id:V(c.dateOnly),role:"gridcell",className:Pt("aethercal-tg-col",c.dateOnly===ee&&"is-today",f&&"is-active",I&&"is-drop-target"),"data-date":c.dateOnly,onDragOver:$?K=>K.preventDefault():void 0,onDrop:$?q(c.dateOnly,!0):void 0,onPointerDown:Y?z(c.dateOnly):void 0,onContextMenu:v?h(c.dateOnly,!0):void 0,children:[M.hourMarks.map(K=>fe("div",{className:"aethercal-tg-line",style:{top:Le(K.topFraction)},"aria-hidden":"true"},K.hour)),E&&E.dateOnly===c.dateOnly?fe("div",{className:"aethercal-tg-select-band",style:{top:Le(E.topFraction),height:Le(E.heightFraction)},"aria-hidden":"true"}):null,c.timed.map(K=>{let{event:_}=K,A=_.editable!==!1,k=Xa(K,r,b.continues,b.endsAt),we=G?.id===_.id?G:null,he=we?pt([{..._,start:we.start,end:we.end}],c.dateOnly,M.config)[0]:void 0,nt=he?he.topFraction:K.topFraction,ct=he?he.heightFraction:K.heightFraction,Ne=X?.eventId===_.id&&c.dateOnly===T||!X&&x===_.id&&c.dateOnly===T,zt=X?.eventId===_.id&&c.dateOnly===T,fa={top:Le(nt),height:Le(ct),left:Le(K.lane/K.laneCount),width:Le(1/K.laneCount),..._.color?{"--ac-tg-event-accent":_.color}:{}};return Ue("div",{id:m(c.dateOnly,_.id),className:Pt("aethercal-tg-event",!A&&"is-locked",y.has(_.id)&&"is-pending",D.has(_.id)&&"is-rolledback",!!we&&"is-resizing",Ne&&"is-active",zt&&"is-grabbed"),...te(_)?{role:"button"}:{},draggable:A&&$,"data-event-id":_.id,"data-lane":K.lane,"data-lane-count":K.laneCount,"aria-label":`${k} ${_.title}`,title:_.title,style:fa,onDragStart:at=>{if(!$||P.current?.kind==="resize"){at.preventDefault();return}at.dataTransfer.setData("text/plain",_.id),at.dataTransfer.effectAllowed="move",U(_.id)},onDragEnd:re,onClick:g?()=>g({id:_.id}):void 0,onContextMenu:v?at=>{at.preventDefault(),at.stopPropagation(),v({id:_.id})}:void 0,children:[fe("time",{className:"aethercal-tg-event-time",children:k})," ",fe("span",{className:"aethercal-tg-event-title",children:_.title}),S&&A?Ue(ra,{children:[fe("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:W(_,"start")}),fe("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:W(_,"end")})]}):null]},_.id)}),N!==null&&c.dateOnly===ee?fe("div",{className:"aethercal-now-indicator",style:{top:Le(N)},"aria-hidden":"true"}):null]},c.dateOnly)})]})]}),fe(lt,{id:J,text:b.keyboardHint}),fe(st,{message:ke})]})}import*as le from"react";function Me(...e){return e.filter(Boolean).join(" ")}var Ie=e=>`${e*100}%`,hn=new Set,Ja="unassigned",ia=e=>e.resource?`r:${e.resource.id}`:Ja;function vt(e,t){let n=t.getBoundingClientRect();return n.width>0?(e-n.left)/n.width:0}function bn(e){let t=O(e);return t.getHours()*60+t.getMinutes()}function Lt(e,t,n){let a=O(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}import{Fragment as ja,jsx as ge,jsxs as Qe}from"react/jsx-runtime";function oa(e){let{dayHeaders:t,nowDateKey:n,locale:a,resourcesLabel:r}=e;return Qe("div",{className:"aethercal-tl-head",role:"row",children:[ge("div",{className:"aethercal-tl-corner",role:"columnheader",children:r}),ge("div",{className:"aethercal-tl-days",children:t.map(i=>ge("div",{role:"columnheader",className:Me("aethercal-tl-dayhead",i.dateOnly===n&&"is-today"),"data-date":i.dateOnly,style:{left:Ie(i.leftFraction),width:Ie(i.widthFraction)},children:ge("span",{children:Ee(i.dateOnly,a)})},i.dateOnly))})]})}function sa(e){let{group:t,domId:n,isActive:a,countLabel:r,onToggle:i}=e;return ge("div",{role:"row",className:Me("aethercal-tl-group",t.collapsed&&"is-collapsed"),children:ge("div",{className:"aethercal-tl-group-head",role:"rowheader",children:Qe("button",{type:"button",id:n,className:Me("aethercal-tl-group-toggle",a&&"is-active"),"aria-expanded":!t.collapsed,tabIndex:-1,onClick:i,children:[ge("span",{className:"aethercal-tl-caret","aria-hidden":"true",children:"\u25BE"}),ge("span",{children:t.id})," ",ge("span",{className:"aethercal-tl-group-count",children:r})]})})})}function la(e){let{row:t,days:n,config:a,ticks:r,nowFraction:i,locale:o,messages:l,rowDomId:s,evtDomId:d,isRowActive:u,isCurrentRow:g,activeEventId:v,kbGrab:y,isKbTarget:D,selectBand:b,resizePreview:M,pendingIds:N,rolledBackIds:ee,dropEnabled:R,resizeEnabled:L,selectEnabled:P,eventInteractive:G,onDrop:w,onPointerDown:E,onTrackContextMenu:F,beginDrag:$,endDrag:S,startResize:Y,onEventClick:C,onEventContextMenu:q}=e,U={"--ac-tl-lanes":t.laneCount},re=t.resource?.color?{"--ac-tl-row-accent":t.resource.color}:{};return Qe("div",{role:"row",className:Me("aethercal-tl-row",!t.resource&&"is-unassigned"),children:[Qe("div",{id:s,role:"rowheader",className:Me("aethercal-tl-rowhead",u&&"is-active"),style:re,children:[t.resource?.color?ge("span",{className:"aethercal-tl-swatch","aria-hidden":"true"}):null,ge("span",{className:"aethercal-tl-rowhead-title",children:t.resource?t.resource.title:l.timelineUnassigned})]}),Qe("div",{role:"gridcell",className:Me("aethercal-tl-track",D&&"is-drop-target"),"data-resource-id":t.resource?.id??"",style:U,onDragOver:R&&t.resource?W=>W.preventDefault():void 0,onDrop:R&&t.resource?w:void 0,onPointerDown:P&&t.resource?E:void 0,onContextMenu:F,children:[r.map(W=>ge("div",{className:Me("aethercal-tl-line",W.isDayStart&&"is-day-start"),style:{left:Ie(W.leftFraction)},"aria-hidden":"true"},`${W.dateOnly}-${W.hour}`)),b&&b.resourceId===t.resource?.id?ge("div",{className:"aethercal-tl-select-band",style:{left:Ie(b.leftFraction),width:Ie(b.widthFraction)},"aria-hidden":"true"}):null,t.blocks.map(W=>{let{event:z}=W,p=z.editable!==!1,h=M?.id===z.id?M:null,Z=y?.eventId===z.id||!y&&v===z.id&&g,Q=W.allDay?l.allDay:pe(h?.start??z.start,o),T=h?qt({...z,start:h.start,end:h.end},n,a)[0]:void 0,H={left:Ie(T?.leftFraction??W.leftFraction),width:Ie(T?.widthFraction??W.widthFraction),top:Ie(W.lane/W.laneCount),height:Ie(1/W.laneCount),...z.color?{"--ac-tl-event-accent":z.color}:{}};return Qe("div",{id:d(z.id),className:Me("aethercal-tl-event",W.allDay&&"is-allday",!p&&"is-locked",W.continuesBefore&&"continues-before",W.continuesAfter&&"continues-after",N.has(z.id)&&"is-pending",ee.has(z.id)&&"is-rolledback",!!h&&"is-resizing",Z&&"is-active",y?.eventId===z.id&&"is-grabbed"),...G(z)?{role:"button"}:{},draggable:p&&R,"data-event-id":z.id,"data-lane":W.lane,"aria-label":`${Q} ${z.title}`,title:z.title,style:H,onDragStart:x=>{if(!$(z.id)){x.preventDefault();return}x.dataTransfer.setData("text/plain",z.id),x.dataTransfer.effectAllowed="move"},onDragEnd:S,onClick:C?()=>C(z.id):void 0,onContextMenu:q?x=>{x.preventDefault(),x.stopPropagation(),q(z.id)}:void 0,children:[ge("time",{className:"aethercal-tl-event-time",children:Q})," ",ge("span",{className:"aethercal-tl-event-title",children:z.title}),L&&p&&!W.allDay?Qe(ja,{children:[ge("div",{className:"aethercal-tl-resize-handle aethercal-tl-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:Y(z,"start")}),ge("div",{className:"aethercal-tl-resize-handle aethercal-tl-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:Y(z,"end")})]}):null]},z.id)}),i!==null?ge("div",{className:"aethercal-tl-now",style:{left:Ie(i)},"aria-hidden":"true"}):null]})]})}var da="aethercal-timeline-styles",ca=`
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
`;function wn(){if(typeof document>"u"||document.getElementById(da))return;let e=document.createElement("style");e.id=da,e.textContent=ca,document.head.appendChild(e)}import*as ve from"react";function ua(e){let{timeline:t,days:n,events:a,dropRows:r,locale:i,messages:o,eventInteractive:l,axisFractionOf:s,toggleGroup:d,announce:u,itemDomId:g,evtDomId:v,onEventDrop:y,onEventResize:D,onRangeSelect:b,onEventClick:M}=e,N=Ce,[ee,R]=ve.useState(0),[L,P]=ve.useState(0),[G,w]=ve.useState(null),[E,F]=ve.useState(null);ve.useEffect(()=>{ee>t.items.length-1&&(R(Math.max(0,t.items.length-1)),w(null),F(null))},[t.items.length,ee]),ve.useEffect(()=>{L>n.length-1&&P(Math.max(0,n.length-1))},[n.length,L]);let $=t.items[ee],S=$?.kind==="row"?$.row:void 0,Y=ve.useMemo(()=>(S?.blocks??[]).map(p=>p.event).filter(p=>l(p)),[S,l]),C=ve.useMemo(()=>{let p=t.dayHeaders[L];if(!p||!S)return[];let h=p.leftFraction,Z=p.leftFraction+p.widthFraction,Q=1e-9;return S.blocks.filter(T=>{let H=T.leftFraction,x=T.leftFraction+T.widthFraction;return x>H?H<Z-Q&&x>h+Q:H>=h-Q&&H<Z-Q}).map(T=>T.event).filter(T=>l(T))},[t.dayHeaders,L,S,l]);ve.useEffect(()=>{let p=new Set(Y.map(h=>h.id));E&&!p.has(E.eventId)?(F(null),w(null)):!E&&G!==null&&!p.has(G)&&w(null)},[Y,G,E]);let q=t.items.length===0?void 0:E?v(E.eventId):G?v(G):g(ee),U=ve.useCallback(p=>r.find(h=>h.resource?.id===p)?.resource?.title??p,[r]),re=ve.useCallback(p=>{let h=E;if(!h)return;let Z=a.find(B=>B.id===h.eventId);if(!Z)return;let Q=Z.allDay===!0,T=h.dateOnly,H=h.minute,x=h.kind==="move"?h.resourceId:"";if(p==="ArrowLeft"||p==="ArrowRight")if(Q)T=be(T,p==="ArrowLeft"?-1:1);else{let B=p==="ArrowLeft"?-N:N,X=He(s(T,H+B),n,t.config,N);if(!X)return;T=X.dateOnly,H=X.minuteOfDay??H}else if(h.kind==="move"&&(p==="ArrowUp"||p==="ArrowDown")){let B=r.findIndex(de=>de.resource?.id===x),X=p==="ArrowUp"?B-1:B+1;if(B===-1||X<0||X>=r.length)return;x=r[X].resource.id}else return;if(!(T===h.dateOnly&&H===h.minute&&(h.kind!=="move"||x===h.resourceId)))if(h.kind==="move"){let B=Q?Ee(T,i):`${Ee(T,i)} ${Lt(T,H,i)}`;u(o.movedTo(`${U(x)} \xB7 ${B}`)),F({...h,dateOnly:T,minute:H,resourceId:x,moved:!0})}else{let B=Se(Z,"end",T,H);u(o.resizedTo(`${pe(B.start,i)} \u2013 ${pe(B.end,i)}`)),F({...h,dateOnly:T,minute:H,moved:!0})}},[E,a,N,n,t.config,r,s,U,u,o,i]),W=ve.useCallback(()=>{let p=E;if(!p)return;if(!p.moved){w(p.eventId),F(null);return}let h=a.find(Z=>Z.id===p.eventId);if(h&&h.editable!==!1&&p.kind==="move"&&y){let Z=h.allDay===!0?null:p.minute;y(Ge(h,p.dateOnly,Z,p.resourceId)),u(o.dropped(`${U(p.resourceId)} \xB7 ${h.allDay===!0?Ee(p.dateOnly,i):Lt(p.dateOnly,p.minute,i)}`)),w(null)}else if(h&&h.editable!==!1&&p.kind==="resize"&&D){let Z=Se(h,"end",p.dateOnly,p.minute);D(Z),u(o.resized(`${pe(Z.start,i)} \u2013 ${pe(Z.end,i)}`)),w(p.eventId)}else w(p.eventId);F(null)},[E,a,y,D,U,u,o,i]),z=ve.useCallback(p=>{let{key:h}=p,Z=h==="Enter"||h===" "||h==="Spacebar",Q=h==="ArrowUp"||h==="ArrowDown"||h==="ArrowLeft"||h==="ArrowRight",T=t.items.length-1;if(E){if(Q){p.preventDefault(),re(h);return}if(Z){p.preventDefault(),W();return}h==="Escape"&&(p.preventDefault(),F(null),u(o.cancelled));return}if(G){let H=Y.findIndex(x=>x.id===G);if(h==="ArrowRight"){p.preventDefault(),H>=0&&H<Y.length-1&&w(Y[H+1].id);return}if(h==="ArrowLeft"){p.preventDefault(),H>0?w(Y[H-1].id):w(null);return}if(h==="ArrowUp"||h==="ArrowDown"){p.preventDefault(),w(null),R(x=>Math.min(Math.max(x+(h==="ArrowUp"?-1:1),0),T));return}if(Z){p.preventDefault();let x=Y.find(B=>B.id===G);if(!x)return;x.editable!==!1&&y&&S?.resource?(F({kind:"move",eventId:x.id,dateOnly:ue(x.start),minute:bn(x.start),resourceId:S.resource.id,moved:!1}),u(o.grabbedMoveHint(x.title))):M&&M({id:x.id});return}if((h==="r"||h==="R")&&D){p.preventDefault();let x=Y.find(B=>B.id===G);x&&x.allDay!==!0&&x.editable!==!1&&(F({kind:"resize",eventId:x.id,dateOnly:ue(x.end),minute:bn(x.end),moved:!1}),u(o.grabbedResizeHint(x.title)));return}h==="Escape"&&(p.preventDefault(),w(null));return}if(h==="ArrowUp"||h==="ArrowDown"){p.preventDefault(),R(H=>Math.min(Math.max(H+(h==="ArrowUp"?-1:1),0),T));return}if(h==="ArrowLeft"||h==="ArrowRight"){p.preventDefault(),P(H=>Math.min(Math.max(H+(h==="ArrowLeft"?-1:1),0),Math.max(0,n.length-1)));return}if(h==="Home"||h==="End"){p.preventDefault(),P(h==="Home"?0:Math.max(0,n.length-1));return}if(Z){if($?.kind==="group"){p.preventDefault(),d($.group.id);return}if(C.length>0){p.preventDefault(),w(C[0].id);return}if(S?.resource&&b&&n.length>0){let H=n[Math.min(L,n.length-1)],x=t.config.dayStartHour*60,B=Math.min(x+60,t.config.dayEndHour*60);B>x&&(p.preventDefault(),b(Pe({dateOnly:H,minuteOfDay:x,resourceId:S.resource.id},{dateOnly:H,minuteOfDay:B,resourceId:S.resource.id})),u(o.createHere(`${S.resource.title} \xB7 ${Ee(H,i)} ${Lt(H,x,i)}`)))}}},[E,G,Y,C,$,S,t.items.length,t.config,n,L,y,D,M,b,re,W,d,u,o,i]);return{activeItem:ee,activeEventId:G,kbGrab:E,currentRow:S,activeDescendantId:q,handleKeyDown:z}}import*as ye from"react";function pa(e){let{days:t,config:n,events:a,axisFractionOf:r,onEventDrop:i,onEventResize:o,onRangeSelect:l,onContextMenu:s}=e,[d,u]=ye.useReducer(mt,ot),g=ye.useRef(null),[v,y]=ye.useState(null),[D,b]=ye.useState(null),M=ye.useCallback(w=>E=>{if(E.preventDefault(),d.status!=="dragging"){u({type:"COMMIT"});return}let F=d.eventId,$=E.dataTransfer.getData("text/plain");if(u({type:"COMMIT"}),$&&$!==F||!i||!w.resource)return;let S=a.find(q=>q.id===F);if(!S||S.editable===!1)return;let Y=He(vt(E.clientX,E.currentTarget),t,n);if(!Y)return;let C=S.allDay===!0?null:Y.minuteOfDay;i(Ge(S,Y.dateOnly,C,w.resource.id))},[d,a,i,t,n]),N=ye.useCallback(w=>!i||g.current?.kind==="resize"?!1:(u({type:"DRAG_START",eventId:w}),!0),[i]),ee=ye.useCallback(()=>u({type:"CANCEL"}),[]),R=ye.useCallback((w,E)=>F=>{if(!o||w.editable===!1||F.button!==0||g.current)return;let $=F.currentTarget.closest(".aethercal-tl-track");$&&(F.preventDefault(),F.stopPropagation(),g.current={kind:"resize",pointerId:F.pointerId,eventId:w.id,edge:E,trackEl:$,payload:null},F.currentTarget.setPointerCapture?.(F.pointerId),u({type:"RESIZE_START",eventId:w.id,edge:E}))},[o]),L=ye.useCallback(w=>E=>{if(!l||E.button!==0||!w.resource||g.current||E.target.closest("[data-event-id], button"))return;let F=E.currentTarget,$=He(vt(E.clientX,F),t,n);if(!$)return;let S=$.minuteOfDay??0;g.current={kind:"select",pointerId:E.pointerId,resourceId:w.resource.id,trackEl:F,anchorDate:$.dateOnly,anchorMinute:S,currentDate:$.dateOnly,currentMinute:S},F.setPointerCapture?.(E.pointerId),u({type:"SELECT_START",point:{dateOnly:$.dateOnly,minuteOfDay:S,resourceId:w.resource.id}})},[l,t,n]),P=d.status==="resizing"||d.status==="selecting";ye.useLayoutEffect(()=>{if(!P)return;let w=Y=>{let C=g.current;if(!C||Y.pointerId!==C.pointerId)return;let q=He(vt(Y.clientX,C.trackEl),t,n);if(!q)return;if(C.kind==="resize"){let W=a.find(p=>p.id===C.eventId);if(!W)return;let z=Se(W,C.edge,q.dateOnly,q.minuteOfDay??0);C.payload=z,y(z);return}C.currentDate=q.dateOnly,C.currentMinute=q.minuteOfDay??0;let U=r(C.anchorDate,C.anchorMinute),re=r(C.currentDate,C.currentMinute);b({resourceId:C.resourceId,leftFraction:Math.min(U,re),widthFraction:Math.abs(re-U)})},E=Y=>{let C=g.current;g.current=null,y(null),b(null),Y&&C&&(C.kind==="resize"&&C.payload&&o&&o(C.payload),C.kind==="select"&&l&&(C.currentDate!==C.anchorDate||C.currentMinute!==C.anchorMinute)&&l(Pe({dateOnly:C.anchorDate,minuteOfDay:C.anchorMinute,resourceId:C.resourceId},{dateOnly:C.currentDate,minuteOfDay:C.currentMinute,resourceId:C.resourceId}))),u({type:Y?"COMMIT":"CANCEL"})},F=Y=>{g.current&&Y.pointerId!==g.current.pointerId||E(!0)},$=Y=>{g.current&&Y.pointerId!==g.current.pointerId||E(!1)},S=Y=>{Y.key==="Escape"&&E(!1)};return window.addEventListener("pointermove",w),window.addEventListener("pointerup",F),window.addEventListener("pointercancel",$),window.addEventListener("keydown",S),()=>{window.removeEventListener("pointermove",w),window.removeEventListener("pointerup",F),window.removeEventListener("pointercancel",$),window.removeEventListener("keydown",S)}},[P,a,t,n,r,o,l]);let G=ye.useCallback(w=>{if(!s||w.target.closest("[data-event-id], button"))return;let E=He(vt(w.clientX,w.currentTarget),t,n);if(!E)return;w.preventDefault();let F=O(`${E.dateOnly}T00:00:00`),$=new Date(F.getFullYear(),F.getMonth(),F.getDate(),0,E.minuteOfDay??0,0);s({start:ae($)})},[s,t,n]);return{interaction:d,resizePreview:v,selectBand:D,handleDrop:M,beginDrag:N,endDrag:ee,startResize:R,startSelect:L,emptyContextMenu:G}}import{Fragment as qa,jsx as Be,jsxs as ga}from"react/jsx-runtime";function Dn(e){let{days:t,resources:n,events:a,locale:r,config:i,now:o,themeVars:l,defaultCollapsedGroupIds:s,onToggleGroup:d,onEventDrop:u,onEventResize:g,onRangeSelect:v,onEventClick:y,onContextMenu:D,pendingIds:b=hn,rolledBackIds:M=hn}=e,N=le.useMemo(()=>e.messages??qe(r),[e.messages,r]);le.useEffect(()=>{Ze(),wn()},[]);let[ee,R]=le.useState(""),L=le.useCallback(m=>R(m),[]),[P,G]=le.useState(()=>new Set(s??[])),w=le.useMemo(()=>[...P],[P]),E=le.useMemo(()=>Zt(n,a,t,{...i,collapsedGroupIds:w}),[n,a,t,i,w]),F=le.useMemo(()=>E.items.flatMap(m=>m.kind==="row"?[m.row]:[]),[E.items]),$=le.useMemo(()=>F.filter(m=>m.resource!==null),[F]),S=le.useMemo(()=>Qt(o,t,E.config),[o,t,E.config]),Y=le.useMemo(()=>ue(ae(o)),[o]),C=!!u,q=!!g,U=!!v,re=le.useCallback((m,J)=>{let{windowMinutes:se,dayStartHour:te}=E.config,ie=t.length*se;if(ie<=0)return 0;let ne=t.indexOf(m);return((ne===-1?0:ne)*se+(J-te*60))/ie},[t,E.config]),W=le.useCallback(m=>{let J=!P.has(m);G(se=>{let te=new Set(se);return te.has(m)?te.delete(m):te.add(m),te}),d?.(m,J),L(J?N.groupCollapsed(m):N.groupExpanded(m))},[P,d,L,N]),z=le.useCallback(m=>!!y||m.editable!==!1&&!!(u||g),[y,u,g]),p=le.useId(),h=`${p}-hint`,Z=le.useCallback(m=>`${p}-i-${m}`,[p]),Q=le.useCallback(m=>`${p}-e-${m}`,[p]),T=pa({days:t,config:E.config,events:a,axisFractionOf:re,...u?{onEventDrop:u}:{},...g?{onEventResize:g}:{},...v?{onRangeSelect:v}:{},...D?{onContextMenu:D}:{}}),H=ua({timeline:E,days:t,events:a,dropRows:$,locale:r,messages:N,eventInteractive:z,axisFractionOf:re,toggleGroup:W,announce:L,itemDomId:Z,evtDomId:Q,...u?{onEventDrop:u}:{},...g?{onEventResize:g}:{},...v?{onRangeSelect:v}:{},...y?{onEventClick:y}:{}}),{interaction:x}=T,{activeItem:B,activeEventId:X,kbGrab:de,currentRow:ke,activeDescendantId:me}=H,V={...l??{}};return ga(qa,{children:[Be("div",{className:Me("aethercal-calendar","aethercal-timeline",x.status==="dragging"&&"is-dragging",x.status==="resizing"&&"is-resizing",x.status==="selecting"&&"is-selecting"),"data-view":"timeline",style:V,children:ga("div",{className:"aethercal-tl-body",role:"grid","aria-label":N.viewNames.timeline,"aria-describedby":h,...me!==void 0?{"aria-activedescendant":me}:{},tabIndex:0,onKeyDown:H.handleKeyDown,children:[Be(oa,{dayHeaders:E.dayHeaders,nowDateKey:Y,locale:r,resourcesLabel:N.timelineResources}),E.items.length===0?Be("div",{className:"aethercal-tl-row aethercal-tl-row-empty",role:"row",children:Be("div",{role:"gridcell",className:"aethercal-tl-empty",children:N.timelineEmpty})}):null,E.items.map((m,J)=>{let se=!X&&!de&&J===B;if(m.kind==="group")return Be(sa,{group:m.group,domId:Z(J),isActive:se,countLabel:N.timelineGroupCount(m.group.resourceCount),onToggle:()=>W(m.group.id)},`g:${m.group.id}`);let{row:te}=m;return Be(la,{row:te,days:t,config:E.config,ticks:E.ticks,nowFraction:S,locale:r,messages:N,rowDomId:Z(J),evtDomId:Q,isRowActive:se,isCurrentRow:ke===te,activeEventId:X,kbGrab:de,isKbTarget:de?.kind==="move"&&te.resource?.id===de.resourceId,selectBand:T.selectBand,resizePreview:T.resizePreview,pendingIds:b,rolledBackIds:M,dropEnabled:C,resizeEnabled:q,selectEnabled:U,eventInteractive:z,onDrop:T.handleDrop(te),onPointerDown:T.startSelect(te),...D?{onTrackContextMenu:T.emptyContextMenu}:{},beginDrag:T.beginDrag,endDrag:T.endDrag,startResize:T.startResize,...y?{onEventClick:ie=>y({id:ie})}:{},...D?{onEventContextMenu:ie=>D({id:ie})}:{}},ia(te))})]})}),Be(lt,{id:h,text:N.timelineKeyboardHint}),Be(st,{message:ee})]})}import*as Te from"react";var Za=48,Qa=.5,er=600,tr=150;function nr(e){typeof window>"u"||window.dispatchEvent(new PointerEvent("pointercancel",{pointerId:e,bubbles:!0,cancelable:!0}))}function ma(e){let{enabled:t,onSwipe:n}=e,a=Te.useRef(null),[r,i]=Te.useState(null),o=Te.useRef(void 0),l=Te.useRef(t);l.current=t;let s=Te.useRef(n);s.current=n,Te.useEffect(()=>()=>{o.current!==void 0&&window.clearTimeout(o.current)},[]);let d=Te.useCallback(v=>{!l.current||v.pointerType!=="touch"||a.current||(a.current={pointerId:v.pointerId,startX:v.clientX,startY:v.clientY,startTime:v.timeStamp,settled:!1})},[]),u=Te.useCallback(v=>{let y=a.current;if(!y||y.settled||v.pointerId!==y.pointerId)return;let D=v.clientX-y.startX,b=v.clientY-y.startY;if(v.timeStamp-y.startTime>er){y.settled=!0;return}if(Math.abs(D)<Za)return;if(Math.abs(b)>Math.abs(D)*Qa){y.settled=!0;return}y.settled=!0;let N=D<0?"next":"prev";nr(y.pointerId),s.current(N),i(N),o.current!==void 0&&window.clearTimeout(o.current),o.current=window.setTimeout(()=>i(null),tr)},[]),g=Te.useCallback(v=>{a.current?.pointerId===v.pointerId&&(a.current=null)},[]);return{handlers:{onPointerDown:d,onPointerMove:u,onPointerUp:g,onPointerCancel:g},swipeDirection:r}}import{jsx as et,jsxs as sr}from"react/jsx-runtime";function ar(...e){return e.filter(Boolean).join(" ")}function rr(e){if(e instanceof Date)return e;if(typeof e=="string"){let t=e.trim();if(t==="")return new Date;try{return O(t)}catch{return new Date}}return new Date}function ir(e){return e instanceof Date?e:typeof e=="string"?O(e):new Date}function Nt(e){let{view:t="month",events:n,resources:a,timelineDays:r,defaultCollapsedGroupIds:i,onToggleGroup:o,anchor:l,locale:s="en",theme:d,messages:u,firstDayOfWeek:g=1,maxEventsPerDay:v=3,weekdayLabels:y,formatMore:D,unavailableLabel:b,dayStartHour:M,dayEndHour:N,allDayLabel:ee,now:R,continuesLabel:L,formatEndsLabel:P,agendaEmptyLabel:G,onEventDrop:w,onEventResize:E,onRangeSelect:F,onEventClick:$,onContextMenu:S,navigation:Y=!1,navigationViews:C=!0,onRangeChange:q,onViewChange:U,pendingIds:re,rolledBackIds:W}=e;xe.useEffect(()=>{Ze()},[]);let z=xe.useMemo(()=>rr(l),[l]),p=xe.useMemo(()=>mn(d),[d]),h=xe.useMemo(()=>{let m={...ee!==void 0?{allDay:ee}:{},...L!==void 0?{continues:L}:{},...P!==void 0?{endsAt:P}:{},...G!==void 0?{noEvents:G}:{},...b!==void 0?{unavailable:b}:{},...D!==void 0?{more:D}:{},...u};return qe(s,m)},[s,ee,L,P,G,b,D,u]),[Z,Q]=xe.useState(()=>new Date);xe.useEffect(()=>{if(R!==void 0||t!=="week"&&t!=="day"&&t!=="timeline")return;let m=setInterval(()=>Q(new Date),6e4);return()=>clearInterval(m)},[R,t]);let T=xe.useMemo(()=>R!==void 0?ir(R):Z,[R,Z]),H=Number.isInteger(g)&&g>=0&&g<=6?g:1,x=Number.isInteger(v)&&v>=0?v:3,B=y&&y.length===7?y:void 0,X=ze(r),de=xe.useMemo(()=>({...M!==void 0?{dayStartHour:M}:{},...N!==void 0?{dayEndHour:N}:{}}),[M,N]),ke=xe.useCallback(m=>{if(!q)return;let se=it(z,t,m==="next"?1:-1,X);q(je(t,se,H,X))},[q,z,t,X,H]),me=ma({enabled:Y&&!!q,onSwipe:ke}),V=(()=>{if(t==="list")return et(Gn,{events:n??[],locale:s,messages:h,themeVars:p});if(t==="month")return et(Vn,{events:n??[],anchor:z,locale:s,messages:h,themeVars:p,firstDayOfWeek:H,maxEventsPerDay:x,...B?{weekdayLabels:B}:{},...w?{onEventDrop:w}:{},...F?{onRangeSelect:F}:{},...$?{onEventClick:$}:{},...S?{onContextMenu:S}:{},...re?{pendingIds:re}:{},...W?{rolledBackIds:W}:{}});if(t==="timeline")return et(Dn,{days:Ut(z,X),resources:a??[],events:n??[],locale:s,messages:h,themeVars:p,config:de,now:T,...i?{defaultCollapsedGroupIds:i}:{},...o?{onToggleGroup:o}:{},...w?{onEventDrop:w}:{},...E?{onEventResize:E}:{},...F?{onRangeSelect:F}:{},...$?{onEventClick:$}:{},...S?{onContextMenu:S}:{},...re?{pendingIds:re}:{},...W?{rolledBackIds:W}:{}});if(t==="week"||t==="day"){let m=t==="week"?_t(z,H):[ue(ae(z))];return et(yn,{view:t,days:m,events:n??[],locale:s,messages:h,themeVars:p,config:de,now:T,...w?{onEventDrop:w}:{},...E?{onEventResize:E}:{},...F?{onRangeSelect:F}:{},...$?{onEventClick:$}:{},...S?{onContextMenu:S}:{},...re?{pendingIds:re}:{},...W?{rolledBackIds:W}:{}})}return et("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,style:p,children:h.unavailable})})();return Y?sr("div",{className:"aethercal-calendar-shell",style:p,children:[et(ln,{view:t,anchor:z,now:T,locale:s,firstDayOfWeek:H,timelineDays:X,messages:h,showViews:C,...q?{onRangeChange:q}:{},...U?{onViewChange:U}:{}}),et("div",{className:ar("aethercal-swipe-viewport",me.swipeDirection==="next"&&"is-swiping-next",me.swipeDirection==="prev"&&"is-swiping-prev"),...me.handlers,children:V})]}):V}var or=Nt;import*as Re from"react";function lr(){return typeof crypto<"u"&&typeof crypto.randomUUID=="function"?crypto.randomUUID():`cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}var dr=8e3,cr=900;function En(e){let{events:t,mutate:n,timeoutMs:a=dr,rollbackFlashMs:r=cr,generateId:i=lr}=e,[o,l]=Re.useReducer(nn,tn),s=Re.useRef(t);s.current=t;let d=Re.useRef(!0),u=Re.useRef(new Map);Re.useEffect(()=>{d.current=!0;let y=u.current;return()=>{d.current=!1;for(let D of y.values())clearTimeout(D);y.clear()}},[]),Re.useEffect(()=>{for(let y of rn(t,o)){let D=o.overrides[y];l({type:"CLEAR",id:y,...D?{clientMutationId:D.clientMutationId}:{}})}},[t,o]);let g=Re.useCallback((y,D)=>{let b=i(),M=s.current.find(w=>w.id===D.id),N=u.current,ee=w=>{let E=N.get(w);E!==void 0&&(clearTimeout(E),N.delete(w))},R=()=>{N.set(`fl:${b}`,setTimeout(()=>{N.delete(`fl:${b}`),d.current&&l({type:"CLEAR",id:D.id,clientMutationId:b})},r))};l({type:"SUBMIT",id:D.id,clientMutationId:b,start:D.start,end:D.end,...M?.revision!==void 0?{baseRevision:M.revision}:{},..."resourceId"in D&&D.resourceId!==void 0?{resourceId:D.resourceId}:{}}),N.set(`to:${b}`,setTimeout(()=>{N.delete(`to:${b}`),d.current&&(l({type:"TIMEOUT",id:D.id,clientMutationId:b}),R())},a));let L=()=>{ee(`to:${b}`),d.current&&(l({type:"REJECT",id:D.id,clientMutationId:b}),R())},P={kind:y,clientMutationId:b,payload:{...D,client_mutation_id:b}},G;try{G=n(P)}catch(w){G=Promise.reject(w instanceof Error?w:new Error(String(w)))}G.then(w=>{if(w.id!==D.id){L();return}ee(`to:${b}`),d.current&&l({type:"RESOLVE",id:w.id,clientMutationId:b,start:w.start,end:w.end,revision:w.revision,...w.resourceId!==void 0?{resourceId:w.resourceId}:{}})}).catch(L)},[n,a,r,i]),v=Re.useMemo(()=>an(t,o),[t,o]);return{events:v.events,pendingIds:v.pendingIds,rolledBackIds:v.rolledBackIds,submit:g}}import{jsx as pr}from"react/jsx-runtime";function ur({events:e,mutate:t,timeoutMs:n,rollbackFlashMs:a,generateId:r,...i}){let{events:o,pendingIds:l,rolledBackIds:s,submit:d}=En({events:e,mutate:t,...n!==void 0?{timeoutMs:n}:{},...a!==void 0?{rollbackFlashMs:a}:{},...r?{generateId:r}:{}});return pr(Nt,{...i,events:o,pendingIds:l,rolledBackIds:s,onEventDrop:u=>d("drop",u),onEventResize:u=>d("resize",u)})}export{Nt as AetherCalendar,qn as CALENDAR_CSS,ln as CalendarNav,dn as DEFAULT_LOCALE_MESSAGES,ur as OptimisticCalendar,At as PRESETS,Yn as PRESET_NAMES,ca as TIMELINE_CSS,ta as TIME_GRID_CSS,yn as TimeGridView,Dn as TimelineView,or as default,un as defaultBaseTokenCss,pn as defaultTimeGridTokenCss,gn as defaultTimelineTokenCss,Ze as ensureCalendarStyles,fn as ensureTimeGridStyles,wn as ensureTimelineStyles,je as getVisibleRange,Xn as isThemePreset,O as parseLocalDateTime,qe as resolveMessages,mn as resolveThemeVars,it as stepAnchor,En as useOptimisticEvents};
