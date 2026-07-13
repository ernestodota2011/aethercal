function Ae(e){return String(e).padStart(2,"0")}function x(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,n,a,r,i,o,d]=t,c=Number(n),m=Number(a),p=Number(r),v=Number(i??"0"),f=Number(o??"0"),O=Number(d??"0");if(m<1||m>12||p<1||p>31||v>23||f>59||O>59)throw new Error(`out-of-range ISO datetime: ${e}`);let T=new Date(c,m-1,p,v,f,O);if(T.getFullYear()!==c||T.getMonth()!==m-1||T.getDate()!==p)throw new Error(`nonexistent calendar date: ${e}`);return T}function j(e){return`${e.getFullYear()}-${Ae(e.getMonth()+1)}-${Ae(e.getDate())}T${Ae(e.getHours())}:${Ae(e.getMinutes())}:${Ae(e.getSeconds())}`}function ge(e){let t=x(e);return`${t.getFullYear()}-${Ae(t.getMonth()+1)}-${Ae(t.getDate())}`}function Mn(e){return`${e.getFullYear()}-${Ae(e.getMonth()+1)}-${Ae(e.getDate())}`}function et(e){let t=x(e.start),n=x(e.end),a=new Date(t.getFullYear(),t.getMonth(),t.getDate()),r=a;if(n.getTime()>t.getTime()){let i=new Date(n.getTime()-1),o=new Date(i.getFullYear(),i.getMonth(),i.getDate());o.getTime()>a.getTime()&&(r=o)}return{startKey:Mn(a),lastKey:Mn(r)}}function ga(e,t){return(e.getDay()-t+7)%7}function tt(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate());return n.setDate(n.getDate()-ga(n,t)),n}function Ut(e,t){return Array.from({length:t},(n,a)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+a);return`${r.getFullYear()}-${Ae(r.getMonth()+1)}-${Ae(r.getDate())}`})}function Bt(e,t=1){return Ut(tt(e,t),7)}function Yt(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),1);return Ut(tt(n,t),42)}function Kt(e,t){return Ut(new Date(e.getFullYear(),e.getMonth(),e.getDate()),t)}function ke(e,t){let n=x(`${ge(e)}T00:00:00`),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()+t);return`${a.getFullYear()}-${Ae(a.getMonth()+1)}-${Ae(a.getDate())}`}function Wt(e,t){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()),a=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((a.getTime()-n.getTime())/864e5)}function ut(e,t){let n=x(e.start),a=x(e.end),r=x(t),i=Wt(n,r),o=new Date(n.getFullYear(),n.getMonth(),n.getDate()+i,n.getHours(),n.getMinutes(),n.getSeconds()),d=new Date(a.getFullYear(),a.getMonth(),a.getDate()+i,a.getHours(),a.getMinutes(),a.getSeconds()),c={id:e.id,start:j(o),end:j(d)};return e.revision!==void 0&&(c.revision=e.revision),c}var ma=370;function Cn(e){return String(e).padStart(2,"0")}function In(e){return`${e.getFullYear()}-${Cn(e.getMonth()+1)}-${Cn(e.getDate())}`}function pa(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function fa(e){let{startKey:t,lastKey:n}=et(e),a=[],r=x(t);for(let i=0;i<ma&&In(r)<=n;i+=1)a.push(In(r)),r=pa(r,1);return{keys:a,startKey:t,lastKey:n}}function Xt(e){let t=new Map;return e.forEach((n,a)=>{let{keys:r,startKey:i,lastKey:o}=fa(n),d=x(n.start).getTime(),c=x(n.end).getTime();for(let m of r){let p={entry:{event:n,isContinuation:m!==i,continuesAfter:m!==o},startMs:d,endMs:c,index:a},v=t.get(m);v?v.push(p):t.set(m,[p])}}),[...t.keys()].sort().map(n=>{let a=t.get(n);return a.sort((r,i)=>r.startMs-i.startMs||r.endMs-i.endMs||r.index-i.index),{date:n,entries:a.map(r=>r.entry)}})}function nt(e,t,n,a){let r=n*a;if(r<=0)return e;let i=Math.min(Math.max(e,0),r-1),o=i-i%a,d=Math.min(o+a-1,r-1);switch(t){case"ArrowLeft":return i>o?i-1:i;case"ArrowRight":return i<d?i+1:i;case"ArrowUp":{let c=i-a;return c>=0?c:i}case"ArrowDown":{let c=i+a;return c<r?c:i}case"Home":return o;case"End":return d;default:return i}}var at=60,Ne=15;function jt(e,t,n){return Math.min(n,Math.max(t,e))}function Ct(e,t){let n=x(`${e}T00:00:00`);return new Date(n.getFullYear(),n.getMonth(),n.getDate(),0,t,0)}function qt(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate(),e.getHours(),e.getMinutes()+t,e.getSeconds())}function It(e,t){return t==null||(e.resourceId=t),e}function rt(e,t,n=Ne){let a=t.dayStartHour*at,r=t.dayEndHour*at,i=a+jt(e,0,1)*t.windowMinutes,o=n>0?n:Ne,d=a+Math.round((i-a)/o)*o;return jt(d,a,r)}function kt(e,t){return jt(e,t.dayStartHour*at,t.dayEndHour*at)}var Jt=24*at;function Zt(e,t,n,a){let r=t+n,i=e;for(;r<0;)r+=Jt,i=ke(i,-1);for(;r>Jt;)r-=Jt,i=ke(i,1);return{dateOnly:i,minuteOfDay:kt(r,a)}}function it(e,t,n,a){if(n===null)return It(ut(e,t),a);let r=x(e.start),i=x(e.end),o=Ct(t,n),d=Wt(r,i),c=r.getHours()*at+r.getMinutes(),p=i.getHours()*at+i.getMinutes()-c,v=new Date(o.getFullYear(),o.getMonth(),o.getDate()+d,o.getHours(),o.getMinutes()+p,0),f={id:e.id,start:j(o),end:j(v)};return e.revision!==void 0&&(f.revision=e.revision),It(f,a)}function $e(e,t,n,a,r={}){let i=r.minDurationMinutes??Ne,o=x(e.start),d=x(e.end),c=Ct(n,a),m=o,p=d;if(t==="end"){let f=qt(o,i);p=c.getTime()>=f.getTime()?c:f}else{let f=qt(d,-i);m=c.getTime()<=f.getTime()?c:f}let v={id:e.id,start:j(m),end:j(p)};return e.revision!==void 0&&(v.revision=e.revision),v}function We(e,t,n={}){let a=n.minDurationMinutes??Ne;if(e.minuteOfDay===null||t.minuteOfDay===null){let[p,v]=e.dateOnly<=t.dateOnly?[e.dateOnly,t.dateOnly]:[t.dateOnly,e.dateOnly],f=x(`${p}T00:00:00`),O=x(`${v}T00:00:00`),T=new Date(O.getFullYear(),O.getMonth(),O.getDate()+1),y={start:j(f),end:j(T),allDay:!0};return It(y,e.resourceId)}let i=Ct(e.dateOnly,e.minuteOfDay??0),o=Ct(t.dateOnly,t.minuteOfDay??0),d=i.getTime()<=o.getTime()?i:o,c=i.getTime()<=o.getTime()?o:i;c.getTime()===d.getTime()&&(c=qt(d,a));let m={start:j(d),end:j(c),allDay:!1};return It(m,e.resourceId)}var He=60,va=24*He,ya=864e5;function St(e,t,n){return Math.min(n,Math.max(t,e))}function ht(e={}){let t=e.dayStartHour,n=e.dayEndHour,a=Number.isFinite(t)&&t!==void 0?St(Math.trunc(t),0,23):0,r=Number.isFinite(n)&&n!==void 0?St(Math.trunc(n),1,24):24,[i,o]=r>a?[a,r]:[0,24];return{dayStartHour:i,dayEndHour:o,windowMinutes:(o-i)*He}}function kn(e){let t=[],n=[];for(let a of e)a.allDay===!0?t.push(a):n.push(a);return{allDay:t,timed:n}}function ot(e,t){let n=x(e),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()),r=Math.round((a.getTime()-t.getTime())/ya),i=n.getHours()*He+n.getMinutes()+n.getSeconds()/60;return r*va+i}function ha(e,t){let n=x(e.start).getTime(),a=x(e.end).getTime(),r=x(t.start).getTime(),i=x(t.end).getTime();return n<i&&r<a}function At(e){let t=[...e].sort((d,c)=>{let m=x(d.start).getTime(),p=x(c.start).getTime();return m!==p?m-p:x(c.end).getTime()-x(d.end).getTime()}),n=[],a=[],r=[],i=Number.NEGATIVE_INFINITY,o=()=>{let d=a.length;for(let c of r)n[c].laneCount=d;a=[],r=[],i=Number.NEGATIVE_INFINITY};for(let d of t){let c=x(d.start).getTime(),m=x(d.end).getTime();r.length>0&&c>=i&&o();let p=a.findIndex(v=>!ha(v,d));p===-1?(p=a.length,a.push(d)):a[p]=d,r.push(n.length),n.push({item:d,lane:p,laneCount:1}),i=Math.max(i,m)}return o(),n}function bt(e,t,n){let a=x(`${t}T00:00:00`),r=n.dayStartHour*He,i=n.dayEndHour*He,o=e.filter(d=>{let c=ot(d.start,a);return!(ot(d.end,a)<=r||c>=i)});return At(o).map(({item:d,lane:c,laneCount:m})=>{let p=ot(d.start,a),v=ot(d.end,a),f=St(p,r,i),O=St(v,f,i),{startKey:T,lastKey:y}=et(d);return{event:d,lane:c,laneCount:m,topFraction:(f-r)/n.windowMinutes,heightFraction:(O-f)/n.windowMinutes,isContinuation:t!==T,continuesAfter:t!==y}})}function ba(e){let t=[];for(let n=e.dayStartHour;n<e.dayEndHour;n+=1)t.push({hour:n,topFraction:(n-e.dayStartHour)*He/e.windowMinutes});return t}function Qt(e,t,n={}){let a="windowMinutes"in n?n:ht(n),{allDay:r,timed:i}=kn(t),o=i.map(c=>({event:c,startTs:x(c.start).getTime(),endTs:x(c.end).getTime()}));return{columns:e.map(c=>{let m=x(`${c}T00:00:00`),p=m.getTime(),v=new Date(m.getFullYear(),m.getMonth(),m.getDate()+1).getTime(),f=o.filter(T=>T.startTs>=v?!1:T.endTs>p?!0:T.startTs===T.endTs&&T.startTs>=p).map(T=>T.event),O=r.filter(T=>{let{startKey:y,lastKey:P}=et(T);return y<=c&&c<=P});return{dateOnly:c,allDay:O,timed:bt(f,c,a)}}),hourMarks:ba(a),config:a}}function en(e,t={}){let n="windowMinutes"in t?t:ht(t),a=e.getHours()*He+e.getMinutes()+e.getSeconds()/60,r=n.dayStartHour*He,i=n.dayEndHour*He;return a<r||a>=i?null:(a-r)/n.windowMinutes}var Xe=60,Ot=7,An=1,On=31;function Dt(e,t,n){return Math.min(n,Math.max(t,e))}function Je(e){return e===void 0||!Number.isFinite(e)?Ot:Dt(Math.trunc(e),An,On)}function tn(e){return"windowMinutes"in e?e:ht(e)}function Da(e){if(e.allDay!==!0)return{start:e.start,end:e.end};let{startKey:t,lastKey:n}=et(e);return{start:`${t}T00:00:00`,end:`${ke(n,1)}T00:00:00`}}function Sn(e,t,n){let a=n.dayStartHour*Xe,r=n.dayEndHour*Xe,i=[];return t.forEach((o,d)=>{let c=x(`${o}T00:00:00`),m=ot(e.start,c),p=ot(e.end,c);if(p<=a||m>=r)return;let v=Dt(m,a,r),f=Dt(p,v,r),O=d*n.windowMinutes;i.push({startMin:O+(v-a),endMin:O+(f-a),clippedStart:m<a,clippedEnd:p>r})}),i}function wa(e){let t=[];for(let n of e){let a=t[t.length-1];a&&a.endMin===n.startMin?(a.endMin=n.endMin,a.clippedEnd=n.clippedEnd):t.push({...n})}return t}function Ea(e,t,n){let a=t.length*n.windowMinutes;return a<=0?[]:At(e.filter(i=>Sn(i,t,n).length>0)).flatMap(({item:i,lane:o,laneCount:d})=>wa(Sn(i,t,n)).map(c=>({event:i.event,lane:o,laneCount:d,leftFraction:c.startMin/a,widthFraction:(c.endMin-c.startMin)/a,allDay:i.event.allDay===!0,continuesBefore:c.clippedStart,continuesAfter:c.clippedEnd})))}function nn(e,t,n,a={}){let r=tn(a),i=new Set(a.collapsedGroupIds??[]),o=[],d=new Set;for(let R of e)d.has(R.id)||(d.add(R.id),o.push(R));let c=[],m=new Map;for(let R of o){let _=R.groupId?R.groupId:void 0;if(_===void 0){c.push({kind:"solo",resource:R});continue}let G=m.get(_);G?G.push(R):(m.set(_,[R]),c.push({kind:"group",id:_}))}let p=new Map,v=[];for(let R of t){let _={event:R,...Da(R)},G=R.resourceId;if(G!==void 0&&d.has(G)){let Y=p.get(G);Y?Y.push(_):p.set(G,[_])}else v.push(_)}let f=(R,_,G)=>{let Y=Ea(G,n,r);return{resource:R,groupId:_,blocks:Y,laneCount:Y.reduce((b,ce)=>Math.max(b,ce.laneCount),1)}},O=[];for(let R of c){if(R.kind==="solo"){O.push({kind:"row",row:f(R.resource,null,p.get(R.resource.id)??[])});continue}let _=m.get(R.id)??[],G=i.has(R.id);if(O.push({kind:"group",group:{id:R.id,collapsed:G,resourceCount:_.length}}),!G)for(let Y of _)O.push({kind:"row",row:f(Y,R.id,p.get(Y.id)??[])})}let T=f(null,null,v);T.blocks.length>0&&O.push({kind:"row",row:T});let y=n.length,P=n.map((R,_)=>({dateOnly:R,leftFraction:y>0?_/y:0,widthFraction:y>0?1/y:0})),z=y*r.windowMinutes,ne=[];return z>0&&n.forEach((R,_)=>{let G=_*r.windowMinutes;for(let Y=r.dayStartHour;Y<r.dayEndHour;Y+=1){let b=(Y-r.dayStartHour)*Xe;ne.push({dateOnly:R,hour:Y,leftFraction:(G+b)/z,isDayStart:Y===r.dayStartHour})}}),{days:[...n],items:O,dayHeaders:P,ticks:ne,config:r}}function st(e,t,n={},a=Ne){let r=tn(n);if(t.length===0||r.windowMinutes<=0)return null;let i=t.length*r.windowMinutes,o=Dt(e,0,1)*i,d=Math.min(Math.floor(o/r.windowMinutes),t.length-1),c=o-d*r.windowMinutes,m=r.dayStartHour*Xe,p=r.dayEndHour*Xe,v=a>0?a:Ne,f=m+Math.round(c/v)*v;return{dateOnly:t[d],minuteOfDay:Dt(f,m,p)}}function an(e,t,n={}){let a=tn(n),r=t.indexOf(ge(j(e)));if(r===-1)return null;let i=e.getHours()*Xe+e.getMinutes()+e.getSeconds()/60,o=a.dayStartHour*Xe,d=a.dayEndHour*Xe;if(i<o||i>=d)return null;let c=t.length*a.windowMinutes;return c<=0?null:(r*a.windowMinutes+(i-o))/c}var xa=1;function wt(e,t,n=xa,a){let r=t.getFullYear(),i=t.getMonth(),o=t.getDate(),d,c;switch(e){case"week":{d=tt(t,n),c=new Date(d.getFullYear(),d.getMonth(),d.getDate()+7);break}case"day":{d=new Date(r,i,o),c=new Date(r,i,o+1);break}case"timeline":{d=new Date(r,i,o),c=new Date(r,i,o+Je(a));break}default:{d=new Date(r,i,1),c=new Date(r,i+1,1);break}}return{view:e,from:j(d),to:j(c)}}function Et(e,t,n,a){let r=e.getFullYear(),i=e.getMonth(),o=e.getDate();switch(t){case"week":return new Date(r,i,o+7*n);case"day":return new Date(r,i,o+n);case"timeline":return new Date(r,i,o+Je(a)*n);default:return new Date(r,i+n,1)}}var Lt={status:"idle"};function Pt(e){return e.status==="dragging"}function rn(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return Lt}}var gt={status:"idle"};function xt(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"RESIZE_START":return{status:"resizing",eventId:t.eventId,edge:t.edge};case"SELECT_START":return{status:"selecting",anchor:t.point,current:t.point};case"SELECT_MOVE":return e.status!=="selecting"?e:{status:"selecting",anchor:e.anchor,current:t.point};case"COMMIT":case"CANCEL":return gt}}var on={overrides:{},appliedRevision:{}};function Ta(e,t){let n={...e};return delete n[t],n}function sn(e,t){switch(t.type){case"SUBMIT":{let n=t.baseRevision??Number.NEGATIVE_INFINITY,a=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;return{overrides:{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"pending",start:t.start,end:t.end,...t.baseRevision!==void 0?{revision:t.baseRevision}:{},...t.resourceId!==void 0?{resourceId:t.resourceId}:{}}},appliedRevision:{...e.appliedRevision,[t.id]:Math.max(a,n)}}}case"RESOLVE":{let n=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;if(t.revision<=n)return e;let a=e.overrides[t.id],r=a!==void 0&&a.clientMutationId===t.clientMutationId&&a.status==="pending",i=t.resourceId??a?.resourceId;return{overrides:r?{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"committed",start:t.start,end:t.end,revision:t.revision,...i!==void 0?{resourceId:i}:{}}}:e.overrides,appliedRevision:{...e.appliedRevision,[t.id]:t.revision}}}case"REJECT":case"TIMEOUT":{let n=e.overrides[t.id];return!n||n.clientMutationId!==t.clientMutationId||n.status!=="pending"?e:{...e,overrides:{...e.overrides,[t.id]:{...n,status:"rolledback"}}}}case"CLEAR":{let n=e.overrides[t.id];return!n||t.clientMutationId&&n.clientMutationId!==t.clientMutationId?e:{...e,overrides:Ta(e.overrides,t.id)}}}}function ln(e,t){let n=new Set,a=new Set,r=o=>o.resourceId!==void 0?{resourceId:o.resourceId}:void 0;return{events:e.map(o=>{let d=t.overrides[o.id];return d?d.status==="pending"?(n.add(o.id),{...o,start:d.start,end:d.end,...r(d)}):d.status==="rolledback"?(a.add(o.id),o):o.revision!==void 0&&d.revision!==void 0&&o.revision>=d.revision?o:{...o,start:d.start,end:d.end,...d.revision!==void 0?{revision:d.revision}:{},...r(d)}:o}),pendingIds:n,rolledBackIds:a}}function dn(e,t){let n=new Map(e.map(r=>[r.id,r])),a=[];for(let[r,i]of Object.entries(t.overrides)){if(i.status!=="committed")continue;let o=n.get(r);o&&o.revision!==void 0&&i.revision!==void 0&&o.revision>=i.revision&&a.push(r)}return a}import*as Ge from"react";import*as Nt from"react";var cn=new Date(2023,0,1);function Pn(e,t){let n=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(a,r)=>{let i=(t+r)%7,o=new Date(cn.getFullYear(),cn.getMonth(),cn.getDate()+i);return n.format(o)})}function un(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function Ln(e,t,n){let a=new Intl.DateTimeFormat(n,{month:"short",day:"numeric"}).format(e),r=new Intl.DateTimeFormat(n,{month:"short",day:"numeric",year:"numeric"}).format(t);return`${a} \u2013 ${r}`}function Nn(e,t,n,a,r=Ot){if(e==="day")return new Intl.DateTimeFormat(n,{dateStyle:"full"}).format(t);if(e==="week"){let i=tt(t,a),o=new Date(i.getFullYear(),i.getMonth(),i.getDate()+6);return Ln(i,o,n)}if(e==="timeline"){let i=Je(r),o=new Date(t.getFullYear(),t.getMonth(),t.getDate()),d=new Date(o.getFullYear(),o.getMonth(),o.getDate()+i-1);return i===1?new Intl.DateTimeFormat(n,{dateStyle:"full"}).format(o):Ln(o,d,n)}return un(t,n)}function Tt(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(x(e))}function be(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(x(e))}function Fn(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(x(e))}import{jsx as je,jsxs as zn}from"react/jsx-runtime";function Ra(...e){return e.filter(Boolean).join(" ")}function Ma(e,t,n){let{event:a,isContinuation:r,continuesAfter:i}=e;return a.allDay===!0?n.allDay:r?i?n.continues:n.endsAt(be(a.end,t)):be(a.start,t)}function Ca({entry:e,locale:t,messages:n}){let{event:a,isContinuation:r,continuesAfter:i}=e,o=Ma(e,t,n),d=a.color?{"--ac-event-accent":a.color}:void 0;return zn("li",{className:Ra("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":a.id,"aria-label":`${o} ${a.title}`,style:d,...a.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...i?{"data-continues-after":""}:{},children:[je("span",{className:"aethercal-agenda-event-time",children:o}),je("span",{className:"aethercal-agenda-event-title",children:a.title})]})}function Gn({events:e,locale:t,messages:n,themeVars:a}){let r=Nt.useMemo(()=>Xt(e),[e]),i=Nt.useId();return r.length===0?je("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:je("p",{className:"aethercal-agenda-empty",children:n.noEvents})}):je("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:r.map(o=>{let d=`${i}-${o.date}`;return zn("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":d,"data-date":o.date,children:[je("div",{className:"aethercal-agenda-day-title",id:d,children:Fn(o.date,t)}),je("ul",{className:"aethercal-agenda-day-events",role:"list",children:o.entries.map((c,m)=>je(Ca,{entry:c,locale:t,messages:n},`${c.event.id}-${m}`))})]},o.date)})})}import{jsx as qe,jsxs as _n}from"react/jsx-runtime";var Ia=["month","week","day","list","timeline"];function gn({view:e,anchor:t,now:n,locale:a,firstDayOfWeek:r,timelineDays:i,messages:o,showViews:d=!0,onRangeChange:c,onViewChange:m}){let p=f=>{c?.(wt(e,f,r,i))},v=Nn(e,t,a,r,i);return _n("div",{className:"aethercal-nav",role:"toolbar","aria-label":o.navToolbar,children:[_n("div",{className:"aethercal-nav-group",children:[qe("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":o.navPrevious,onClick:()=>p(Et(t,e,-1)),children:qe("span",{"aria-hidden":"true",children:"\u2039"})}),qe("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-today",onClick:()=>p(n),children:o.navToday}),qe("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":o.navNext,onClick:()=>p(Et(t,e,1)),children:qe("span",{"aria-hidden":"true",children:"\u203A"})})]}),qe("span",{className:"aethercal-nav-title","aria-live":"polite",children:v}),d?qe("div",{className:"aethercal-nav-views",children:Ia.map(f=>qe("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-view","aria-pressed":f===e,onClick:()=>m?.(wt(f,t,r,i)),children:o.viewNames[f]},f))}):null]})}var ka={allDay:"All day",continues:"Continues",endsAt:e=>`ends ${e}`,more:e=>`+${e} more`,noEvents:"No events",unavailable:"This view is not available yet.",keyboardHint:"Use the arrow keys to move between days. Press Enter on an event to grab it, the arrow keys to move or resize it, Enter to drop, and Escape to cancel.",grabbedMoveHint:e=>`Grabbed ${e}. Use the arrow keys to move it, Enter to drop, Escape to cancel.`,grabbedResizeHint:e=>`Resizing ${e}. Use the up and down arrow keys to change its duration, Enter to confirm, Escape to cancel.`,movedTo:e=>`Moved to ${e}`,resizedTo:e=>`Duration ${e}`,dropped:e=>`Dropped on ${e}`,resized:e=>`Duration set to ${e}`,createHere:e=>`Create an event on ${e}`,cancelled:"Cancelled",navToolbar:"Calendar navigation",navPrevious:"Previous",navNext:"Next",navToday:"Today",viewNames:{month:"Month",week:"Week",day:"Day",list:"Agenda",timeline:"Timeline"},timelineResources:"Resources",timelineUnassigned:"Unassigned",timelineGroupCount:e=>e===1?"1 resource":`${e} resources`,groupExpanded:e=>`${e} expanded`,groupCollapsed:e=>`${e} collapsed`,timelineKeyboardHint:"Use the up and down arrow keys to move between resources and the left and right arrow keys to move between days. Press Enter on a group to expand or collapse it, or on an event to grab it; then use the left and right arrow keys to change its time, the up and down arrow keys to move it to another resource, Enter to drop it, and Escape to cancel."},Sa={allDay:"Todo el d\xEDa",continues:"Contin\xFAa",endsAt:e=>`termina ${e}`,more:e=>`+${e} m\xE1s`,noEvents:"Sin eventos",unavailable:"Esta vista a\xFAn no est\xE1 disponible.",keyboardHint:"Usa las flechas para moverte entre los d\xEDas. Pulsa Enter sobre un evento para agarrarlo, las flechas para moverlo o cambiar su duraci\xF3n, Enter para soltarlo y Escape para cancelar.",grabbedMoveHint:e=>`Agarraste el evento ${e}. Usa las flechas para moverlo, Enter para soltarlo y Escape para cancelar.`,grabbedResizeHint:e=>`Est\xE1s cambiando la duraci\xF3n de ${e}. Usa las flechas hacia arriba y abajo para ajustarla, Enter para confirmar y Escape para cancelar.`,movedTo:e=>`Movido a ${e}`,resizedTo:e=>`Duraci\xF3n ${e}`,dropped:e=>`Soltado en ${e}`,resized:e=>`Duraci\xF3n establecida en ${e}`,createHere:e=>`Crear un evento en ${e}`,cancelled:"Cancelado",navToolbar:"Navegaci\xF3n del calendario",navPrevious:"Anterior",navNext:"Siguiente",navToday:"Hoy",viewNames:{month:"Mes",week:"Semana",day:"D\xEDa",list:"Agenda",timeline:"Cronograma"},timelineResources:"Recursos",timelineUnassigned:"Sin asignar",timelineGroupCount:e=>e===1?"1 recurso":`${e} recursos`,groupExpanded:e=>`${e} desplegado`,groupCollapsed:e=>`${e} plegado`,timelineKeyboardHint:"Usa las flechas hacia arriba y abajo para moverte entre los recursos, y las flechas izquierda y derecha para moverte entre los d\xEDas. Pulsa Enter sobre un grupo para desplegarlo o plegarlo, o sobre un evento para agarrarlo; luego usa las flechas izquierda y derecha para cambiar su hora, las flechas hacia arriba y abajo para moverlo a otro recurso, Enter para soltarlo y Escape para cancelar."},mn={en:ka,es:Sa};function Aa(e){return e.toLowerCase().split("-")[0]??""}function lt(e,t,n=mn){let a=e.toLowerCase(),r=n[a]??n[Aa(e)]??n.en??mn.en;return t?{...r,...t}:r}import*as Z from"react";import{jsx as $n}from"react/jsx-runtime";function mt({message:e}){return $n("div",{className:"aethercal-sr-only","aria-live":"polite","aria-atomic":"true",children:e})}function pt({id:e,text:t}){return $n("div",{id:e,className:"aethercal-sr-only",children:t})}import{jsx as Hn,jsxs as La}from"react/jsx-runtime";function Oa(...e){return e.filter(Boolean).join(" ")}function Ft({event:e,timeLabel:t,onDragStart:n,onDragEnd:a,isPending:r,isRolledBack:i,onClick:o,onContextMenu:d,id:c,interactive:m,isActive:p,isGrabbed:v}){let f=e.editable!==!1,O=e.color?{"--ac-event-accent":e.color}:void 0,T=t?`${t} ${e.title}`:e.title;return La("div",{className:Oa("aethercal-event",!f&&"is-locked",r&&"is-pending",i&&"is-rolledback",p&&"is-active",v&&"is-grabbed"),...c?{id:c}:{},...m?{role:"button"}:{},draggable:f,"data-event-id":e.id,"aria-label":T,title:e.title,style:O,onDragStart:y=>{y.dataTransfer.setData("text/plain",e.id),y.dataTransfer.effectAllowed="move",n(e.id)},onDragEnd:a,onClick:o,onContextMenu:d?y=>{y.preventDefault(),y.stopPropagation(),d()}:void 0,children:[t?Hn("time",{className:"aethercal-event-time",children:t}):null,t?" ":null,Hn("span",{className:"aethercal-event-title",children:e.title})]})}import{Fragment as Ga,jsx as ze,jsxs as Gt}from"react/jsx-runtime";var Vn=new Set,ft=7,Un=6;function Bn(...e){return e.filter(Boolean).join(" ")}function Pa(e){let t=[];for(let n=0;n<e.length;n+=ft)t.push(e.slice(n,n+ft));return t}function Na(e){let t=new Map;for(let n of e){let a=ge(n.start),r=t.get(a);r?r.push(n):t.set(a,[n])}return t}function Fa(e){return{start:`${e}T00:00:00`,end:`${ke(e,1)}T00:00:00`,allDay:!0}}function Yn(e){let{events:t,anchor:n,locale:a,firstDayOfWeek:r,messages:i,weekdayLabels:o,maxEventsPerDay:d,themeVars:c,onEventDrop:m,onRangeSelect:p,onEventClick:v,onContextMenu:f,pendingIds:O=Vn,rolledBackIds:T=Vn}=e,y=Z.useMemo(()=>Yt(n,r),[n,r]),P=Z.useMemo(()=>Pa(y),[y]),z=Z.useMemo(()=>o??Pn(a,r),[o,a,r]),ne=Z.useMemo(()=>Na(t),[t]),R=n.getMonth(),_=ge(j(new Date)),G=Z.useMemo(()=>ge(j(n)),[n]),[Y,b]=Z.useReducer(rn,Lt),[ce,me]=Z.useState(()=>new Set),pe=Z.useId(),[q,De]=Z.useState(G),[ae,W]=Z.useState(null),[U,de]=Z.useState(null),[xe,Te]=Z.useState("");Z.useEffect(()=>{y.includes(q)||(De(G),W(null),de(null))},[y,q,G]);let fe=Z.useCallback(F=>!!v||F.editable!==!1&&!!m,[v,m]);Z.useEffect(()=>{let F=new Set((ne.get(q)??[]).filter(A=>fe(A)).map(A=>A.id));U&&!F.has(U.eventId)?(de(null),W(null)):!U&&ae!==null&&!F.has(ae)&&W(null)},[ne,q,ae,U,fe]);let Ce=F=>`${pe}-c-${F}`,Le=(F,A)=>`${pe}-e-${F}-${A}`,Q=`${pe}-hint`,K=U?Le(q,U.eventId):ae?Le(q,ae):Ce(q),Ie=Z.useCallback(F=>{me(A=>{let V=new Set(A);return V.add(F),V})},[]),ue=Z.useCallback(F=>A=>{if(A.preventDefault(),!Pt(Y)){b({type:"DROP"});return}let V=Y.eventId,se=A.dataTransfer.getData("text/plain");if(b({type:"DROP"}),se&&se!==V||!m)return;let re=t.find(ee=>ee.id===V);!re||re.editable===!1||m(ut(re,F))},[Y,t,m]),oe=!!m,J=Z.useCallback(F=>{if(!U)return;let A=ke(U.targetDate,F),V=y[0],se=y[y.length-1];A<V||A>se||(Te(i.movedTo(Tt(A,a))),de({...U,targetDate:A,moved:!0}))},[U,y,a,i]),Re=Z.useCallback(()=>{if(!U)return;if(!U.moved){W(U.eventId),de(null);return}let F=t.find(A=>A.id===U.eventId);F&&F.editable!==!1&&m&&(m(ut(F,U.targetDate)),Te(i.dropped(Tt(U.targetDate,a)))),De(U.targetDate),W(null),de(null)},[U,t,m,i,a]),Pe={ArrowLeft:-1,ArrowRight:1,ArrowUp:-ft,ArrowDown:ft},ve=Z.useCallback(F=>{let{key:A}=F,V=A==="Enter"||A===" "||A==="Spacebar";if(U){if(A in Pe){F.preventDefault(),J(Pe[A]);return}if(V){F.preventDefault(),Re();return}if(A==="Escape"){F.preventDefault(),de(null),Te(i.cancelled);return}return}let se=ne.get(q)??[],re=se.filter(ee=>fe(ee));if(ae){let ee=re.findIndex(X=>X.id===ae);if(A==="ArrowDown"){F.preventDefault(),ee>=0&&ee<re.length-1&&W(re[ee+1].id);return}if(A==="ArrowUp"){F.preventDefault(),ee>0?W(re[ee-1].id):W(null);return}if(V){F.preventDefault();let X=re.find(ye=>ye.id===ae);if(!X)return;X.editable!==!1&&m?(de({eventId:X.id,targetDate:q,moved:!1}),Te(i.grabbedMoveHint(X.title))):v&&v({id:X.id});return}if(A==="Escape"){F.preventDefault(),W(null);return}if(A==="ArrowLeft"||A==="ArrowRight"||A==="Home"||A==="End"){F.preventDefault(),W(null);let X=nt(y.indexOf(q),A,Un,ft);De(y[X]);return}return}if(A in Pe||A==="Home"||A==="End"){F.preventDefault();let ee=nt(y.indexOf(q),A,Un,ft);De(y[ee]);return}V&&(re.length>0?(F.preventDefault(),Ie(q),W(re[0].id)):se.length===0&&p&&(F.preventDefault(),p(Fa(q)),Te(i.createHere(Tt(q,a)))))},[U,ae,q,y,ne,fe,m,v,p,J,Re,Ie,i,a,Pe]);return Gt(Ga,{children:[Gt("div",{className:Bn("aethercal-calendar",Pt(Y)&&"is-dragging"),role:"grid","aria-label":un(n,a),"aria-describedby":Q,"aria-activedescendant":K,tabIndex:0,"data-view":"month",style:c,onKeyDown:ve,children:[ze("div",{className:"aethercal-weekdays",role:"row",children:z.map((F,A)=>ze("div",{role:"columnheader",className:"aethercal-weekday",children:F},A))}),P.map((F,A)=>ze("div",{className:"aethercal-week",role:"row",children:F.map(V=>{let se=ne.get(V)??[],re=ce.has(V),ee=re?se:se.slice(0,d),X=se.length-ee.length,ye=new Date(`${V}T00:00:00`).getMonth()!==R,he=V===_,ie=!ae&&!U&&V===q,Se=U?.targetDate===V;return Gt("div",{id:Ce(V),role:"gridcell",className:Bn("aethercal-day",ye&&"is-outside",he&&"is-today",ie&&"is-active",Se&&"is-drop-target"),"data-date":V,onDragOver:oe?te=>te.preventDefault():void 0,onDrop:oe?ue(V):void 0,onContextMenu:f?te=>{te.target.closest("[data-event-id], button")||(te.preventDefault(),f({start:`${V}T00:00:00`}))}:void 0,children:[ze("span",{className:"aethercal-sr-only",children:Tt(V,a)}),ze("div",{className:"aethercal-day-head",children:ze("span",{className:"aethercal-day-number","aria-hidden":"true",children:Number(V.slice(-2))})}),Gt("div",{className:"aethercal-day-events",children:[ee.map(te=>{let Qe=U?.eventId===te.id||!U&&ae===te.id;return ze(Ft,{id:Le(V,te.id),event:te,interactive:fe(te),isActive:Qe,isGrabbed:U?.eventId===te.id,timeLabel:te.allDay?null:be(te.start,a),onDragStart:s=>b({type:"DRAG_START",eventId:s}),onDragEnd:()=>b({type:"DRAG_CANCEL"}),isPending:O.has(te.id),isRolledBack:T.has(te.id),...v?{onClick:()=>v({id:te.id})}:{},...f?{onContextMenu:()=>f({id:te.id})}:{}},te.id)}),X>0&&!re?ze("button",{type:"button",className:"aethercal-more",onClick:()=>Ie(V),children:i.more(X)}):null]})]},V)})},A))]}),ze(pt,{id:Q,text:i.keyboardHint}),ze(mt,{message:xe})]})}var Kn={light:{"--ac-fg":"#1f2328","--ac-muted":"#5f6672","--ac-faint":"#676e79","--ac-bg":"#ffffff","--ac-header-fg":"#4b5563","--ac-border":"#e5e7eb","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#fafafa","--ac-today-marker-bg":"#111827","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#eef1f4","--ac-event-fg":"#1f2328","--ac-event-accent":"#64748b","--ac-more-fg":"#4b5563","--ac-focus":"#2563eb","--ac-rollback":"#b91c1c","--ac-tg-now":"#dc2626"},dark:{"--ac-fg":"#e6e8eb","--ac-muted":"#9aa1ab","--ac-faint":"#868e99","--ac-bg":"#14161a","--ac-header-fg":"#b3b9c2","--ac-border":"#2a2e35","--ac-cell-bg":"#171a1f","--ac-cell-bg-outside":"#111318","--ac-today-marker-bg":"#e6e8eb","--ac-today-marker-fg":"#14161a","--ac-event-bg":"#242a32","--ac-event-fg":"#e6e8eb","--ac-event-accent":"#8b98a9","--ac-more-fg":"#b3b9c2","--ac-focus":"#6ea8fe","--ac-rollback":"#f87171","--ac-tg-now":"#f87171"},midnight:{"--ac-fg":"#dfe4ea","--ac-muted":"#8b95a1","--ac-faint":"#828a95","--ac-bg":"#0b0f14","--ac-header-fg":"#a7b0bd","--ac-border":"#1c232c","--ac-cell-bg":"#0e131a","--ac-cell-bg-outside":"#090d12","--ac-today-marker-bg":"#dfe4ea","--ac-today-marker-fg":"#0b0f14","--ac-event-bg":"#17212c","--ac-event-fg":"#dfe4ea","--ac-event-accent":"#7f8ea3","--ac-more-fg":"#a7b0bd","--ac-focus":"#74a9ff","--ac-rollback":"#fb7185","--ac-tg-now":"#fb7185"},high_contrast:{"--ac-fg":"#000000","--ac-muted":"#000000","--ac-faint":"#1a1a1a","--ac-bg":"#ffffff","--ac-header-fg":"#000000","--ac-border":"#000000","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#ffffff","--ac-today-marker-bg":"#000000","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#e0e0e0","--ac-event-fg":"#000000","--ac-event-accent":"#000000","--ac-more-fg":"#000000","--ac-focus":"#0033cc","--ac-rollback":"#b00000","--ac-tg-now":"#d00000"}};var zt=Kn,Wn=["light","dark","midnight","high_contrast"],_a=new Set(Wn),$a={"--ac-font":'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',"--ac-radius":"8px","--ac-cell-min-height":"96px"},Ha={"--ac-tg-gutter":"56px","--ac-tg-body-height":"640px","--ac-tg-hour-min-height":"44px","--ac-tg-line":"var(--ac-border)","--ac-tg-event-bg":"var(--ac-event-bg)","--ac-tg-event-fg":"var(--ac-event-fg)","--ac-tg-event-accent":"var(--ac-event-accent)"},Va={"--ac-tl-rowhead-width":"168px","--ac-tl-lane-height":"30px","--ac-tl-body-height":"560px","--ac-tl-line":"var(--ac-border)","--ac-tl-event-bg":"var(--ac-event-bg)","--ac-tl-event-fg":"var(--ac-event-fg)","--ac-tl-event-accent":"var(--ac-event-accent)","--ac-tl-group-bg":"var(--ac-cell-bg-outside)","--ac-tl-now":"var(--ac-tg-now)"},Xn=["--ac-tg-now"],Ua=/[;{}<>]/;function Jn(e){return typeof e=="string"&&_a.has(e)}function pn(e){return Object.entries(e).map(([t,n])=>`  ${t}: ${n};`).join(`
`)}function Ba(){let e={};for(let[t,n]of Object.entries(zt.light))Xn.includes(t)||(e[t]=n);return e}function jn(){let e={};for(let t of Xn){let n=zt.light[t];n!==void 0&&(e[t]=n)}return e}function fn(){return pn({...$a,...Ba()})}function vn(){return pn({...Ha,...jn()})}function yn(){return pn({...Va,...jn()})}function Ya(e){let t={};for(let[n,a]of Object.entries(e))n.startsWith("--ac-")&&(typeof a!="string"||a.trim()===""||Ua.test(a)||(t[n]=a));return t}function hn(e){return e===void 0?{}:typeof e=="string"?Jn(e)?{...zt[e]}:{}:Ya(e)}var qn="aethercal-calendar-styles",Zn=`
:where(.aethercal-calendar, .aethercal-calendar-shell) {
${fn()}
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
`;function dt(){if(typeof document>"u"||document.getElementById(qn))return;let e=document.createElement("style");e.id=qn,e.textContent=Zn,document.head.appendChild(e)}import*as H from"react";function Fe(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(x(e))}function Qn(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function ea(e,t){if(e.length===0)return"";let n=x(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(n);let a=x(e[e.length-1]),r=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${r.format(n)} \u2013 ${r.format(a)}`}var ta="aethercal-timegrid-styles",na=`
:where(.aethercal-timegrid) {
${vn()}
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
`;function bn(){if(typeof document>"u"||document.getElementById(ta))return;let e=document.createElement("style");e.id=ta,e.textContent=na,document.head.appendChild(e)}import{Fragment as ia,jsx as Ee,jsxs as Ze}from"react/jsx-runtime";function _t(...e){return e.filter(Boolean).join(" ")}var Ve=e=>`${e*100}%`,aa=new Set;function ra(e){let t=x(e);return t.getHours()*60+t.getMinutes()}function Dn(e,t,n){let a=x(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}function Ka(e,t,n,a){let{event:r,isContinuation:i,continuesAfter:o}=e;return i?o?n:a(be(r.end,t)):be(r.start,t)}function $t(e,t){let n=t.getBoundingClientRect();return n.height>0?(e-n.top)/n.height:0}function wn(e){let{view:t,days:n,events:a,locale:r,config:i,now:o,themeVars:d,onEventDrop:c,onEventResize:m,onRangeSelect:p,onEventClick:v,onContextMenu:f,pendingIds:O=aa,rolledBackIds:T=aa}=e,y=H.useMemo(()=>{if(e.messages)return e.messages;let s={...e.allDayLabel!==void 0?{allDay:e.allDayLabel}:{},...e.continuesLabel!==void 0?{continues:e.continuesLabel}:{},...e.formatEndsLabel!==void 0?{endsAt:e.formatEndsLabel}:{}};return lt(r,s)},[e.messages,e.allDayLabel,e.continuesLabel,e.formatEndsLabel,r]);H.useEffect(()=>{dt(),bn()},[]);let P=H.useMemo(()=>Qt(n,a,i),[n,a,i]),z=H.useMemo(()=>en(o,i),[o,i]),ne=H.useMemo(()=>ge(j(o)),[o]),[R,_]=H.useReducer(xt,gt),G=H.useRef(null),[Y,b]=H.useState(null),[ce,me]=H.useState(null),pe=!!c,q=!!m,De=!!p,ae=R.status==="dragging",W=H.useCallback((s,u)=>D=>{if(D.preventDefault(),R.status!=="dragging"){_({type:"COMMIT"});return}let M=R.eventId,I=D.dataTransfer.getData("text/plain");if(_({type:"COMMIT"}),I&&I!==M||!c)return;let w=a.find(Me=>Me.id===M);if(!w||w.editable===!1)return;let E=null;if(u&&w.allDay!==!0){let we=D.currentTarget.getBoundingClientRect();we.height>0&&Number.isFinite(D.clientY)&&(E=rt((D.clientY-we.top)/we.height,P.config))}c(it(w,s,E))},[R,a,c,P.config]),U=H.useCallback(s=>{G.current?.kind!=="resize"&&_({type:"DRAG_START",eventId:s})},[]),de=H.useCallback(()=>_({type:"CANCEL"}),[]),xe=H.useCallback((s,u)=>D=>{if(!m||s.editable===!1||D.button!==0||G.current)return;let M=D.currentTarget.closest(".aethercal-tg-col");M?.dataset.date&&(D.preventDefault(),D.stopPropagation(),G.current={kind:"resize",pointerId:D.pointerId,eventId:s.id,edge:u,dateOnly:M.dataset.date,colEl:M,payload:null},D.currentTarget.setPointerCapture?.(D.pointerId),_({type:"RESIZE_START",eventId:s.id,edge:u}))},[m]),Te=H.useCallback(s=>u=>{if(!p||u.button!==0||G.current||u.target.closest("[data-event-id], button"))return;let D=u.currentTarget,M=rt($t(u.clientY,D),P.config);G.current={kind:"select",pointerId:u.pointerId,anchorDate:s,anchorCol:D,anchorMinute:M,currentDate:s,currentCol:D,currentMinute:M},D.setPointerCapture?.(u.pointerId),_({type:"SELECT_START",point:{dateOnly:s,minuteOfDay:M}})},[p,P.config]),fe=R.status==="resizing"||R.status==="selecting";H.useLayoutEffect(()=>{if(!fe)return;let s=w=>{let E=G.current;if(!(!E||w.pointerId!==E.pointerId))if(E.kind==="resize"){let Me=document.elementFromPoint(w.clientX,w.clientY)?.closest(".aethercal-tg-col"),we=Me?.dataset.date?Me:E.colEl,Ye=rt($t(w.clientY,we),P.config),l=a.find(k=>k.id===E.eventId);if(!l)return;let g=$e(l,E.edge,we.dataset.date??E.dateOnly,Ye);E.payload=g,b(g)}else{let Me=document.elementFromPoint(w.clientX,w.clientY)?.closest(".aethercal-tg-col"),we=Me?.dataset.date?Me:E.currentCol;E.currentCol=we,E.currentDate=we.dataset.date??E.anchorDate,E.currentMinute=rt($t(w.clientY,we),P.config);let Ye=We({dateOnly:E.anchorDate,minuteOfDay:E.anchorMinute},{dateOnly:E.currentDate,minuteOfDay:E.currentMinute}),g=(E.currentDate===E.anchorDate?bt([{id:"__sel",title:"",start:Ye.start,end:Ye.end}],E.anchorDate,P.config):[])[0];me(g?{dateOnly:E.anchorDate,topFraction:g.topFraction,heightFraction:g.heightFraction}:null)}},u=w=>{let E=G.current;G.current=null,b(null),me(null),w&&E&&(E.kind==="resize"&&E.payload&&m&&m(E.payload),E.kind==="select"&&p&&(E.currentDate!==E.anchorDate||E.currentMinute!==E.anchorMinute)&&p(We({dateOnly:E.anchorDate,minuteOfDay:E.anchorMinute},{dateOnly:E.currentDate,minuteOfDay:E.currentMinute}))),_({type:w?"COMMIT":"CANCEL"})},D=w=>{G.current&&w.pointerId!==G.current.pointerId||u(!0)},M=w=>{G.current&&w.pointerId!==G.current.pointerId||u(!1)},I=w=>{w.key==="Escape"&&u(!1)};return window.addEventListener("pointermove",s),window.addEventListener("pointerup",D),window.addEventListener("pointercancel",M),window.addEventListener("keydown",I),()=>{window.removeEventListener("pointermove",s),window.removeEventListener("pointerup",D),window.removeEventListener("pointercancel",M),window.removeEventListener("keydown",I)}},[fe,a,P.config,m,p]);let Ce=H.useCallback((s,u)=>D=>{if(!f||D.target.closest("[data-event-id], button"))return;if(D.preventDefault(),!u){f({start:`${s}T00:00:00`});return}let M=rt($t(D.clientY,D.currentTarget),P.config),I=x(`${s}T00:00:00`),w=new Date(I.getFullYear(),I.getMonth(),I.getDate(),0,M,0);f({start:j(w)})},[f,P.config]),Le=H.useId(),Q=H.useMemo(()=>P.columns.map(s=>s.dateOnly),[P.columns]),[K,Ie]=H.useState(()=>(Q.includes(ne)?ne:Q[0])??""),[ue,oe]=H.useState(null),[J,Re]=H.useState(null),[Pe,ve]=H.useState("");H.useEffect(()=>{Q.includes(K)||(Ie(Q[0]??""),oe(null),Re(null))},[Q,K]);let F=s=>`${Le}-col-${s}`,A=(s,u)=>`${Le}-e-${s}-${u}`,V=`${Le}-hint`,se=Ne,re=H.useCallback(s=>!!v||s.editable!==!1&&!!(c||m),[v,c,m]),ee=H.useMemo(()=>{let s=P.columns.find(u=>u.dateOnly===K);return s?[...s.allDay,...s.timed.map(u=>u.event)]:[]},[P.columns,K]),X=H.useMemo(()=>ee.filter(s=>re(s)),[ee,re]);H.useEffect(()=>{let s=new Set(X.map(u=>u.id));J&&!s.has(J.eventId)?(Re(null),oe(null)):!J&&ue!==null&&!s.has(ue)&&oe(null)},[X,ue,J]);let ye=J?A(K,J.eventId):ue?A(K,ue):F(K),he=H.useCallback(s=>{let u=J;if(!u)return;let D=u.dateOnly,M=u.minute,I=a.find(E=>E.id===u.eventId),w=I?.allDay===!0;if(!w&&(s==="ArrowUp"||s==="ArrowDown")){let E=Zt(D,M,s==="ArrowUp"?-se:se,P.config);D=E.dateOnly,M=E.minuteOfDay}else s==="ArrowLeft"?D=ke(D,-1):s==="ArrowRight"&&(D=ke(D,1));if(!(D===u.dateOnly&&M===u.minute)){if(I)if(u.kind==="move")ve(y.movedTo(w?Fe(D,r):`${Fe(D,r)} ${Dn(D,M,r)}`));else{let E=$e(I,"end",D,M);ve(y.resizedTo(`${be(E.start,r)} \u2013 ${be(E.end,r)}`))}Re({...u,dateOnly:D,minute:M,moved:!0})}},[J,se,P.config,a,y,r]),ie=H.useCallback(()=>{let s=J;if(!s)return;if(!s.moved){oe(s.eventId),Re(null);return}let u=a.find(D=>D.id===s.eventId);if(u&&u.editable!==!1&&s.kind==="move"&&c){let D=it(u,s.dateOnly,u.allDay===!0?null:s.minute);c(D);let M=ge(D.start);Ie(Q.includes(M)?M:K),oe(null),ve(y.dropped(u.allDay===!0?Fe(s.dateOnly,r):Dn(s.dateOnly,s.minute,r)))}else if(u&&u.editable!==!1&&s.kind==="resize"&&m){let D=$e(u,"end",s.dateOnly,s.minute);m(D),oe(s.eventId),ve(y.resized(`${be(D.start,r)} \u2013 ${be(D.end,r)}`))}else oe(s.eventId);Re(null)},[J,a,c,m,Q,K,y,r]),Se=H.useCallback(s=>{let{key:u}=s,D=u==="Enter"||u===" "||u==="Spacebar",M=u==="ArrowUp"||u==="ArrowDown"||u==="ArrowLeft"||u==="ArrowRight";if(J){if(M){s.preventDefault(),he(u);return}if(D){s.preventDefault(),ie();return}if(u==="Escape"){s.preventDefault(),Re(null),ve(y.cancelled);return}return}if(ue){let I=X.findIndex(w=>w.id===ue);if(u==="ArrowDown"){s.preventDefault(),I>=0&&I<X.length-1&&oe(X[I+1].id);return}if(u==="ArrowUp"){s.preventDefault(),I>0?oe(X[I-1].id):oe(null);return}if(u==="ArrowLeft"||u==="ArrowRight"){s.preventDefault(),oe(null);let w=Q.indexOf(K);Ie(Q[nt(w,u,1,Q.length)]);return}if(D){s.preventDefault();let w=X.find(E=>E.id===ue);if(!w)return;w.editable!==!1&&c?(Re({kind:"move",eventId:w.id,dateOnly:ge(w.start),minute:ra(w.start),moved:!1}),ve(y.grabbedMoveHint(w.title))):v&&v({id:w.id});return}if((u==="r"||u==="R")&&m){s.preventDefault();let w=X.find(E=>E.id===ue);w&&w.allDay!==!0&&w.editable!==!1&&(Re({kind:"resize",eventId:w.id,dateOnly:ge(w.end),minute:ra(w.end),moved:!1}),ve(y.grabbedResizeHint(w.title)));return}if(u==="Escape"){s.preventDefault(),oe(null);return}return}if(u==="ArrowLeft"||u==="ArrowRight"||u==="Home"||u==="End"){s.preventDefault();let I=Q.indexOf(K);Ie(Q[nt(I,u,1,Q.length)]);return}if(u==="ArrowDown"){X.length>0&&(s.preventDefault(),oe(X[0].id));return}if(D){if(X.length>0)s.preventDefault(),oe(X[0].id);else if(ee.length===0&&p){let I=P.config.dayEndHour*60,w=kt(P.config.dayStartHour*60,P.config),E=Math.min(w+60,I);E>w&&(s.preventDefault(),p(We({dateOnly:K,minuteOfDay:w},{dateOnly:K,minuteOfDay:E})),ve(y.createHere(`${Fe(K,r)} ${Dn(K,w,r)}`)))}}},[J,ue,K,ee,X,Q,c,m,v,p,he,ie,P.config,y,r]),te={"--ac-tg-cols":P.columns.length,"--ac-tg-hours":P.config.dayEndHour-P.config.dayStartHour,...d??{}},Qe=y.allDay;return Ze(ia,{children:[Ze("div",{className:_t("aethercal-calendar","aethercal-timegrid",ae&&"is-dragging",R.status==="resizing"&&"is-resizing",R.status==="selecting"&&"is-selecting"),role:"grid","aria-label":ea(n,r),"aria-describedby":V,"aria-activedescendant":ye,tabIndex:0,"data-view":t,style:te,onKeyDown:Se,children:[Ze("div",{className:"aethercal-tg-head",role:"row",children:[Ee("div",{className:"aethercal-tg-corner"}),P.columns.map(s=>Ee("div",{role:"columnheader",className:_t("aethercal-tg-colhead",s.dateOnly===ne&&"is-today"),"data-date":s.dateOnly,children:Ee("span",{className:"aethercal-tg-colhead-date",children:Fe(s.dateOnly,r)})},s.dateOnly))]}),Ze("div",{className:"aethercal-tg-allday",role:"row",children:[Ee("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:Qe}),P.columns.map(s=>Ee("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":s.dateOnly,onDragOver:pe?u=>u.preventDefault():void 0,onDrop:pe?W(s.dateOnly,!1):void 0,onContextMenu:f?Ce(s.dateOnly,!1):void 0,children:s.allDay.map(u=>{let D=J?.eventId===u.id&&s.dateOnly===K||!J&&ue===u.id&&s.dateOnly===K;return Ee(Ft,{id:A(s.dateOnly,u.id),event:u,interactive:re(u),isActive:D,isGrabbed:J?.eventId===u.id&&s.dateOnly===K,timeLabel:null,onDragStart:U,onDragEnd:de,isPending:O.has(u.id),isRolledBack:T.has(u.id),...v?{onClick:()=>v({id:u.id})}:{},...f?{onContextMenu:()=>f({id:u.id})}:{}},u.id)})},s.dateOnly))]}),Ze("div",{className:"aethercal-tg-body",role:"row",tabIndex:0,children:[Ee("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:P.hourMarks.map(s=>Ee("div",{className:"aethercal-tg-hour",style:{top:Ve(s.topFraction)},children:Qn(s.hour,r)},s.hour))}),P.columns.map(s=>{let u=!ue&&!J&&s.dateOnly===K,D=J?.dateOnly===s.dateOnly;return Ze("div",{id:F(s.dateOnly),role:"gridcell",className:_t("aethercal-tg-col",s.dateOnly===ne&&"is-today",u&&"is-active",D&&"is-drop-target"),"data-date":s.dateOnly,onDragOver:pe?M=>M.preventDefault():void 0,onDrop:pe?W(s.dateOnly,!0):void 0,onPointerDown:De?Te(s.dateOnly):void 0,onContextMenu:f?Ce(s.dateOnly,!0):void 0,children:[P.hourMarks.map(M=>Ee("div",{className:"aethercal-tg-line",style:{top:Ve(M.topFraction)},"aria-hidden":"true"},M.hour)),ce&&ce.dateOnly===s.dateOnly?Ee("div",{className:"aethercal-tg-select-band",style:{top:Ve(ce.topFraction),height:Ve(ce.heightFraction)},"aria-hidden":"true"}):null,s.timed.map(M=>{let{event:I}=M,w=I.editable!==!1,E=Ka(M,r,y.continues,y.endsAt),Me=Y?.id===I.id?Y:null,we=Me?bt([{...I,start:Me.start,end:Me.end}],s.dateOnly,P.config)[0]:void 0,Ye=we?we.topFraction:M.topFraction,l=we?we.heightFraction:M.heightFraction,g=J?.eventId===I.id&&s.dateOnly===K||!J&&ue===I.id&&s.dateOnly===K,k=J?.eventId===I.id&&s.dateOnly===K,S={top:Ve(Ye),height:Ve(l),left:Ve(M.lane/M.laneCount),width:Ve(1/M.laneCount),...I.color?{"--ac-tg-event-accent":I.color}:{}};return Ze("div",{id:A(s.dateOnly,I.id),className:_t("aethercal-tg-event",!w&&"is-locked",O.has(I.id)&&"is-pending",T.has(I.id)&&"is-rolledback",!!Me&&"is-resizing",g&&"is-active",k&&"is-grabbed"),...re(I)?{role:"button"}:{},draggable:w,"data-event-id":I.id,"data-lane":M.lane,"data-lane-count":M.laneCount,"aria-label":`${E} ${I.title}`,title:I.title,style:S,onDragStart:$=>{if(G.current?.kind==="resize"){$.preventDefault();return}$.dataTransfer.setData("text/plain",I.id),$.dataTransfer.effectAllowed="move",U(I.id)},onDragEnd:de,onClick:v?()=>v({id:I.id}):void 0,onContextMenu:f?$=>{$.preventDefault(),$.stopPropagation(),f({id:I.id})}:void 0,children:[Ee("time",{className:"aethercal-tg-event-time",children:E})," ",Ee("span",{className:"aethercal-tg-event-title",children:I.title}),q&&w?Ze(ia,{children:[Ee("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:xe(I,"start")}),Ee("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:xe(I,"end")})]}):null]},I.id)}),z!==null&&s.dateOnly===ne?Ee("div",{className:"aethercal-now-indicator",style:{top:Ve(z)},"aria-hidden":"true"}):null]},s.dateOnly)})]})]}),Ee(pt,{id:V,text:y.keyboardHint}),Ee(mt,{message:Pe})]})}import*as L from"react";var oa="aethercal-timeline-styles",sa=`
:where(.aethercal-timeline) {
${yn()}
}
.aethercal-timeline { display: flex; flex-direction: column; }
.aethercal-tl-head,
.aethercal-tl-row,
.aethercal-tl-group {
  display: grid;
  grid-template-columns: var(--ac-tl-rowhead-width) minmax(0, 1fr);
}
.aethercal-tl-head { border-bottom: 1px solid var(--ac-border); }
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
`;function En(){if(typeof document>"u"||document.getElementById(oa))return;let e=document.createElement("style");e.id=oa,e.textContent=sa,document.head.appendChild(e)}import{Fragment as ca,jsx as le,jsxs as Be}from"react/jsx-runtime";function Ue(...e){return e.filter(Boolean).join(" ")}var _e=e=>`${e*100}%`,la=new Set,Wa="unassigned",Xa=e=>e.resource?`r:${e.resource.id}`:Wa;function Ht(e,t){let n=t.getBoundingClientRect();return n.width>0?(e-n.left)/n.width:0}function da(e){let t=x(e);return t.getHours()*60+t.getMinutes()}function xn(e,t,n){let a=x(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}function Tn(e){let{days:t,resources:n,events:a,locale:r,config:i,now:o,themeVars:d,defaultCollapsedGroupIds:c,onToggleGroup:m,onEventDrop:p,onEventResize:v,onRangeSelect:f,onEventClick:O,onContextMenu:T,pendingIds:y=la,rolledBackIds:P=la}=e,z=L.useMemo(()=>e.messages??lt(r),[e.messages,r]);L.useEffect(()=>{dt(),En()},[]);let[ne,R]=L.useState(""),[_,G]=L.useState(()=>new Set(c??[])),Y=L.useMemo(()=>[..._],[_]),b=L.useMemo(()=>nn(n,a,t,{...i,collapsedGroupIds:Y}),[n,a,t,i,Y]),ce=L.useMemo(()=>b.items.flatMap(l=>l.kind==="row"?[l.row]:[]),[b.items]),me=L.useMemo(()=>ce.filter(l=>l.resource!==null),[ce]),pe=L.useMemo(()=>an(o,t,b.config),[o,t,b.config]),q=L.useMemo(()=>ge(j(o)),[o]),[De,ae]=L.useReducer(xt,gt),W=L.useRef(null),[U,de]=L.useState(null),[xe,Te]=L.useState(null),fe=!!p,Ce=!!v,Le=!!f,Q=L.useCallback((l,g)=>{let{windowMinutes:k,dayStartHour:S}=b.config,$=t.length*k;if($<=0)return 0;let N=t.indexOf(l);return((N===-1?0:N)*k+(g-S*60))/$},[t,b.config]),K=L.useCallback(l=>{let g=!_.has(l);G(k=>{let S=new Set(k);return S.has(l)?S.delete(l):S.add(l),S}),m?.(l,g),R(g?z.groupCollapsed(l):z.groupExpanded(l))},[_,m,z]),Ie=L.useCallback(l=>g=>{if(g.preventDefault(),De.status!=="dragging"){ae({type:"COMMIT"});return}let k=De.eventId,S=g.dataTransfer.getData("text/plain");if(ae({type:"COMMIT"}),S&&S!==k||!p||!l.resource)return;let $=a.find(C=>C.id===k);if(!$||$.editable===!1)return;let N=st(Ht(g.clientX,g.currentTarget),t,b.config);if(!N)return;let h=$.allDay===!0?null:N.minuteOfDay;p(it($,N.dateOnly,h,l.resource.id))},[De,a,p,t,b.config]),ue=L.useCallback(l=>{W.current?.kind!=="resize"&&ae({type:"DRAG_START",eventId:l})},[]),oe=L.useCallback(()=>ae({type:"CANCEL"}),[]),J=L.useCallback((l,g)=>k=>{if(!v||l.editable===!1||k.button!==0||W.current)return;let S=k.currentTarget.closest(".aethercal-tl-track");S&&(k.preventDefault(),k.stopPropagation(),W.current={kind:"resize",pointerId:k.pointerId,eventId:l.id,edge:g,trackEl:S,payload:null},k.currentTarget.setPointerCapture?.(k.pointerId),ae({type:"RESIZE_START",eventId:l.id,edge:g}))},[v]),Re=L.useCallback(l=>g=>{if(!f||g.button!==0||!l.resource||W.current||g.target.closest("[data-event-id], button"))return;let k=g.currentTarget,S=st(Ht(g.clientX,k),t,b.config);if(!S)return;let $=S.minuteOfDay??0;W.current={kind:"select",pointerId:g.pointerId,resourceId:l.resource.id,trackEl:k,anchorDate:S.dateOnly,anchorMinute:$,currentDate:S.dateOnly,currentMinute:$},k.setPointerCapture?.(g.pointerId),ae({type:"SELECT_START",point:{dateOnly:S.dateOnly,minuteOfDay:$,resourceId:l.resource.id}})},[f,t,b.config]),Pe=De.status==="resizing"||De.status==="selecting";L.useLayoutEffect(()=>{if(!Pe)return;let l=N=>{let h=W.current;if(!h||N.pointerId!==h.pointerId)return;let C=st(Ht(N.clientX,h.trackEl),t,b.config);if(!C)return;if(h.kind==="resize"){let yt=a.find(Mt=>Mt.id===h.eventId);if(!yt)return;let Rt=$e(yt,h.edge,C.dateOnly,C.minuteOfDay??0);h.payload=Rt,de(Rt);return}h.currentDate=C.dateOnly,h.currentMinute=C.minuteOfDay??0;let B=Q(h.anchorDate,h.anchorMinute),Ke=Q(h.currentDate,h.currentMinute);Te({resourceId:h.resourceId,leftFraction:Math.min(B,Ke),widthFraction:Math.abs(Ke-B)})},g=N=>{let h=W.current;W.current=null,de(null),Te(null),N&&h&&(h.kind==="resize"&&h.payload&&v&&v(h.payload),h.kind==="select"&&f&&(h.currentDate!==h.anchorDate||h.currentMinute!==h.anchorMinute)&&f(We({dateOnly:h.anchorDate,minuteOfDay:h.anchorMinute,resourceId:h.resourceId},{dateOnly:h.currentDate,minuteOfDay:h.currentMinute,resourceId:h.resourceId}))),ae({type:N?"COMMIT":"CANCEL"})},k=N=>{W.current&&N.pointerId!==W.current.pointerId||g(!0)},S=N=>{W.current&&N.pointerId!==W.current.pointerId||g(!1)},$=N=>{N.key==="Escape"&&g(!1)};return window.addEventListener("pointermove",l),window.addEventListener("pointerup",k),window.addEventListener("pointercancel",S),window.addEventListener("keydown",$),()=>{window.removeEventListener("pointermove",l),window.removeEventListener("pointerup",k),window.removeEventListener("pointercancel",S),window.removeEventListener("keydown",$)}},[Pe,a,t,b.config,Q,v,f]);let ve=L.useCallback(l=>{if(!T||l.target.closest("[data-event-id], button"))return;let g=st(Ht(l.clientX,l.currentTarget),t,b.config);if(!g)return;l.preventDefault();let k=x(`${g.dateOnly}T00:00:00`),S=new Date(k.getFullYear(),k.getMonth(),k.getDate(),0,g.minuteOfDay??0,0);T({start:j(S)})},[T,t,b.config]),F=L.useId(),A=`${F}-hint`,V=Ne,[se,re]=L.useState(0),[ee,X]=L.useState(0),[ye,he]=L.useState(null),[ie,Se]=L.useState(null),te=l=>`${F}-i-${l}`,Qe=l=>`${F}-e-${l}`;L.useEffect(()=>{se>b.items.length-1&&(re(Math.max(0,b.items.length-1)),he(null),Se(null))},[b.items.length,se]),L.useEffect(()=>{ee>t.length-1&&X(Math.max(0,t.length-1))},[t.length,ee]);let s=b.items[se],u=s?.kind==="row"?s.row:void 0,D=L.useCallback(l=>!!O||l.editable!==!1&&!!(p||v),[O,p,v]),M=L.useMemo(()=>(u?.blocks??[]).map(l=>l.event).filter(l=>D(l)),[u,D]);L.useEffect(()=>{let l=new Set(M.map(g=>g.id));ie&&!l.has(ie.eventId)?(Se(null),he(null)):!ie&&ye!==null&&!l.has(ye)&&he(null)},[M,ye,ie]);let I=ie?Qe(ie.eventId):ye?Qe(ye):te(se),w=L.useCallback(l=>me.find(g=>g.resource?.id===l)?.resource?.title??l,[me]),E=L.useCallback(l=>{let g=ie;if(!g)return;let k=a.find(C=>C.id===g.eventId);if(!k)return;let S=k.allDay===!0,$=g.dateOnly,N=g.minute,h=g.kind==="move"?g.resourceId:"";if(l==="ArrowLeft"||l==="ArrowRight")if(S)$=ke($,l==="ArrowLeft"?-1:1);else{let C=l==="ArrowLeft"?-V:V,B=st(Q($,N+C),t,b.config,V);if(!B)return;$=B.dateOnly,N=B.minuteOfDay??N}else if(g.kind==="move"&&(l==="ArrowUp"||l==="ArrowDown")){let C=me.findIndex(Ke=>Ke.resource?.id===h),B=l==="ArrowUp"?C-1:C+1;if(C===-1||B<0||B>=me.length)return;h=me[B].resource.id}else return;if(!($===g.dateOnly&&N===g.minute&&(g.kind!=="move"||h===g.resourceId)))if(g.kind==="move"){let C=S?Fe($,r):`${Fe($,r)} ${xn($,N,r)}`;R(z.movedTo(`${w(h)} \xB7 ${C}`)),Se({...g,dateOnly:$,minute:N,resourceId:h,moved:!0})}else{let C=$e(k,"end",$,N);R(z.resizedTo(`${be(C.start,r)} \u2013 ${be(C.end,r)}`)),Se({...g,dateOnly:$,minute:N,moved:!0})}},[ie,a,V,t,b.config,me,Q,w,z,r]),Me=L.useCallback(()=>{let l=ie;if(!l)return;if(!l.moved){he(l.eventId),Se(null);return}let g=a.find(k=>k.id===l.eventId);if(g&&g.editable!==!1&&l.kind==="move"&&p){let k=g.allDay===!0?null:l.minute;p(it(g,l.dateOnly,k,l.resourceId)),R(z.dropped(`${w(l.resourceId)} \xB7 ${g.allDay===!0?Fe(l.dateOnly,r):xn(l.dateOnly,l.minute,r)}`)),he(null)}else if(g&&g.editable!==!1&&l.kind==="resize"&&v){let k=$e(g,"end",l.dateOnly,l.minute);v(k),R(z.resized(`${be(k.start,r)} \u2013 ${be(k.end,r)}`)),he(l.eventId)}else he(l.eventId);Se(null)},[ie,a,p,v,w,z,r]),we=L.useCallback(l=>{let{key:g}=l,k=g==="Enter"||g===" "||g==="Spacebar",S=g==="ArrowUp"||g==="ArrowDown"||g==="ArrowLeft"||g==="ArrowRight",$=b.items.length-1;if(ie){if(S){l.preventDefault(),E(g);return}if(k){l.preventDefault(),Me();return}g==="Escape"&&(l.preventDefault(),Se(null),R(z.cancelled));return}if(ye){let N=M.findIndex(h=>h.id===ye);if(g==="ArrowRight"){l.preventDefault(),N>=0&&N<M.length-1&&he(M[N+1].id);return}if(g==="ArrowLeft"){l.preventDefault(),N>0?he(M[N-1].id):he(null);return}if(g==="ArrowUp"||g==="ArrowDown"){l.preventDefault(),he(null),re(h=>Math.min(Math.max(h+(g==="ArrowUp"?-1:1),0),$));return}if(k){l.preventDefault();let h=M.find(C=>C.id===ye);if(!h)return;h.editable!==!1&&p&&u?.resource?(Se({kind:"move",eventId:h.id,dateOnly:ge(h.start),minute:da(h.start),resourceId:u.resource.id,moved:!1}),R(z.grabbedMoveHint(h.title))):O&&O({id:h.id});return}if((g==="r"||g==="R")&&v){l.preventDefault();let h=M.find(C=>C.id===ye);h&&h.allDay!==!0&&h.editable!==!1&&(Se({kind:"resize",eventId:h.id,dateOnly:ge(h.end),minute:da(h.end),moved:!1}),R(z.grabbedResizeHint(h.title)));return}g==="Escape"&&(l.preventDefault(),he(null));return}if(g==="ArrowUp"||g==="ArrowDown"){l.preventDefault(),re(N=>Math.min(Math.max(N+(g==="ArrowUp"?-1:1),0),$));return}if(g==="ArrowLeft"||g==="ArrowRight"){l.preventDefault(),X(N=>Math.min(Math.max(N+(g==="ArrowLeft"?-1:1),0),Math.max(0,t.length-1)));return}if(g==="Home"||g==="End"){l.preventDefault(),X(g==="Home"?0:Math.max(0,t.length-1));return}if(k){if(s?.kind==="group"){l.preventDefault(),K(s.group.id);return}if(M.length>0){l.preventDefault(),he(M[0].id);return}if(u?.resource&&u.blocks.length===0&&f&&t.length>0){let N=t[Math.min(ee,t.length-1)],h=b.config.dayStartHour*60,C=Math.min(h+60,b.config.dayEndHour*60);C>h&&(l.preventDefault(),f(We({dateOnly:N,minuteOfDay:h,resourceId:u.resource.id},{dateOnly:N,minuteOfDay:C,resourceId:u.resource.id})),R(z.createHere(`${u.resource.title} \xB7 ${Fe(N,r)} ${xn(N,h,r)}`)))}}},[ie,ye,M,s,u,b.items.length,b.config,t,ee,p,v,O,f,E,Me,K,z,r]),Ye={...d??{}};return Be(ca,{children:[Be("div",{className:Ue("aethercal-calendar","aethercal-timeline",De.status==="dragging"&&"is-dragging",De.status==="resizing"&&"is-resizing",De.status==="selecting"&&"is-selecting"),role:"grid","aria-label":z.viewNames.timeline,"aria-describedby":A,"aria-activedescendant":I,tabIndex:0,"data-view":"timeline",style:Ye,onKeyDown:we,children:[Be("div",{className:"aethercal-tl-head",role:"row",children:[le("div",{className:"aethercal-tl-corner",role:"columnheader",children:z.timelineResources}),le("div",{className:"aethercal-tl-days",children:b.dayHeaders.map(l=>le("div",{role:"columnheader",className:Ue("aethercal-tl-dayhead",l.dateOnly===q&&"is-today"),"data-date":l.dateOnly,style:{left:_e(l.leftFraction),width:_e(l.widthFraction)},children:le("span",{children:Fe(l.dateOnly,r)})},l.dateOnly))})]}),le("div",{className:"aethercal-tl-body",role:"rowgroup",tabIndex:0,children:b.items.map((l,g)=>{let k=!ye&&!ie&&g===se;if(l.kind==="group"){let{group:C}=l;return le("div",{role:"row",className:Ue("aethercal-tl-group",C.collapsed&&"is-collapsed"),children:le("div",{className:"aethercal-tl-group-head",role:"rowheader",children:Be("button",{type:"button",id:te(g),className:Ue("aethercal-tl-group-toggle",k&&"is-active"),"aria-expanded":!C.collapsed,tabIndex:-1,onClick:()=>K(C.id),children:[le("span",{className:"aethercal-tl-caret","aria-hidden":"true",children:"\u25BE"}),le("span",{children:C.id})," ",le("span",{className:"aethercal-tl-group-count",children:z.timelineGroupCount(C.resourceCount)})]})})},`g:${C.id}`)}let{row:S}=l,$=ie?.kind==="move"&&S.resource?.id===ie.resourceId,N={"--ac-tl-lanes":S.laneCount},h=S.resource?.color?{"--ac-tl-row-accent":S.resource.color}:{};return Be("div",{role:"row",className:Ue("aethercal-tl-row",!S.resource&&"is-unassigned"),children:[Be("div",{id:te(g),role:"rowheader",className:Ue("aethercal-tl-rowhead",k&&"is-active"),style:h,children:[S.resource?.color?le("span",{className:"aethercal-tl-swatch","aria-hidden":"true"}):null,le("span",{className:"aethercal-tl-rowhead-title",children:S.resource?S.resource.title:z.timelineUnassigned})]}),Be("div",{role:"gridcell",className:Ue("aethercal-tl-track",$&&"is-drop-target"),"data-resource-id":S.resource?.id??"",style:N,onDragOver:fe&&S.resource?C=>C.preventDefault():void 0,onDrop:fe&&S.resource?Ie(S):void 0,onPointerDown:Le&&S.resource?Re(S):void 0,onContextMenu:T?ve:void 0,children:[b.ticks.map(C=>le("div",{className:Ue("aethercal-tl-line",C.isDayStart&&"is-day-start"),style:{left:_e(C.leftFraction)},"aria-hidden":"true"},`${C.dateOnly}-${C.hour}`)),xe&&xe.resourceId===S.resource?.id?le("div",{className:"aethercal-tl-select-band",style:{left:_e(xe.leftFraction),width:_e(xe.widthFraction)},"aria-hidden":"true"}):null,S.blocks.map(C=>{let{event:B}=C,Ke=B.editable!==!1,yt=U?.id===B.id?U:null,Rt=ie?.eventId===B.id||!ie&&ye===B.id&&u===S,Mt=C.allDay?z.allDay:be(yt?.start??B.start,r),ua={left:_e(C.leftFraction),width:_e(C.widthFraction),top:_e(C.lane/C.laneCount),height:_e(1/C.laneCount),...B.color?{"--ac-tl-event-accent":B.color}:{}};return Be("div",{id:Qe(B.id),className:Ue("aethercal-tl-event",C.allDay&&"is-allday",!Ke&&"is-locked",C.continuesBefore&&"continues-before",C.continuesAfter&&"continues-after",y.has(B.id)&&"is-pending",P.has(B.id)&&"is-rolledback",!!yt&&"is-resizing",Rt&&"is-active",ie?.eventId===B.id&&"is-grabbed"),...D(B)?{role:"button"}:{},draggable:Ke,"data-event-id":B.id,"data-lane":C.lane,"aria-label":`${Mt} ${B.title}`,title:B.title,style:ua,onDragStart:ct=>{if(W.current?.kind==="resize"){ct.preventDefault();return}ct.dataTransfer.setData("text/plain",B.id),ct.dataTransfer.effectAllowed="move",ue(B.id)},onDragEnd:oe,onClick:O?()=>O({id:B.id}):void 0,onContextMenu:T?ct=>{ct.preventDefault(),ct.stopPropagation(),T({id:B.id})}:void 0,children:[le("time",{className:"aethercal-tl-event-time",children:Mt})," ",le("span",{className:"aethercal-tl-event-title",children:B.title}),Ce&&Ke&&!C.allDay?Be(ca,{children:[le("div",{className:"aethercal-tl-resize-handle aethercal-tl-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:J(B,"start")}),le("div",{className:"aethercal-tl-resize-handle aethercal-tl-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:J(B,"end")})]}):null]},B.id)}),pe!==null?le("div",{className:"aethercal-tl-now",style:{left:_e(pe)},"aria-hidden":"true"}):null]})]},Xa(S))})})]}),le(pt,{id:A,text:z.timelineKeyboardHint}),le(mt,{message:ne})]})}import{jsx as vt,jsxs as Za}from"react/jsx-runtime";function Ja(e){if(e instanceof Date)return e;if(typeof e=="string"){let t=e.trim();if(t==="")return new Date;try{return x(t)}catch{return new Date}}return new Date}function ja(e){return e instanceof Date?e:typeof e=="string"?x(e):new Date}function Vt(e){let{view:t="month",events:n,resources:a,timelineDays:r,defaultCollapsedGroupIds:i,onToggleGroup:o,anchor:d,locale:c="en",theme:m,messages:p,firstDayOfWeek:v=1,maxEventsPerDay:f=3,weekdayLabels:O,formatMore:T,unavailableLabel:y,dayStartHour:P,dayEndHour:z,allDayLabel:ne,now:R,continuesLabel:_,formatEndsLabel:G,agendaEmptyLabel:Y,onEventDrop:b,onEventResize:ce,onRangeSelect:me,onEventClick:pe,onContextMenu:q,navigation:De=!1,navigationViews:ae=!0,onRangeChange:W,onViewChange:U,pendingIds:de,rolledBackIds:xe}=e;Ge.useEffect(()=>{dt()},[]);let Te=Ge.useMemo(()=>Ja(d),[d]),fe=Ge.useMemo(()=>hn(m),[m]),Ce=Ge.useMemo(()=>{let ve={...ne!==void 0?{allDay:ne}:{},..._!==void 0?{continues:_}:{},...G!==void 0?{endsAt:G}:{},...Y!==void 0?{noEvents:Y}:{},...y!==void 0?{unavailable:y}:{},...T!==void 0?{more:T}:{},...p};return lt(c,ve)},[c,ne,_,G,Y,y,T,p]),[Le,Q]=Ge.useState(()=>new Date);Ge.useEffect(()=>{if(R!==void 0||t!=="week"&&t!=="day"&&t!=="timeline")return;let ve=setInterval(()=>Q(new Date),6e4);return()=>clearInterval(ve)},[R,t]);let K=Ge.useMemo(()=>R!==void 0?ja(R):Le,[R,Le]),Ie=Number.isInteger(v)&&v>=0&&v<=6?v:1,ue=Number.isInteger(f)&&f>=0?f:3,oe=O&&O.length===7?O:void 0,J=Je(r),Re=Ge.useMemo(()=>({...P!==void 0?{dayStartHour:P}:{},...z!==void 0?{dayEndHour:z}:{}}),[P,z]),Pe=(()=>{if(t==="list")return vt(Gn,{events:n??[],locale:c,messages:Ce,themeVars:fe});if(t==="month")return vt(Yn,{events:n??[],anchor:Te,locale:c,messages:Ce,themeVars:fe,firstDayOfWeek:Ie,maxEventsPerDay:ue,...oe?{weekdayLabels:oe}:{},...b?{onEventDrop:b}:{},...me?{onRangeSelect:me}:{},...pe?{onEventClick:pe}:{},...q?{onContextMenu:q}:{},...de?{pendingIds:de}:{},...xe?{rolledBackIds:xe}:{}});if(t==="timeline")return vt(Tn,{days:Kt(Te,J),resources:a??[],events:n??[],locale:c,messages:Ce,themeVars:fe,config:Re,now:K,...i?{defaultCollapsedGroupIds:i}:{},...o?{onToggleGroup:o}:{},...b?{onEventDrop:b}:{},...ce?{onEventResize:ce}:{},...me?{onRangeSelect:me}:{},...pe?{onEventClick:pe}:{},...q?{onContextMenu:q}:{},...de?{pendingIds:de}:{},...xe?{rolledBackIds:xe}:{}});if(t==="week"||t==="day"){let ve=t==="week"?Bt(Te,Ie):[ge(j(Te))];return vt(wn,{view:t,days:ve,events:n??[],locale:c,messages:Ce,themeVars:fe,config:Re,now:K,...b?{onEventDrop:b}:{},...ce?{onEventResize:ce}:{},...me?{onRangeSelect:me}:{},...pe?{onEventClick:pe}:{},...q?{onContextMenu:q}:{},...de?{pendingIds:de}:{},...xe?{rolledBackIds:xe}:{}})}return vt("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,style:fe,children:Ce.unavailable})})();return De?Za("div",{className:"aethercal-calendar-shell",style:fe,children:[vt(gn,{view:t,anchor:Te,now:K,locale:c,firstDayOfWeek:Ie,timelineDays:J,messages:Ce,showViews:ae,...W?{onRangeChange:W}:{},...U?{onViewChange:U}:{}}),Pe]}):Pe}var qa=Vt;import*as Oe from"react";function Qa(){return typeof crypto<"u"&&typeof crypto.randomUUID=="function"?crypto.randomUUID():`cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}var er=8e3,tr=900;function Rn(e){let{events:t,mutate:n,timeoutMs:a=er,rollbackFlashMs:r=tr,generateId:i=Qa}=e,[o,d]=Oe.useReducer(sn,on),c=Oe.useRef(t);c.current=t;let m=Oe.useRef(!0),p=Oe.useRef(new Map);Oe.useEffect(()=>{m.current=!0;let O=p.current;return()=>{m.current=!1;for(let T of O.values())clearTimeout(T);O.clear()}},[]),Oe.useEffect(()=>{for(let O of dn(t,o)){let T=o.overrides[O];d({type:"CLEAR",id:O,...T?{clientMutationId:T.clientMutationId}:{}})}},[t,o]);let v=Oe.useCallback((O,T)=>{let y=i(),P=c.current.find(b=>b.id===T.id),z=p.current,ne=b=>{let ce=z.get(b);ce!==void 0&&(clearTimeout(ce),z.delete(b))},R=()=>{z.set(`fl:${y}`,setTimeout(()=>{z.delete(`fl:${y}`),m.current&&d({type:"CLEAR",id:T.id,clientMutationId:y})},r))};d({type:"SUBMIT",id:T.id,clientMutationId:y,start:T.start,end:T.end,...P?.revision!==void 0?{baseRevision:P.revision}:{},..."resourceId"in T&&T.resourceId!==void 0?{resourceId:T.resourceId}:{}}),z.set(`to:${y}`,setTimeout(()=>{z.delete(`to:${y}`),m.current&&(d({type:"TIMEOUT",id:T.id,clientMutationId:y}),R())},a));let _=()=>{ne(`to:${y}`),m.current&&(d({type:"REJECT",id:T.id,clientMutationId:y}),R())},G={kind:O,clientMutationId:y,payload:{...T,client_mutation_id:y}},Y;try{Y=n(G)}catch(b){Y=Promise.reject(b instanceof Error?b:new Error(String(b)))}Y.then(b=>{if(b.id!==T.id){_();return}ne(`to:${y}`),m.current&&d({type:"RESOLVE",id:b.id,clientMutationId:y,start:b.start,end:b.end,revision:b.revision,...b.resourceId!==void 0?{resourceId:b.resourceId}:{}})}).catch(_)},[n,a,r,i]),f=Oe.useMemo(()=>ln(t,o),[t,o]);return{events:f.events,pendingIds:f.pendingIds,rolledBackIds:f.rolledBackIds,submit:v}}import{jsx as ar}from"react/jsx-runtime";function nr({events:e,mutate:t,timeoutMs:n,rollbackFlashMs:a,generateId:r,...i}){let{events:o,pendingIds:d,rolledBackIds:c,submit:m}=Rn({events:e,mutate:t,...n!==void 0?{timeoutMs:n}:{},...a!==void 0?{rollbackFlashMs:a}:{},...r?{generateId:r}:{}});return ar(Vt,{...i,events:o,pendingIds:d,rolledBackIds:c,onEventDrop:p=>m("drop",p),onEventResize:p=>m("resize",p)})}export{Vt as AetherCalendar,Zn as CALENDAR_CSS,gn as CalendarNav,mn as DEFAULT_LOCALE_MESSAGES,nr as OptimisticCalendar,zt as PRESETS,Wn as PRESET_NAMES,sa as TIMELINE_CSS,na as TIME_GRID_CSS,wn as TimeGridView,Tn as TimelineView,qa as default,fn as defaultBaseTokenCss,vn as defaultTimeGridTokenCss,yn as defaultTimelineTokenCss,dt as ensureCalendarStyles,bn as ensureTimeGridStyles,En as ensureTimelineStyles,wt as getVisibleRange,Jn as isThemePreset,x as parseLocalDateTime,lt as resolveMessages,hn as resolveThemeVars,Et as stepAnchor,Rn as useOptimisticEvents};
