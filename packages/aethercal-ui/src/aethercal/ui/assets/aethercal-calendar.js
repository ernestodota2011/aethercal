function Ae(e){return String(e).padStart(2,"0")}function I(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,n,a,r,i,o,u]=t,l=Number(n),c=Number(a),p=Number(r),v=Number(i??"0"),f=Number(o??"0"),O=Number(u??"0");if(c<1||c>12||p<1||p>31||v>23||f>59||O>59)throw new Error(`out-of-range ISO datetime: ${e}`);let x=new Date(l,c-1,p,v,f,O);if(x.getFullYear()!==l||x.getMonth()!==c-1||x.getDate()!==p)throw new Error(`nonexistent calendar date: ${e}`);return x}function j(e){return`${e.getFullYear()}-${Ae(e.getMonth()+1)}-${Ae(e.getDate())}T${Ae(e.getHours())}:${Ae(e.getMinutes())}:${Ae(e.getSeconds())}`}function pe(e){let t=I(e);return`${t.getFullYear()}-${Ae(t.getMonth()+1)}-${Ae(t.getDate())}`}function Mn(e){return`${e.getFullYear()}-${Ae(e.getMonth()+1)}-${Ae(e.getDate())}`}function et(e){let t=I(e.start),n=I(e.end),a=new Date(t.getFullYear(),t.getMonth(),t.getDate()),r=a;if(n.getTime()>t.getTime()){let i=new Date(n.getTime()-1),o=new Date(i.getFullYear(),i.getMonth(),i.getDate());o.getTime()>a.getTime()&&(r=o)}return{startKey:Mn(a),lastKey:Mn(r)}}function ga(e,t){return(e.getDay()-t+7)%7}function tt(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate());return n.setDate(n.getDate()-ga(n,t)),n}function Ut(e,t){return Array.from({length:t},(n,a)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+a);return`${r.getFullYear()}-${Ae(r.getMonth()+1)}-${Ae(r.getDate())}`})}function Bt(e,t=1){return Ut(tt(e,t),7)}function Yt(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),1);return Ut(tt(n,t),42)}function Kt(e,t){return Ut(new Date(e.getFullYear(),e.getMonth(),e.getDate()),t)}function ke(e,t){let n=I(`${pe(e)}T00:00:00`),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()+t);return`${a.getFullYear()}-${Ae(a.getMonth()+1)}-${Ae(a.getDate())}`}function Wt(e,t){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()),a=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((a.getTime()-n.getTime())/864e5)}function ut(e,t){let n=I(e.start),a=I(e.end),r=I(t),i=Wt(n,r),o=new Date(n.getFullYear(),n.getMonth(),n.getDate()+i,n.getHours(),n.getMinutes(),n.getSeconds()),u=new Date(a.getFullYear(),a.getMonth(),a.getDate()+i,a.getHours(),a.getMinutes(),a.getSeconds()),l={id:e.id,start:j(o),end:j(u)};return e.revision!==void 0&&(l.revision=e.revision),l}var ma=370;function Cn(e){return String(e).padStart(2,"0")}function In(e){return`${e.getFullYear()}-${Cn(e.getMonth()+1)}-${Cn(e.getDate())}`}function pa(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function fa(e){let{startKey:t,lastKey:n}=et(e),a=[],r=I(t);for(let i=0;i<ma&&In(r)<=n;i+=1)a.push(In(r)),r=pa(r,1);return{keys:a,startKey:t,lastKey:n}}function Xt(e){let t=new Map;return e.forEach((n,a)=>{let{keys:r,startKey:i,lastKey:o}=fa(n),u=I(n.start).getTime(),l=I(n.end).getTime();for(let c of r){let p={entry:{event:n,isContinuation:c!==i,continuesAfter:c!==o},startMs:u,endMs:l,index:a},v=t.get(c);v?v.push(p):t.set(c,[p])}}),[...t.keys()].sort().map(n=>{let a=t.get(n);return a.sort((r,i)=>r.startMs-i.startMs||r.endMs-i.endMs||r.index-i.index),{date:n,entries:a.map(r=>r.entry)}})}function nt(e,t,n,a){let r=n*a;if(r<=0)return e;let i=Math.min(Math.max(e,0),r-1),o=i-i%a,u=Math.min(o+a-1,r-1);switch(t){case"ArrowLeft":return i>o?i-1:i;case"ArrowRight":return i<u?i+1:i;case"ArrowUp":{let l=i-a;return l>=0?l:i}case"ArrowDown":{let l=i+a;return l<r?l:i}case"Home":return o;case"End":return u;default:return i}}var at=60,Ne=15;function jt(e,t,n){return Math.min(n,Math.max(t,e))}function Ct(e,t){let n=I(`${e}T00:00:00`);return new Date(n.getFullYear(),n.getMonth(),n.getDate(),0,t,0)}function qt(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate(),e.getHours(),e.getMinutes()+t,e.getSeconds())}function It(e,t){return t==null||(e.resourceId=t),e}function rt(e,t,n=Ne){let a=t.dayStartHour*at,r=t.dayEndHour*at,i=a+jt(e,0,1)*t.windowMinutes,o=n>0?n:Ne,u=a+Math.round((i-a)/o)*o;return jt(u,a,r)}function kt(e,t){return jt(e,t.dayStartHour*at,t.dayEndHour*at)}var Jt=24*at;function Zt(e,t,n,a){let r=t+n,i=e;for(;r<0;)r+=Jt,i=ke(i,-1);for(;r>Jt;)r-=Jt,i=ke(i,1);return{dateOnly:i,minuteOfDay:kt(r,a)}}function it(e,t,n,a){if(n===null)return It(ut(e,t),a);let r=I(e.start),i=I(e.end),o=Ct(t,n),u=Wt(r,i),l=r.getHours()*at+r.getMinutes(),p=i.getHours()*at+i.getMinutes()-l,v=new Date(o.getFullYear(),o.getMonth(),o.getDate()+u,o.getHours(),o.getMinutes()+p,0),f={id:e.id,start:j(o),end:j(v)};return e.revision!==void 0&&(f.revision=e.revision),It(f,a)}function He(e,t,n,a,r={}){let i=r.minDurationMinutes??Ne,o=I(e.start),u=I(e.end),l=Ct(n,a),c=o,p=u;if(t==="end"){let f=qt(o,i);p=l.getTime()>=f.getTime()?l:f}else{let f=qt(u,-i);c=l.getTime()<=f.getTime()?l:f}let v={id:e.id,start:j(c),end:j(p)};return e.revision!==void 0&&(v.revision=e.revision),v}function We(e,t,n={}){let a=n.minDurationMinutes??Ne;if(e.minuteOfDay===null||t.minuteOfDay===null){let[p,v]=e.dateOnly<=t.dateOnly?[e.dateOnly,t.dateOnly]:[t.dateOnly,e.dateOnly],f=I(`${p}T00:00:00`),O=I(`${v}T00:00:00`),x=new Date(O.getFullYear(),O.getMonth(),O.getDate()+1),b={start:j(f),end:j(x),allDay:!0};return It(b,e.resourceId)}let i=Ct(e.dateOnly,e.minuteOfDay??0),o=Ct(t.dateOnly,t.minuteOfDay??0),u=i.getTime()<=o.getTime()?i:o,l=i.getTime()<=o.getTime()?o:i;l.getTime()===u.getTime()&&(l=qt(u,a));let c={start:j(u),end:j(l),allDay:!1};return It(c,e.resourceId)}var Ve=60,va=24*Ve,ha=864e5;function St(e,t,n){return Math.min(n,Math.max(t,e))}function yt(e={}){let t=e.dayStartHour,n=e.dayEndHour,a=Number.isFinite(t)&&t!==void 0?St(Math.trunc(t),0,23):0,r=Number.isFinite(n)&&n!==void 0?St(Math.trunc(n),1,24):24,[i,o]=r>a?[a,r]:[0,24];return{dayStartHour:i,dayEndHour:o,windowMinutes:(o-i)*Ve}}function kn(e){let t=[],n=[];for(let a of e)a.allDay===!0?t.push(a):n.push(a);return{allDay:t,timed:n}}function ot(e,t){let n=I(e),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()),r=Math.round((a.getTime()-t.getTime())/ha),i=n.getHours()*Ve+n.getMinutes()+n.getSeconds()/60;return r*va+i}function At(e,t){let n=e.map(l=>{let[c,p]=t(l);return{item:l,start:c,end:p}});n.sort((l,c)=>l.start!==c.start?l.start-c.start:c.end-l.end);let a=[],r=[],i=[],o=Number.NEGATIVE_INFINITY,u=()=>{let l=r.length;for(let c of i)a[c].laneCount=l;r=[],i=[],o=Number.NEGATIVE_INFINITY};for(let l of n){i.length>0&&l.start>=o&&u();let c=r.findIndex(p=>!(p.start<l.end&&l.start<p.end));c===-1?(c=r.length,r.push({start:l.start,end:l.end})):r[c]={start:l.start,end:l.end},i.push(a.length),a.push({item:l.item,lane:c,laneCount:1}),o=Math.max(o,l.end)}return u(),a}function Sn(e){return At(e,t=>[I(t.start).getTime(),I(t.end).getTime()])}function bt(e,t,n){let a=I(`${t}T00:00:00`),r=n.dayStartHour*Ve,i=n.dayEndHour*Ve,o=e.filter(u=>{let l=ot(u.start,a);return!(ot(u.end,a)<=r||l>=i)});return Sn(o).map(({item:u,lane:l,laneCount:c})=>{let p=ot(u.start,a),v=ot(u.end,a),f=St(p,r,i),O=St(v,f,i),{startKey:x,lastKey:b}=et(u);return{event:u,lane:l,laneCount:c,topFraction:(f-r)/n.windowMinutes,heightFraction:(O-f)/n.windowMinutes,isContinuation:t!==x,continuesAfter:t!==b}})}function ya(e){let t=[];for(let n=e.dayStartHour;n<e.dayEndHour;n+=1)t.push({hour:n,topFraction:(n-e.dayStartHour)*Ve/e.windowMinutes});return t}function Qt(e,t,n={}){let a="windowMinutes"in n?n:yt(n),{allDay:r,timed:i}=kn(t),o=i.map(l=>({event:l,startTs:I(l.start).getTime(),endTs:I(l.end).getTime()}));return{columns:e.map(l=>{let c=I(`${l}T00:00:00`),p=c.getTime(),v=new Date(c.getFullYear(),c.getMonth(),c.getDate()+1).getTime(),f=o.filter(x=>x.startTs>=v?!1:x.endTs>p?!0:x.startTs===x.endTs&&x.startTs>=p).map(x=>x.event),O=r.filter(x=>{let{startKey:b,lastKey:P}=et(x);return b<=l&&l<=P});return{dateOnly:l,allDay:O,timed:bt(f,l,a)}}),hourMarks:ya(a),config:a}}function en(e,t={}){let n="windowMinutes"in t?t:yt(t),a=e.getHours()*Ve+e.getMinutes()+e.getSeconds()/60,r=n.dayStartHour*Ve,i=n.dayEndHour*Ve;return a<r||a>=i?null:(a-r)/n.windowMinutes}var Xe=60,Ot=7,An=1,On=31;function Dt(e,t,n){return Math.min(n,Math.max(t,e))}function Je(e){return e===void 0||!Number.isFinite(e)?Ot:Dt(Math.trunc(e),An,On)}function tn(e){return"windowMinutes"in e?e:yt(e)}function ba(e){if(e.allDay!==!0)return{start:e.start,end:e.end};let{startKey:t,lastKey:n}=et(e);return{start:`${t}T00:00:00`,end:`${ke(n,1)}T00:00:00`}}function Da(e,t,n){let a=n.dayStartHour*Xe,r=n.dayEndHour*Xe,i=[];return t.forEach((o,u)=>{let l=I(`${o}T00:00:00`),c=ot(e.start,l),p=ot(e.end,l);if(p<=a||c>=r)return;let v=Dt(c,a,r),f=Dt(p,v,r),O=u*n.windowMinutes;i.push({startMin:O+(v-a),endMin:O+(f-a),clippedStart:c<a,clippedEnd:p>r})}),i}function wa(e){let t=[];for(let n of e){let a=t[t.length-1];a&&a.endMin===n.startMin?(a.endMin=n.endMin,a.clippedEnd=n.clippedEnd):t.push({...n})}return t}function Ea(e,t,n){let a=t.length*n.windowMinutes;if(a<=0)return[];let r=[];for(let o of e){let u=wa(Da(o,t,n));u.length>0&&r.push({item:o,runs:u})}return At(r,o=>[o.runs[0].startMin,o.runs[o.runs.length-1].endMin]).flatMap(({item:o,lane:u,laneCount:l})=>o.runs.map(c=>({event:o.item.event,lane:u,laneCount:l,leftFraction:c.startMin/a,widthFraction:(c.endMin-c.startMin)/a,allDay:o.item.event.allDay===!0,continuesBefore:c.clippedStart,continuesAfter:c.clippedEnd})))}function nn(e,t,n,a={}){let r=tn(a),i=new Set(a.collapsedGroupIds??[]),o=[],u=new Set;for(let T of e)u.has(T.id)||(u.add(T.id),o.push(T));let l=[],c=new Map;for(let T of o){let _=T.groupId?T.groupId:void 0;if(_===void 0){l.push({kind:"solo",resource:T});continue}let z=c.get(_);z?z.push(T):(c.set(_,[T]),l.push({kind:"group",id:_}))}let p=new Map,v=[];for(let T of t){let _={event:T,...ba(T)},z=T.resourceId;if(z!==void 0&&u.has(z)){let Y=p.get(z);Y?Y.push(_):p.set(z,[_])}else v.push(_)}let f=(T,_,z)=>{let Y=Ea(z,n,r);return{resource:T,groupId:_,blocks:Y,laneCount:Y.reduce((h,ue)=>Math.max(h,ue.laneCount),1)}},O=[];for(let T of l){if(T.kind==="solo"){O.push({kind:"row",row:f(T.resource,null,p.get(T.resource.id)??[])});continue}let _=c.get(T.id)??[],z=i.has(T.id);if(O.push({kind:"group",group:{id:T.id,collapsed:z,resourceCount:_.length}}),!z)for(let Y of _)O.push({kind:"row",row:f(Y,T.id,p.get(Y.id)??[])})}let x=f(null,null,v);x.blocks.length>0&&O.push({kind:"row",row:x});let b=n.length,P=n.map((T,_)=>({dateOnly:T,leftFraction:b>0?_/b:0,widthFraction:b>0?1/b:0})),L=b*r.windowMinutes,ne=[];return L>0&&n.forEach((T,_)=>{let z=_*r.windowMinutes;for(let Y=r.dayStartHour;Y<r.dayEndHour;Y+=1){let h=(Y-r.dayStartHour)*Xe;ne.push({dateOnly:T,hour:Y,leftFraction:(z+h)/L,isDayStart:Y===r.dayStartHour})}}),{days:[...n],items:O,dayHeaders:P,ticks:ne,config:r}}function st(e,t,n={},a=Ne){let r=tn(n);if(t.length===0||r.windowMinutes<=0)return null;let i=t.length*r.windowMinutes,o=Dt(e,0,1)*i,u=Math.min(Math.floor(o/r.windowMinutes),t.length-1),l=o-u*r.windowMinutes,c=r.dayStartHour*Xe,p=r.dayEndHour*Xe,v=a>0?a:Ne,f=c+Math.round(l/v)*v;return{dateOnly:t[u],minuteOfDay:Dt(f,c,p)}}function an(e,t,n={}){let a=tn(n),r=t.indexOf(pe(j(e)));if(r===-1)return null;let i=e.getHours()*Xe+e.getMinutes()+e.getSeconds()/60,o=a.dayStartHour*Xe,u=a.dayEndHour*Xe;if(i<o||i>=u)return null;let l=t.length*a.windowMinutes;return l<=0?null:(r*a.windowMinutes+(i-o))/l}var xa=1;function wt(e,t,n=xa,a){let r=t.getFullYear(),i=t.getMonth(),o=t.getDate(),u,l;switch(e){case"week":{u=tt(t,n),l=new Date(u.getFullYear(),u.getMonth(),u.getDate()+7);break}case"day":{u=new Date(r,i,o),l=new Date(r,i,o+1);break}case"timeline":{u=new Date(r,i,o),l=new Date(r,i,o+Je(a));break}default:{u=new Date(r,i,1),l=new Date(r,i+1,1);break}}return{view:e,from:j(u),to:j(l)}}function Et(e,t,n,a){let r=e.getFullYear(),i=e.getMonth(),o=e.getDate();switch(t){case"week":return new Date(r,i,o+7*n);case"day":return new Date(r,i,o+n);case"timeline":return new Date(r,i,o+Je(a)*n);default:return new Date(r,i+n,1)}}var Lt={status:"idle"};function Pt(e){return e.status==="dragging"}function rn(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return Lt}}var gt={status:"idle"};function xt(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"RESIZE_START":return{status:"resizing",eventId:t.eventId,edge:t.edge};case"SELECT_START":return{status:"selecting",anchor:t.point,current:t.point};case"SELECT_MOVE":return e.status!=="selecting"?e:{status:"selecting",anchor:e.anchor,current:t.point};case"COMMIT":case"CANCEL":return gt}}var on={overrides:{},appliedRevision:{}};function Ta(e,t){let n={...e};return delete n[t],n}function sn(e,t){switch(t.type){case"SUBMIT":{let n=t.baseRevision??Number.NEGATIVE_INFINITY,a=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;return{overrides:{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"pending",start:t.start,end:t.end,...t.baseRevision!==void 0?{revision:t.baseRevision}:{},...t.resourceId!==void 0?{resourceId:t.resourceId}:{}}},appliedRevision:{...e.appliedRevision,[t.id]:Math.max(a,n)}}}case"RESOLVE":{let n=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;if(t.revision<=n)return e;let a=e.overrides[t.id],r=a!==void 0&&a.clientMutationId===t.clientMutationId&&a.status==="pending",i=t.resourceId??a?.resourceId;return{overrides:r?{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"committed",start:t.start,end:t.end,revision:t.revision,...i!==void 0?{resourceId:i}:{}}}:e.overrides,appliedRevision:{...e.appliedRevision,[t.id]:t.revision}}}case"REJECT":case"TIMEOUT":{let n=e.overrides[t.id];return!n||n.clientMutationId!==t.clientMutationId||n.status!=="pending"?e:{...e,overrides:{...e.overrides,[t.id]:{...n,status:"rolledback"}}}}case"CLEAR":{let n=e.overrides[t.id];return!n||t.clientMutationId&&n.clientMutationId!==t.clientMutationId?e:{...e,overrides:Ta(e.overrides,t.id)}}}}function ln(e,t){let n=new Set,a=new Set,r=o=>o.resourceId!==void 0?{resourceId:o.resourceId}:void 0;return{events:e.map(o=>{let u=t.overrides[o.id];return u?u.status==="pending"?(n.add(o.id),{...o,start:u.start,end:u.end,...r(u)}):u.status==="rolledback"?(a.add(o.id),o):o.revision!==void 0&&u.revision!==void 0&&o.revision>=u.revision?o:{...o,start:u.start,end:u.end,...u.revision!==void 0?{revision:u.revision}:{},...r(u)}:o}),pendingIds:n,rolledBackIds:a}}function dn(e,t){let n=new Map(e.map(r=>[r.id,r])),a=[];for(let[r,i]of Object.entries(t.overrides)){if(i.status!=="committed")continue;let o=n.get(r);o&&o.revision!==void 0&&i.revision!==void 0&&o.revision>=i.revision&&a.push(r)}return a}import*as Ge from"react";import*as Nt from"react";var cn=new Date(2023,0,1);function Pn(e,t){let n=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(a,r)=>{let i=(t+r)%7,o=new Date(cn.getFullYear(),cn.getMonth(),cn.getDate()+i);return n.format(o)})}function un(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function Ln(e,t,n){let a=new Intl.DateTimeFormat(n,{month:"short",day:"numeric"}).format(e),r=new Intl.DateTimeFormat(n,{month:"short",day:"numeric",year:"numeric"}).format(t);return`${a} \u2013 ${r}`}function Nn(e,t,n,a,r=Ot){if(e==="day")return new Intl.DateTimeFormat(n,{dateStyle:"full"}).format(t);if(e==="week"){let i=tt(t,a),o=new Date(i.getFullYear(),i.getMonth(),i.getDate()+6);return Ln(i,o,n)}if(e==="timeline"){let i=Je(r),o=new Date(t.getFullYear(),t.getMonth(),t.getDate()),u=new Date(o.getFullYear(),o.getMonth(),o.getDate()+i-1);return i===1?new Intl.DateTimeFormat(n,{dateStyle:"full"}).format(o):Ln(o,u,n)}return un(t,n)}function Tt(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(I(e))}function be(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(I(e))}function Fn(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(I(e))}import{jsx as je,jsxs as zn}from"react/jsx-runtime";function Ra(...e){return e.filter(Boolean).join(" ")}function Ma(e,t,n){let{event:a,isContinuation:r,continuesAfter:i}=e;return a.allDay===!0?n.allDay:r?i?n.continues:n.endsAt(be(a.end,t)):be(a.start,t)}function Ca({entry:e,locale:t,messages:n}){let{event:a,isContinuation:r,continuesAfter:i}=e,o=Ma(e,t,n),u=a.color?{"--ac-event-accent":a.color}:void 0;return zn("li",{className:Ra("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":a.id,"aria-label":`${o} ${a.title}`,style:u,...a.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...i?{"data-continues-after":""}:{},children:[je("span",{className:"aethercal-agenda-event-time",children:o}),je("span",{className:"aethercal-agenda-event-title",children:a.title})]})}function Gn({events:e,locale:t,messages:n,themeVars:a}){let r=Nt.useMemo(()=>Xt(e),[e]),i=Nt.useId();return r.length===0?je("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:je("p",{className:"aethercal-agenda-empty",children:n.noEvents})}):je("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:r.map(o=>{let u=`${i}-${o.date}`;return zn("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":u,"data-date":o.date,children:[je("div",{className:"aethercal-agenda-day-title",id:u,children:Fn(o.date,t)}),je("ul",{className:"aethercal-agenda-day-events",role:"list",children:o.entries.map((l,c)=>je(Ca,{entry:l,locale:t,messages:n},`${l.event.id}-${c}`))})]},o.date)})})}import{jsx as qe,jsxs as _n}from"react/jsx-runtime";var Ia=["month","week","day","list","timeline"];function gn({view:e,anchor:t,now:n,locale:a,firstDayOfWeek:r,timelineDays:i,messages:o,showViews:u=!0,onRangeChange:l,onViewChange:c}){let p=f=>{l?.(wt(e,f,r,i))},v=Nn(e,t,a,r,i);return _n("div",{className:"aethercal-nav",role:"toolbar","aria-label":o.navToolbar,children:[_n("div",{className:"aethercal-nav-group",children:[qe("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":o.navPrevious,onClick:()=>p(Et(t,e,-1)),children:qe("span",{"aria-hidden":"true",children:"\u2039"})}),qe("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-today",onClick:()=>p(n),children:o.navToday}),qe("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":o.navNext,onClick:()=>p(Et(t,e,1)),children:qe("span",{"aria-hidden":"true",children:"\u203A"})})]}),qe("span",{className:"aethercal-nav-title","aria-live":"polite",children:v}),u?qe("div",{className:"aethercal-nav-views",children:Ia.map(f=>qe("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-view","aria-pressed":f===e,onClick:()=>c?.(wt(f,t,r,i)),children:o.viewNames[f]},f))}):null]})}var ka={allDay:"All day",continues:"Continues",endsAt:e=>`ends ${e}`,more:e=>`+${e} more`,noEvents:"No events",unavailable:"This view is not available yet.",keyboardHint:"Use the arrow keys to move between days. Press Enter on an event to grab it, the arrow keys to move or resize it, Enter to drop, and Escape to cancel.",grabbedMoveHint:e=>`Grabbed ${e}. Use the arrow keys to move it, Enter to drop, Escape to cancel.`,grabbedResizeHint:e=>`Resizing ${e}. Use the up and down arrow keys to change its duration, Enter to confirm, Escape to cancel.`,movedTo:e=>`Moved to ${e}`,resizedTo:e=>`Duration ${e}`,dropped:e=>`Dropped on ${e}`,resized:e=>`Duration set to ${e}`,createHere:e=>`Create an event on ${e}`,cancelled:"Cancelled",navToolbar:"Calendar navigation",navPrevious:"Previous",navNext:"Next",navToday:"Today",viewNames:{month:"Month",week:"Week",day:"Day",list:"Agenda",timeline:"Timeline"},timelineResources:"Resources",timelineUnassigned:"Unassigned",timelineEmpty:"No resources to show",timelineGroupCount:e=>e===1?"1 resource":`${e} resources`,groupExpanded:e=>`${e} expanded`,groupCollapsed:e=>`${e} collapsed`,timelineKeyboardHint:"Use the up and down arrow keys to move between resources and the left and right arrow keys to move between days. Press Enter on a group to expand or collapse it, or on an event to grab it; then use the left and right arrow keys to change its time, the up and down arrow keys to move it to another resource, Enter to drop it, and Escape to cancel."},Sa={allDay:"Todo el d\xEDa",continues:"Contin\xFAa",endsAt:e=>`termina ${e}`,more:e=>`+${e} m\xE1s`,noEvents:"Sin eventos",unavailable:"Esta vista a\xFAn no est\xE1 disponible.",keyboardHint:"Usa las flechas para moverte entre los d\xEDas. Pulsa Enter sobre un evento para agarrarlo, las flechas para moverlo o cambiar su duraci\xF3n, Enter para soltarlo y Escape para cancelar.",grabbedMoveHint:e=>`Agarraste el evento ${e}. Usa las flechas para moverlo, Enter para soltarlo y Escape para cancelar.`,grabbedResizeHint:e=>`Est\xE1s cambiando la duraci\xF3n de ${e}. Usa las flechas hacia arriba y abajo para ajustarla, Enter para confirmar y Escape para cancelar.`,movedTo:e=>`Movido a ${e}`,resizedTo:e=>`Duraci\xF3n ${e}`,dropped:e=>`Soltado en ${e}`,resized:e=>`Duraci\xF3n establecida en ${e}`,createHere:e=>`Crear un evento en ${e}`,cancelled:"Cancelado",navToolbar:"Navegaci\xF3n del calendario",navPrevious:"Anterior",navNext:"Siguiente",navToday:"Hoy",viewNames:{month:"Mes",week:"Semana",day:"D\xEDa",list:"Agenda",timeline:"Cronograma"},timelineResources:"Recursos",timelineUnassigned:"Sin asignar",timelineEmpty:"No hay recursos para mostrar",timelineGroupCount:e=>e===1?"1 recurso":`${e} recursos`,groupExpanded:e=>`${e} desplegado`,groupCollapsed:e=>`${e} plegado`,timelineKeyboardHint:"Usa las flechas hacia arriba y abajo para moverte entre los recursos, y las flechas izquierda y derecha para moverte entre los d\xEDas. Pulsa Enter sobre un grupo para desplegarlo o plegarlo, o sobre un evento para agarrarlo; luego usa las flechas izquierda y derecha para cambiar su hora, las flechas hacia arriba y abajo para moverlo a otro recurso, Enter para soltarlo y Escape para cancelar."},mn={en:ka,es:Sa};function Aa(e){return e.toLowerCase().split("-")[0]??""}function lt(e,t,n=mn){let a=e.toLowerCase(),r=n[a]??n[Aa(e)]??n.en??mn.en;return t?{...r,...t}:r}import*as Z from"react";import{jsx as $n}from"react/jsx-runtime";function mt({message:e}){return $n("div",{className:"aethercal-sr-only","aria-live":"polite","aria-atomic":"true",children:e})}function pt({id:e,text:t}){return $n("div",{id:e,className:"aethercal-sr-only",children:t})}import{jsx as Hn,jsxs as La}from"react/jsx-runtime";function Oa(...e){return e.filter(Boolean).join(" ")}function Ft({event:e,timeLabel:t,onDragStart:n,onDragEnd:a,canDrag:r=!0,isPending:i,isRolledBack:o,onClick:u,onContextMenu:l,id:c,interactive:p,isActive:v,isGrabbed:f}){let O=e.editable!==!1,x=O&&r,b=e.color?{"--ac-event-accent":e.color}:void 0,P=t?`${t} ${e.title}`:e.title;return La("div",{className:Oa("aethercal-event",!O&&"is-locked",i&&"is-pending",o&&"is-rolledback",v&&"is-active",f&&"is-grabbed"),...c?{id:c}:{},...p?{role:"button"}:{},draggable:x,"data-event-id":e.id,"aria-label":P,title:e.title,style:b,onDragStart:L=>{if(!x){L.preventDefault();return}L.dataTransfer.setData("text/plain",e.id),L.dataTransfer.effectAllowed="move",n(e.id)},onDragEnd:a,onClick:u,onContextMenu:l?L=>{L.preventDefault(),L.stopPropagation(),l()}:void 0,children:[t?Hn("time",{className:"aethercal-event-time",children:t}):null,t?" ":null,Hn("span",{className:"aethercal-event-title",children:e.title})]})}import{Fragment as Ga,jsx as ze,jsxs as Gt}from"react/jsx-runtime";var Vn=new Set,ft=7,Un=6;function Bn(...e){return e.filter(Boolean).join(" ")}function Pa(e){let t=[];for(let n=0;n<e.length;n+=ft)t.push(e.slice(n,n+ft));return t}function Na(e){let t=new Map;for(let n of e){let a=pe(n.start),r=t.get(a);r?r.push(n):t.set(a,[n])}return t}function Fa(e){return{start:`${e}T00:00:00`,end:`${ke(e,1)}T00:00:00`,allDay:!0}}function Yn(e){let{events:t,anchor:n,locale:a,firstDayOfWeek:r,messages:i,weekdayLabels:o,maxEventsPerDay:u,themeVars:l,onEventDrop:c,onRangeSelect:p,onEventClick:v,onContextMenu:f,pendingIds:O=Vn,rolledBackIds:x=Vn}=e,b=Z.useMemo(()=>Yt(n,r),[n,r]),P=Z.useMemo(()=>Pa(b),[b]),L=Z.useMemo(()=>o??Pn(a,r),[o,a,r]),ne=Z.useMemo(()=>Na(t),[t]),T=n.getMonth(),_=pe(j(new Date)),z=Z.useMemo(()=>pe(j(n)),[n]),[Y,h]=Z.useReducer(rn,Lt),[ue,fe]=Z.useState(()=>new Set),le=Z.useId(),[q,De]=Z.useState(z),[ae,W]=Z.useState(null),[U,ce]=Z.useState(null),[xe,Te]=Z.useState("");Z.useEffect(()=>{b.includes(q)||(De(z),W(null),ce(null))},[b,q,z]);let ge=Z.useCallback(G=>!!v||G.editable!==!1&&!!c,[v,c]);Z.useEffect(()=>{let G=new Set((ne.get(q)??[]).filter(A=>ge(A)).map(A=>A.id));U&&!G.has(U.eventId)?(ce(null),W(null)):!U&&ae!==null&&!G.has(ae)&&W(null)},[ne,q,ae,U,ge]);let Ce=G=>`${le}-c-${G}`,Le=(G,A)=>`${le}-e-${G}-${A}`,Q=`${le}-hint`,K=U?Le(q,U.eventId):ae?Le(q,ae):Ce(q),Ie=Z.useCallback(G=>{fe(A=>{let V=new Set(A);return V.add(G),V})},[]),me=Z.useCallback(G=>A=>{if(A.preventDefault(),!Pt(Y)){h({type:"DROP"});return}let V=Y.eventId,de=A.dataTransfer.getData("text/plain");if(h({type:"DROP"}),de&&de!==V||!c)return;let ie=t.find(ee=>ee.id===V);!ie||ie.editable===!1||c(ut(ie,G))},[Y,t,c]),re=!!c,J=Z.useCallback(G=>{if(!U)return;let A=ke(U.targetDate,G),V=b[0],de=b[b.length-1];A<V||A>de||(Te(i.movedTo(Tt(A,a))),ce({...U,targetDate:A,moved:!0}))},[U,b,a,i]),Re=Z.useCallback(()=>{if(!U)return;if(!U.moved){W(U.eventId),ce(null);return}let G=t.find(A=>A.id===U.eventId);G&&G.editable!==!1&&c&&(c(ut(G,U.targetDate)),Te(i.dropped(Tt(U.targetDate,a)))),De(U.targetDate),W(null),ce(null)},[U,t,c,i,a]),Pe={ArrowLeft:-1,ArrowRight:1,ArrowUp:-ft,ArrowDown:ft},ve=Z.useCallback(G=>{let{key:A}=G,V=A==="Enter"||A===" "||A==="Spacebar";if(U){if(A in Pe){G.preventDefault(),J(Pe[A]);return}if(V){G.preventDefault(),Re();return}if(A==="Escape"){G.preventDefault(),ce(null),Te(i.cancelled);return}return}let de=ne.get(q)??[],ie=de.filter(ee=>ge(ee));if(ae){let ee=ie.findIndex(X=>X.id===ae);if(A==="ArrowDown"){G.preventDefault(),ee>=0&&ee<ie.length-1&&W(ie[ee+1].id);return}if(A==="ArrowUp"){G.preventDefault(),ee>0?W(ie[ee-1].id):W(null);return}if(V){G.preventDefault();let X=ie.find(he=>he.id===ae);if(!X)return;X.editable!==!1&&c?(ce({eventId:X.id,targetDate:q,moved:!1}),Te(i.grabbedMoveHint(X.title))):v&&v({id:X.id});return}if(A==="Escape"){G.preventDefault(),W(null);return}if(A==="ArrowLeft"||A==="ArrowRight"||A==="Home"||A==="End"){G.preventDefault(),W(null);let X=nt(b.indexOf(q),A,Un,ft);De(b[X]);return}return}if(A in Pe||A==="Home"||A==="End"){G.preventDefault();let ee=nt(b.indexOf(q),A,Un,ft);De(b[ee]);return}V&&(ie.length>0?(G.preventDefault(),Ie(q),W(ie[0].id)):de.length===0&&p&&(G.preventDefault(),p(Fa(q)),Te(i.createHere(Tt(q,a)))))},[U,ae,q,b,ne,ge,c,v,p,J,Re,Ie,i,a,Pe]);return Gt(Ga,{children:[Gt("div",{className:Bn("aethercal-calendar",Pt(Y)&&"is-dragging"),role:"grid","aria-label":un(n,a),"aria-describedby":Q,"aria-activedescendant":K,tabIndex:0,"data-view":"month",style:l,onKeyDown:ve,children:[ze("div",{className:"aethercal-weekdays",role:"row",children:L.map((G,A)=>ze("div",{role:"columnheader",className:"aethercal-weekday",children:G},A))}),P.map((G,A)=>ze("div",{className:"aethercal-week",role:"row",children:G.map(V=>{let de=ne.get(V)??[],ie=ue.has(V),ee=ie?de:de.slice(0,u),X=de.length-ee.length,he=new Date(`${V}T00:00:00`).getMonth()!==T,ye=V===_,oe=!ae&&!U&&V===q,Se=U?.targetDate===V;return Gt("div",{id:Ce(V),role:"gridcell",className:Bn("aethercal-day",he&&"is-outside",ye&&"is-today",oe&&"is-active",Se&&"is-drop-target"),"data-date":V,onDragOver:re?te=>te.preventDefault():void 0,onDrop:re?me(V):void 0,onContextMenu:f?te=>{te.target.closest("[data-event-id], button")||(te.preventDefault(),f({start:`${V}T00:00:00`}))}:void 0,children:[ze("span",{className:"aethercal-sr-only",children:Tt(V,a)}),ze("div",{className:"aethercal-day-head",children:ze("span",{className:"aethercal-day-number","aria-hidden":"true",children:Number(V.slice(-2))})}),Gt("div",{className:"aethercal-day-events",children:[ee.map(te=>{let Qe=U?.eventId===te.id||!U&&ae===te.id;return ze(Ft,{id:Le(V,te.id),event:te,interactive:ge(te),isActive:Qe,isGrabbed:U?.eventId===te.id,timeLabel:te.allDay?null:be(te.start,a),canDrag:re,onDragStart:s=>h({type:"DRAG_START",eventId:s}),onDragEnd:()=>h({type:"DRAG_CANCEL"}),isPending:O.has(te.id),isRolledBack:x.has(te.id),...v?{onClick:()=>v({id:te.id})}:{},...f?{onContextMenu:()=>f({id:te.id})}:{}},te.id)}),X>0&&!ie?ze("button",{type:"button",className:"aethercal-more",onClick:()=>Ie(V),children:i.more(X)}):null]})]},V)})},A))]}),ze(pt,{id:Q,text:i.keyboardHint}),ze(mt,{message:xe})]})}var Kn={light:{"--ac-fg":"#1f2328","--ac-muted":"#5f6672","--ac-faint":"#676e79","--ac-bg":"#ffffff","--ac-header-fg":"#4b5563","--ac-border":"#e5e7eb","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#fafafa","--ac-today-marker-bg":"#111827","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#eef1f4","--ac-event-fg":"#1f2328","--ac-event-accent":"#64748b","--ac-more-fg":"#4b5563","--ac-focus":"#2563eb","--ac-rollback":"#b91c1c","--ac-tg-now":"#dc2626"},dark:{"--ac-fg":"#e6e8eb","--ac-muted":"#9aa1ab","--ac-faint":"#868e99","--ac-bg":"#14161a","--ac-header-fg":"#b3b9c2","--ac-border":"#2a2e35","--ac-cell-bg":"#171a1f","--ac-cell-bg-outside":"#111318","--ac-today-marker-bg":"#e6e8eb","--ac-today-marker-fg":"#14161a","--ac-event-bg":"#242a32","--ac-event-fg":"#e6e8eb","--ac-event-accent":"#8b98a9","--ac-more-fg":"#b3b9c2","--ac-focus":"#6ea8fe","--ac-rollback":"#f87171","--ac-tg-now":"#f87171"},midnight:{"--ac-fg":"#dfe4ea","--ac-muted":"#8b95a1","--ac-faint":"#828a95","--ac-bg":"#0b0f14","--ac-header-fg":"#a7b0bd","--ac-border":"#1c232c","--ac-cell-bg":"#0e131a","--ac-cell-bg-outside":"#090d12","--ac-today-marker-bg":"#dfe4ea","--ac-today-marker-fg":"#0b0f14","--ac-event-bg":"#17212c","--ac-event-fg":"#dfe4ea","--ac-event-accent":"#7f8ea3","--ac-more-fg":"#a7b0bd","--ac-focus":"#74a9ff","--ac-rollback":"#fb7185","--ac-tg-now":"#fb7185"},high_contrast:{"--ac-fg":"#000000","--ac-muted":"#000000","--ac-faint":"#1a1a1a","--ac-bg":"#ffffff","--ac-header-fg":"#000000","--ac-border":"#000000","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#ffffff","--ac-today-marker-bg":"#000000","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#e0e0e0","--ac-event-fg":"#000000","--ac-event-accent":"#000000","--ac-more-fg":"#000000","--ac-focus":"#0033cc","--ac-rollback":"#b00000","--ac-tg-now":"#d00000"}};var zt=Kn,Wn=["light","dark","midnight","high_contrast"],_a=new Set(Wn),$a={"--ac-font":'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',"--ac-radius":"8px","--ac-cell-min-height":"96px"},Ha={"--ac-tg-gutter":"56px","--ac-tg-body-height":"640px","--ac-tg-hour-min-height":"44px","--ac-tg-line":"var(--ac-border)","--ac-tg-event-bg":"var(--ac-event-bg)","--ac-tg-event-fg":"var(--ac-event-fg)","--ac-tg-event-accent":"var(--ac-event-accent)"},Va={"--ac-tl-rowhead-width":"168px","--ac-tl-lane-height":"30px","--ac-tl-body-height":"560px","--ac-tl-line":"var(--ac-border)","--ac-tl-event-bg":"var(--ac-event-bg)","--ac-tl-event-fg":"var(--ac-event-fg)","--ac-tl-event-accent":"var(--ac-event-accent)","--ac-tl-group-bg":"var(--ac-cell-bg-outside)","--ac-tl-now":"var(--ac-tg-now)"},Xn=["--ac-tg-now"],Ua=/[;{}<>]/;function Jn(e){return typeof e=="string"&&_a.has(e)}function pn(e){return Object.entries(e).map(([t,n])=>`  ${t}: ${n};`).join(`
`)}function Ba(){let e={};for(let[t,n]of Object.entries(zt.light))Xn.includes(t)||(e[t]=n);return e}function jn(){let e={};for(let t of Xn){let n=zt.light[t];n!==void 0&&(e[t]=n)}return e}function fn(){return pn({...$a,...Ba()})}function vn(){return pn({...Ha,...jn()})}function hn(){return pn({...Va,...jn()})}function Ya(e){let t={};for(let[n,a]of Object.entries(e))n.startsWith("--ac-")&&(typeof a!="string"||a.trim()===""||Ua.test(a)||(t[n]=a));return t}function yn(e){return e===void 0?{}:typeof e=="string"?Jn(e)?{...zt[e]}:{}:Ya(e)}var qn="aethercal-calendar-styles",Zn=`
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
`;function dt(){if(typeof document>"u"||document.getElementById(qn))return;let e=document.createElement("style");e.id=qn,e.textContent=Zn,document.head.appendChild(e)}import*as H from"react";function Fe(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(I(e))}function Qn(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function ea(e,t){if(e.length===0)return"";let n=I(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(n);let a=I(e[e.length-1]),r=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${r.format(n)} \u2013 ${r.format(a)}`}var ta="aethercal-timegrid-styles",na=`
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
`;function bn(){if(typeof document>"u"||document.getElementById(ta))return;let e=document.createElement("style");e.id=ta,e.textContent=na,document.head.appendChild(e)}import{Fragment as ia,jsx as Ee,jsxs as Ze}from"react/jsx-runtime";function _t(...e){return e.filter(Boolean).join(" ")}var Ue=e=>`${e*100}%`,aa=new Set;function ra(e){let t=I(e);return t.getHours()*60+t.getMinutes()}function Dn(e,t,n){let a=I(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}function Ka(e,t,n,a){let{event:r,isContinuation:i,continuesAfter:o}=e;return i?o?n:a(be(r.end,t)):be(r.start,t)}function $t(e,t){let n=t.getBoundingClientRect();return n.height>0?(e-n.top)/n.height:0}function wn(e){let{view:t,days:n,events:a,locale:r,config:i,now:o,themeVars:u,onEventDrop:l,onEventResize:c,onRangeSelect:p,onEventClick:v,onContextMenu:f,pendingIds:O=aa,rolledBackIds:x=aa}=e,b=H.useMemo(()=>{if(e.messages)return e.messages;let s={...e.allDayLabel!==void 0?{allDay:e.allDayLabel}:{},...e.continuesLabel!==void 0?{continues:e.continuesLabel}:{},...e.formatEndsLabel!==void 0?{endsAt:e.formatEndsLabel}:{}};return lt(r,s)},[e.messages,e.allDayLabel,e.continuesLabel,e.formatEndsLabel,r]);H.useEffect(()=>{dt(),bn()},[]);let P=H.useMemo(()=>Qt(n,a,i),[n,a,i]),L=H.useMemo(()=>en(o,i),[o,i]),ne=H.useMemo(()=>pe(j(o)),[o]),[T,_]=H.useReducer(xt,gt),z=H.useRef(null),[Y,h]=H.useState(null),[ue,fe]=H.useState(null),le=!!l,q=!!c,De=!!p,ae=T.status==="dragging",W=H.useCallback((s,g)=>D=>{if(D.preventDefault(),T.status!=="dragging"){_({type:"COMMIT"});return}let R=T.eventId,C=D.dataTransfer.getData("text/plain");if(_({type:"COMMIT"}),C&&C!==R||!l)return;let w=a.find(Me=>Me.id===R);if(!w||w.editable===!1)return;let E=null;if(g&&w.allDay!==!0){let we=D.currentTarget.getBoundingClientRect();we.height>0&&Number.isFinite(D.clientY)&&(E=rt((D.clientY-we.top)/we.height,P.config))}l(it(w,s,E))},[T,a,l,P.config]),U=H.useCallback(s=>{z.current?.kind!=="resize"&&_({type:"DRAG_START",eventId:s})},[]),ce=H.useCallback(()=>_({type:"CANCEL"}),[]),xe=H.useCallback((s,g)=>D=>{if(!c||s.editable===!1||D.button!==0||z.current)return;let R=D.currentTarget.closest(".aethercal-tg-col");R?.dataset.date&&(D.preventDefault(),D.stopPropagation(),z.current={kind:"resize",pointerId:D.pointerId,eventId:s.id,edge:g,dateOnly:R.dataset.date,colEl:R,payload:null},D.currentTarget.setPointerCapture?.(D.pointerId),_({type:"RESIZE_START",eventId:s.id,edge:g}))},[c]),Te=H.useCallback(s=>g=>{if(!p||g.button!==0||z.current||g.target.closest("[data-event-id], button"))return;let D=g.currentTarget,R=rt($t(g.clientY,D),P.config);z.current={kind:"select",pointerId:g.pointerId,anchorDate:s,anchorCol:D,anchorMinute:R,currentDate:s,currentCol:D,currentMinute:R},D.setPointerCapture?.(g.pointerId),_({type:"SELECT_START",point:{dateOnly:s,minuteOfDay:R}})},[p,P.config]),ge=T.status==="resizing"||T.status==="selecting";H.useLayoutEffect(()=>{if(!ge)return;let s=w=>{let E=z.current;if(!(!E||w.pointerId!==E.pointerId))if(E.kind==="resize"){let Me=document.elementFromPoint(w.clientX,w.clientY)?.closest(".aethercal-tg-col"),we=Me?.dataset.date?Me:E.colEl,Ye=rt($t(w.clientY,we),P.config),d=a.find(k=>k.id===E.eventId);if(!d)return;let m=He(d,E.edge,we.dataset.date??E.dateOnly,Ye);E.payload=m,h(m)}else{let Me=document.elementFromPoint(w.clientX,w.clientY)?.closest(".aethercal-tg-col"),we=Me?.dataset.date?Me:E.currentCol;E.currentCol=we,E.currentDate=we.dataset.date??E.anchorDate,E.currentMinute=rt($t(w.clientY,we),P.config);let Ye=We({dateOnly:E.anchorDate,minuteOfDay:E.anchorMinute},{dateOnly:E.currentDate,minuteOfDay:E.currentMinute}),m=(E.currentDate===E.anchorDate?bt([{id:"__sel",title:"",start:Ye.start,end:Ye.end}],E.anchorDate,P.config):[])[0];fe(m?{dateOnly:E.anchorDate,topFraction:m.topFraction,heightFraction:m.heightFraction}:null)}},g=w=>{let E=z.current;z.current=null,h(null),fe(null),w&&E&&(E.kind==="resize"&&E.payload&&c&&c(E.payload),E.kind==="select"&&p&&(E.currentDate!==E.anchorDate||E.currentMinute!==E.anchorMinute)&&p(We({dateOnly:E.anchorDate,minuteOfDay:E.anchorMinute},{dateOnly:E.currentDate,minuteOfDay:E.currentMinute}))),_({type:w?"COMMIT":"CANCEL"})},D=w=>{z.current&&w.pointerId!==z.current.pointerId||g(!0)},R=w=>{z.current&&w.pointerId!==z.current.pointerId||g(!1)},C=w=>{w.key==="Escape"&&g(!1)};return window.addEventListener("pointermove",s),window.addEventListener("pointerup",D),window.addEventListener("pointercancel",R),window.addEventListener("keydown",C),()=>{window.removeEventListener("pointermove",s),window.removeEventListener("pointerup",D),window.removeEventListener("pointercancel",R),window.removeEventListener("keydown",C)}},[ge,a,P.config,c,p]);let Ce=H.useCallback((s,g)=>D=>{if(!f||D.target.closest("[data-event-id], button"))return;if(D.preventDefault(),!g){f({start:`${s}T00:00:00`});return}let R=rt($t(D.clientY,D.currentTarget),P.config),C=I(`${s}T00:00:00`),w=new Date(C.getFullYear(),C.getMonth(),C.getDate(),0,R,0);f({start:j(w)})},[f,P.config]),Le=H.useId(),Q=H.useMemo(()=>P.columns.map(s=>s.dateOnly),[P.columns]),[K,Ie]=H.useState(()=>(Q.includes(ne)?ne:Q[0])??""),[me,re]=H.useState(null),[J,Re]=H.useState(null),[Pe,ve]=H.useState("");H.useEffect(()=>{Q.includes(K)||(Ie(Q[0]??""),re(null),Re(null))},[Q,K]);let G=s=>`${Le}-col-${s}`,A=(s,g)=>`${Le}-e-${s}-${g}`,V=`${Le}-hint`,de=Ne,ie=H.useCallback(s=>!!v||s.editable!==!1&&!!(l||c),[v,l,c]),ee=H.useMemo(()=>{let s=P.columns.find(g=>g.dateOnly===K);return s?[...s.allDay,...s.timed.map(g=>g.event)]:[]},[P.columns,K]),X=H.useMemo(()=>ee.filter(s=>ie(s)),[ee,ie]);H.useEffect(()=>{let s=new Set(X.map(g=>g.id));J&&!s.has(J.eventId)?(Re(null),re(null)):!J&&me!==null&&!s.has(me)&&re(null)},[X,me,J]);let he=J?A(K,J.eventId):me?A(K,me):G(K),ye=H.useCallback(s=>{let g=J;if(!g)return;let D=g.dateOnly,R=g.minute,C=a.find(E=>E.id===g.eventId),w=C?.allDay===!0;if(!w&&(s==="ArrowUp"||s==="ArrowDown")){let E=Zt(D,R,s==="ArrowUp"?-de:de,P.config);D=E.dateOnly,R=E.minuteOfDay}else s==="ArrowLeft"?D=ke(D,-1):s==="ArrowRight"&&(D=ke(D,1));if(!(D===g.dateOnly&&R===g.minute)){if(C)if(g.kind==="move")ve(b.movedTo(w?Fe(D,r):`${Fe(D,r)} ${Dn(D,R,r)}`));else{let E=He(C,"end",D,R);ve(b.resizedTo(`${be(E.start,r)} \u2013 ${be(E.end,r)}`))}Re({...g,dateOnly:D,minute:R,moved:!0})}},[J,de,P.config,a,b,r]),oe=H.useCallback(()=>{let s=J;if(!s)return;if(!s.moved){re(s.eventId),Re(null);return}let g=a.find(D=>D.id===s.eventId);if(g&&g.editable!==!1&&s.kind==="move"&&l){let D=it(g,s.dateOnly,g.allDay===!0?null:s.minute);l(D);let R=pe(D.start);Ie(Q.includes(R)?R:K),re(null),ve(b.dropped(g.allDay===!0?Fe(s.dateOnly,r):Dn(s.dateOnly,s.minute,r)))}else if(g&&g.editable!==!1&&s.kind==="resize"&&c){let D=He(g,"end",s.dateOnly,s.minute);c(D),re(s.eventId),ve(b.resized(`${be(D.start,r)} \u2013 ${be(D.end,r)}`))}else re(s.eventId);Re(null)},[J,a,l,c,Q,K,b,r]),Se=H.useCallback(s=>{let{key:g}=s,D=g==="Enter"||g===" "||g==="Spacebar",R=g==="ArrowUp"||g==="ArrowDown"||g==="ArrowLeft"||g==="ArrowRight";if(J){if(R){s.preventDefault(),ye(g);return}if(D){s.preventDefault(),oe();return}if(g==="Escape"){s.preventDefault(),Re(null),ve(b.cancelled);return}return}if(me){let C=X.findIndex(w=>w.id===me);if(g==="ArrowDown"){s.preventDefault(),C>=0&&C<X.length-1&&re(X[C+1].id);return}if(g==="ArrowUp"){s.preventDefault(),C>0?re(X[C-1].id):re(null);return}if(g==="ArrowLeft"||g==="ArrowRight"){s.preventDefault(),re(null);let w=Q.indexOf(K);Ie(Q[nt(w,g,1,Q.length)]);return}if(D){s.preventDefault();let w=X.find(E=>E.id===me);if(!w)return;w.editable!==!1&&l?(Re({kind:"move",eventId:w.id,dateOnly:pe(w.start),minute:ra(w.start),moved:!1}),ve(b.grabbedMoveHint(w.title))):v&&v({id:w.id});return}if((g==="r"||g==="R")&&c){s.preventDefault();let w=X.find(E=>E.id===me);w&&w.allDay!==!0&&w.editable!==!1&&(Re({kind:"resize",eventId:w.id,dateOnly:pe(w.end),minute:ra(w.end),moved:!1}),ve(b.grabbedResizeHint(w.title)));return}if(g==="Escape"){s.preventDefault(),re(null);return}return}if(g==="ArrowLeft"||g==="ArrowRight"||g==="Home"||g==="End"){s.preventDefault();let C=Q.indexOf(K);Ie(Q[nt(C,g,1,Q.length)]);return}if(g==="ArrowDown"){X.length>0&&(s.preventDefault(),re(X[0].id));return}if(D){if(X.length>0)s.preventDefault(),re(X[0].id);else if(ee.length===0&&p){let C=P.config.dayEndHour*60,w=kt(P.config.dayStartHour*60,P.config),E=Math.min(w+60,C);E>w&&(s.preventDefault(),p(We({dateOnly:K,minuteOfDay:w},{dateOnly:K,minuteOfDay:E})),ve(b.createHere(`${Fe(K,r)} ${Dn(K,w,r)}`)))}}},[J,me,K,ee,X,Q,l,c,v,p,ye,oe,P.config,b,r]),te={"--ac-tg-cols":P.columns.length,"--ac-tg-hours":P.config.dayEndHour-P.config.dayStartHour,...u??{}},Qe=b.allDay;return Ze(ia,{children:[Ze("div",{className:_t("aethercal-calendar","aethercal-timegrid",ae&&"is-dragging",T.status==="resizing"&&"is-resizing",T.status==="selecting"&&"is-selecting"),role:"grid","aria-label":ea(n,r),"aria-describedby":V,"aria-activedescendant":he,tabIndex:0,"data-view":t,style:te,onKeyDown:Se,children:[Ze("div",{className:"aethercal-tg-head",role:"row",children:[Ee("div",{className:"aethercal-tg-corner"}),P.columns.map(s=>Ee("div",{role:"columnheader",className:_t("aethercal-tg-colhead",s.dateOnly===ne&&"is-today"),"data-date":s.dateOnly,children:Ee("span",{className:"aethercal-tg-colhead-date",children:Fe(s.dateOnly,r)})},s.dateOnly))]}),Ze("div",{className:"aethercal-tg-allday",role:"row",children:[Ee("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:Qe}),P.columns.map(s=>Ee("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":s.dateOnly,onDragOver:le?g=>g.preventDefault():void 0,onDrop:le?W(s.dateOnly,!1):void 0,onContextMenu:f?Ce(s.dateOnly,!1):void 0,children:s.allDay.map(g=>{let D=J?.eventId===g.id&&s.dateOnly===K||!J&&me===g.id&&s.dateOnly===K;return Ee(Ft,{id:A(s.dateOnly,g.id),event:g,interactive:ie(g),isActive:D,isGrabbed:J?.eventId===g.id&&s.dateOnly===K,timeLabel:null,canDrag:le,onDragStart:U,onDragEnd:ce,isPending:O.has(g.id),isRolledBack:x.has(g.id),...v?{onClick:()=>v({id:g.id})}:{},...f?{onContextMenu:()=>f({id:g.id})}:{}},g.id)})},s.dateOnly))]}),Ze("div",{className:"aethercal-tg-body",role:"row",tabIndex:0,children:[Ee("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:P.hourMarks.map(s=>Ee("div",{className:"aethercal-tg-hour",style:{top:Ue(s.topFraction)},children:Qn(s.hour,r)},s.hour))}),P.columns.map(s=>{let g=!me&&!J&&s.dateOnly===K,D=J?.dateOnly===s.dateOnly;return Ze("div",{id:G(s.dateOnly),role:"gridcell",className:_t("aethercal-tg-col",s.dateOnly===ne&&"is-today",g&&"is-active",D&&"is-drop-target"),"data-date":s.dateOnly,onDragOver:le?R=>R.preventDefault():void 0,onDrop:le?W(s.dateOnly,!0):void 0,onPointerDown:De?Te(s.dateOnly):void 0,onContextMenu:f?Ce(s.dateOnly,!0):void 0,children:[P.hourMarks.map(R=>Ee("div",{className:"aethercal-tg-line",style:{top:Ue(R.topFraction)},"aria-hidden":"true"},R.hour)),ue&&ue.dateOnly===s.dateOnly?Ee("div",{className:"aethercal-tg-select-band",style:{top:Ue(ue.topFraction),height:Ue(ue.heightFraction)},"aria-hidden":"true"}):null,s.timed.map(R=>{let{event:C}=R,w=C.editable!==!1,E=Ka(R,r,b.continues,b.endsAt),Me=Y?.id===C.id?Y:null,we=Me?bt([{...C,start:Me.start,end:Me.end}],s.dateOnly,P.config)[0]:void 0,Ye=we?we.topFraction:R.topFraction,d=we?we.heightFraction:R.heightFraction,m=J?.eventId===C.id&&s.dateOnly===K||!J&&me===C.id&&s.dateOnly===K,k=J?.eventId===C.id&&s.dateOnly===K,S={top:Ue(Ye),height:Ue(d),left:Ue(R.lane/R.laneCount),width:Ue(1/R.laneCount),...C.color?{"--ac-tg-event-accent":C.color}:{}};return Ze("div",{id:A(s.dateOnly,C.id),className:_t("aethercal-tg-event",!w&&"is-locked",O.has(C.id)&&"is-pending",x.has(C.id)&&"is-rolledback",!!Me&&"is-resizing",m&&"is-active",k&&"is-grabbed"),...ie(C)?{role:"button"}:{},draggable:w&&le,"data-event-id":C.id,"data-lane":R.lane,"data-lane-count":R.laneCount,"aria-label":`${E} ${C.title}`,title:C.title,style:S,onDragStart:$=>{if(!le||z.current?.kind==="resize"){$.preventDefault();return}$.dataTransfer.setData("text/plain",C.id),$.dataTransfer.effectAllowed="move",U(C.id)},onDragEnd:ce,onClick:v?()=>v({id:C.id}):void 0,onContextMenu:f?$=>{$.preventDefault(),$.stopPropagation(),f({id:C.id})}:void 0,children:[Ee("time",{className:"aethercal-tg-event-time",children:E})," ",Ee("span",{className:"aethercal-tg-event-title",children:C.title}),q&&w?Ze(ia,{children:[Ee("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:xe(C,"start")}),Ee("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:xe(C,"end")})]}):null]},C.id)}),L!==null&&s.dateOnly===ne?Ee("div",{className:"aethercal-now-indicator",style:{top:Ue(L)},"aria-hidden":"true"}):null]},s.dateOnly)})]})]}),Ee(pt,{id:V,text:b.keyboardHint}),Ee(mt,{message:Pe})]})}import*as N from"react";var oa="aethercal-timeline-styles",sa=`
:where(.aethercal-timeline) {
${hn()}
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
`;function En(){if(typeof document>"u"||document.getElementById(oa))return;let e=document.createElement("style");e.id=oa,e.textContent=sa,document.head.appendChild(e)}import{Fragment as ca,jsx as se,jsxs as $e}from"react/jsx-runtime";function Be(...e){return e.filter(Boolean).join(" ")}var _e=e=>`${e*100}%`,la=new Set,Wa="unassigned",Xa=e=>e.resource?`r:${e.resource.id}`:Wa;function Ht(e,t){let n=t.getBoundingClientRect();return n.width>0?(e-n.left)/n.width:0}function da(e){let t=I(e);return t.getHours()*60+t.getMinutes()}function xn(e,t,n){let a=I(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}function Tn(e){let{days:t,resources:n,events:a,locale:r,config:i,now:o,themeVars:u,defaultCollapsedGroupIds:l,onToggleGroup:c,onEventDrop:p,onEventResize:v,onRangeSelect:f,onEventClick:O,onContextMenu:x,pendingIds:b=la,rolledBackIds:P=la}=e,L=N.useMemo(()=>e.messages??lt(r),[e.messages,r]);N.useEffect(()=>{dt(),En()},[]);let[ne,T]=N.useState(""),[_,z]=N.useState(()=>new Set(l??[])),Y=N.useMemo(()=>[..._],[_]),h=N.useMemo(()=>nn(n,a,t,{...i,collapsedGroupIds:Y}),[n,a,t,i,Y]),ue=N.useMemo(()=>h.items.flatMap(d=>d.kind==="row"?[d.row]:[]),[h.items]),fe=N.useMemo(()=>ue.filter(d=>d.resource!==null),[ue]),le=N.useMemo(()=>an(o,t,h.config),[o,t,h.config]),q=N.useMemo(()=>pe(j(o)),[o]),[De,ae]=N.useReducer(xt,gt),W=N.useRef(null),[U,ce]=N.useState(null),[xe,Te]=N.useState(null),ge=!!p,Ce=!!v,Le=!!f,Q=N.useCallback((d,m)=>{let{windowMinutes:k,dayStartHour:S}=h.config,$=t.length*k;if($<=0)return 0;let F=t.indexOf(d);return((F===-1?0:F)*k+(m-S*60))/$},[t,h.config]),K=N.useCallback(d=>{let m=!_.has(d);z(k=>{let S=new Set(k);return S.has(d)?S.delete(d):S.add(d),S}),c?.(d,m),T(m?L.groupCollapsed(d):L.groupExpanded(d))},[_,c,L]),Ie=N.useCallback(d=>m=>{if(m.preventDefault(),De.status!=="dragging"){ae({type:"COMMIT"});return}let k=De.eventId,S=m.dataTransfer.getData("text/plain");if(ae({type:"COMMIT"}),S&&S!==k||!p||!d.resource)return;let $=a.find(M=>M.id===k);if(!$||$.editable===!1)return;let F=st(Ht(m.clientX,m.currentTarget),t,h.config);if(!F)return;let y=$.allDay===!0?null:F.minuteOfDay;p(it($,F.dateOnly,y,d.resource.id))},[De,a,p,t,h.config]),me=N.useCallback(d=>!p||W.current?.kind==="resize"?!1:(ae({type:"DRAG_START",eventId:d}),!0),[p]),re=N.useCallback(()=>ae({type:"CANCEL"}),[]),J=N.useCallback((d,m)=>k=>{if(!v||d.editable===!1||k.button!==0||W.current)return;let S=k.currentTarget.closest(".aethercal-tl-track");S&&(k.preventDefault(),k.stopPropagation(),W.current={kind:"resize",pointerId:k.pointerId,eventId:d.id,edge:m,trackEl:S,payload:null},k.currentTarget.setPointerCapture?.(k.pointerId),ae({type:"RESIZE_START",eventId:d.id,edge:m}))},[v]),Re=N.useCallback(d=>m=>{if(!f||m.button!==0||!d.resource||W.current||m.target.closest("[data-event-id], button"))return;let k=m.currentTarget,S=st(Ht(m.clientX,k),t,h.config);if(!S)return;let $=S.minuteOfDay??0;W.current={kind:"select",pointerId:m.pointerId,resourceId:d.resource.id,trackEl:k,anchorDate:S.dateOnly,anchorMinute:$,currentDate:S.dateOnly,currentMinute:$},k.setPointerCapture?.(m.pointerId),ae({type:"SELECT_START",point:{dateOnly:S.dateOnly,minuteOfDay:$,resourceId:d.resource.id}})},[f,t,h.config]),Pe=De.status==="resizing"||De.status==="selecting";N.useLayoutEffect(()=>{if(!Pe)return;let d=F=>{let y=W.current;if(!y||F.pointerId!==y.pointerId)return;let M=st(Ht(F.clientX,y.trackEl),t,h.config);if(!M)return;if(y.kind==="resize"){let ht=a.find(Mt=>Mt.id===y.eventId);if(!ht)return;let Rt=He(ht,y.edge,M.dateOnly,M.minuteOfDay??0);y.payload=Rt,ce(Rt);return}y.currentDate=M.dateOnly,y.currentMinute=M.minuteOfDay??0;let B=Q(y.anchorDate,y.anchorMinute),Ke=Q(y.currentDate,y.currentMinute);Te({resourceId:y.resourceId,leftFraction:Math.min(B,Ke),widthFraction:Math.abs(Ke-B)})},m=F=>{let y=W.current;W.current=null,ce(null),Te(null),F&&y&&(y.kind==="resize"&&y.payload&&v&&v(y.payload),y.kind==="select"&&f&&(y.currentDate!==y.anchorDate||y.currentMinute!==y.anchorMinute)&&f(We({dateOnly:y.anchorDate,minuteOfDay:y.anchorMinute,resourceId:y.resourceId},{dateOnly:y.currentDate,minuteOfDay:y.currentMinute,resourceId:y.resourceId}))),ae({type:F?"COMMIT":"CANCEL"})},k=F=>{W.current&&F.pointerId!==W.current.pointerId||m(!0)},S=F=>{W.current&&F.pointerId!==W.current.pointerId||m(!1)},$=F=>{F.key==="Escape"&&m(!1)};return window.addEventListener("pointermove",d),window.addEventListener("pointerup",k),window.addEventListener("pointercancel",S),window.addEventListener("keydown",$),()=>{window.removeEventListener("pointermove",d),window.removeEventListener("pointerup",k),window.removeEventListener("pointercancel",S),window.removeEventListener("keydown",$)}},[Pe,a,t,h.config,Q,v,f]);let ve=N.useCallback(d=>{if(!x||d.target.closest("[data-event-id], button"))return;let m=st(Ht(d.clientX,d.currentTarget),t,h.config);if(!m)return;d.preventDefault();let k=I(`${m.dateOnly}T00:00:00`),S=new Date(k.getFullYear(),k.getMonth(),k.getDate(),0,m.minuteOfDay??0,0);x({start:j(S)})},[x,t,h.config]),G=N.useId(),A=`${G}-hint`,V=Ne,[de,ie]=N.useState(0),[ee,X]=N.useState(0),[he,ye]=N.useState(null),[oe,Se]=N.useState(null),te=d=>`${G}-i-${d}`,Qe=d=>`${G}-e-${d}`;N.useEffect(()=>{de>h.items.length-1&&(ie(Math.max(0,h.items.length-1)),ye(null),Se(null))},[h.items.length,de]),N.useEffect(()=>{ee>t.length-1&&X(Math.max(0,t.length-1))},[t.length,ee]);let s=h.items[de],g=s?.kind==="row"?s.row:void 0,D=N.useCallback(d=>!!O||d.editable!==!1&&!!(p||v),[O,p,v]),R=N.useMemo(()=>(g?.blocks??[]).map(d=>d.event).filter(d=>D(d)),[g,D]);N.useEffect(()=>{let d=new Set(R.map(m=>m.id));oe&&!d.has(oe.eventId)?(Se(null),ye(null)):!oe&&he!==null&&!d.has(he)&&ye(null)},[R,he,oe]);let C=h.items.length===0?void 0:oe?Qe(oe.eventId):he?Qe(he):te(de),w=N.useCallback(d=>fe.find(m=>m.resource?.id===d)?.resource?.title??d,[fe]),E=N.useCallback(d=>{let m=oe;if(!m)return;let k=a.find(M=>M.id===m.eventId);if(!k)return;let S=k.allDay===!0,$=m.dateOnly,F=m.minute,y=m.kind==="move"?m.resourceId:"";if(d==="ArrowLeft"||d==="ArrowRight")if(S)$=ke($,d==="ArrowLeft"?-1:1);else{let M=d==="ArrowLeft"?-V:V,B=st(Q($,F+M),t,h.config,V);if(!B)return;$=B.dateOnly,F=B.minuteOfDay??F}else if(m.kind==="move"&&(d==="ArrowUp"||d==="ArrowDown")){let M=fe.findIndex(Ke=>Ke.resource?.id===y),B=d==="ArrowUp"?M-1:M+1;if(M===-1||B<0||B>=fe.length)return;y=fe[B].resource.id}else return;if(!($===m.dateOnly&&F===m.minute&&(m.kind!=="move"||y===m.resourceId)))if(m.kind==="move"){let M=S?Fe($,r):`${Fe($,r)} ${xn($,F,r)}`;T(L.movedTo(`${w(y)} \xB7 ${M}`)),Se({...m,dateOnly:$,minute:F,resourceId:y,moved:!0})}else{let M=He(k,"end",$,F);T(L.resizedTo(`${be(M.start,r)} \u2013 ${be(M.end,r)}`)),Se({...m,dateOnly:$,minute:F,moved:!0})}},[oe,a,V,t,h.config,fe,Q,w,L,r]),Me=N.useCallback(()=>{let d=oe;if(!d)return;if(!d.moved){ye(d.eventId),Se(null);return}let m=a.find(k=>k.id===d.eventId);if(m&&m.editable!==!1&&d.kind==="move"&&p){let k=m.allDay===!0?null:d.minute;p(it(m,d.dateOnly,k,d.resourceId)),T(L.dropped(`${w(d.resourceId)} \xB7 ${m.allDay===!0?Fe(d.dateOnly,r):xn(d.dateOnly,d.minute,r)}`)),ye(null)}else if(m&&m.editable!==!1&&d.kind==="resize"&&v){let k=He(m,"end",d.dateOnly,d.minute);v(k),T(L.resized(`${be(k.start,r)} \u2013 ${be(k.end,r)}`)),ye(d.eventId)}else ye(d.eventId);Se(null)},[oe,a,p,v,w,L,r]),we=N.useCallback(d=>{let{key:m}=d,k=m==="Enter"||m===" "||m==="Spacebar",S=m==="ArrowUp"||m==="ArrowDown"||m==="ArrowLeft"||m==="ArrowRight",$=h.items.length-1;if(oe){if(S){d.preventDefault(),E(m);return}if(k){d.preventDefault(),Me();return}m==="Escape"&&(d.preventDefault(),Se(null),T(L.cancelled));return}if(he){let F=R.findIndex(y=>y.id===he);if(m==="ArrowRight"){d.preventDefault(),F>=0&&F<R.length-1&&ye(R[F+1].id);return}if(m==="ArrowLeft"){d.preventDefault(),F>0?ye(R[F-1].id):ye(null);return}if(m==="ArrowUp"||m==="ArrowDown"){d.preventDefault(),ye(null),ie(y=>Math.min(Math.max(y+(m==="ArrowUp"?-1:1),0),$));return}if(k){d.preventDefault();let y=R.find(M=>M.id===he);if(!y)return;y.editable!==!1&&p&&g?.resource?(Se({kind:"move",eventId:y.id,dateOnly:pe(y.start),minute:da(y.start),resourceId:g.resource.id,moved:!1}),T(L.grabbedMoveHint(y.title))):O&&O({id:y.id});return}if((m==="r"||m==="R")&&v){d.preventDefault();let y=R.find(M=>M.id===he);y&&y.allDay!==!0&&y.editable!==!1&&(Se({kind:"resize",eventId:y.id,dateOnly:pe(y.end),minute:da(y.end),moved:!1}),T(L.grabbedResizeHint(y.title)));return}m==="Escape"&&(d.preventDefault(),ye(null));return}if(m==="ArrowUp"||m==="ArrowDown"){d.preventDefault(),ie(F=>Math.min(Math.max(F+(m==="ArrowUp"?-1:1),0),$));return}if(m==="ArrowLeft"||m==="ArrowRight"){d.preventDefault(),X(F=>Math.min(Math.max(F+(m==="ArrowLeft"?-1:1),0),Math.max(0,t.length-1)));return}if(m==="Home"||m==="End"){d.preventDefault(),X(m==="Home"?0:Math.max(0,t.length-1));return}if(k){if(s?.kind==="group"){d.preventDefault(),K(s.group.id);return}if(R.length>0){d.preventDefault(),ye(R[0].id);return}if(g?.resource&&g.blocks.length===0&&f&&t.length>0){let F=t[Math.min(ee,t.length-1)],y=h.config.dayStartHour*60,M=Math.min(y+60,h.config.dayEndHour*60);M>y&&(d.preventDefault(),f(We({dateOnly:F,minuteOfDay:y,resourceId:g.resource.id},{dateOnly:F,minuteOfDay:M,resourceId:g.resource.id})),T(L.createHere(`${g.resource.title} \xB7 ${Fe(F,r)} ${xn(F,y,r)}`)))}}},[oe,he,R,s,g,h.items.length,h.config,t,ee,p,v,O,f,E,Me,K,L,r]),Ye={...u??{}};return $e(ca,{children:[$e("div",{className:Be("aethercal-calendar","aethercal-timeline",De.status==="dragging"&&"is-dragging",De.status==="resizing"&&"is-resizing",De.status==="selecting"&&"is-selecting"),role:"grid","aria-label":L.viewNames.timeline,"aria-describedby":A,...C!==void 0?{"aria-activedescendant":C}:{},tabIndex:0,"data-view":"timeline",style:Ye,onKeyDown:we,children:[$e("div",{className:"aethercal-tl-head",role:"row",children:[se("div",{className:"aethercal-tl-corner",role:"columnheader",children:L.timelineResources}),se("div",{className:"aethercal-tl-days",children:h.dayHeaders.map(d=>se("div",{role:"columnheader",className:Be("aethercal-tl-dayhead",d.dateOnly===q&&"is-today"),"data-date":d.dateOnly,style:{left:_e(d.leftFraction),width:_e(d.widthFraction)},children:se("span",{children:Fe(d.dateOnly,r)})},d.dateOnly))})]}),$e("div",{className:"aethercal-tl-body",role:"rowgroup",tabIndex:0,children:[h.items.length===0?se("div",{className:"aethercal-tl-row aethercal-tl-row-empty",role:"row",children:se("div",{role:"gridcell",className:"aethercal-tl-empty",children:L.timelineEmpty})}):null,h.items.map((d,m)=>{let k=!he&&!oe&&m===de;if(d.kind==="group"){let{group:M}=d;return se("div",{role:"row",className:Be("aethercal-tl-group",M.collapsed&&"is-collapsed"),children:se("div",{className:"aethercal-tl-group-head",role:"rowheader",children:$e("button",{type:"button",id:te(m),className:Be("aethercal-tl-group-toggle",k&&"is-active"),"aria-expanded":!M.collapsed,tabIndex:-1,onClick:()=>K(M.id),children:[se("span",{className:"aethercal-tl-caret","aria-hidden":"true",children:"\u25BE"}),se("span",{children:M.id})," ",se("span",{className:"aethercal-tl-group-count",children:L.timelineGroupCount(M.resourceCount)})]})})},`g:${M.id}`)}let{row:S}=d,$=oe?.kind==="move"&&S.resource?.id===oe.resourceId,F={"--ac-tl-lanes":S.laneCount},y=S.resource?.color?{"--ac-tl-row-accent":S.resource.color}:{};return $e("div",{role:"row",className:Be("aethercal-tl-row",!S.resource&&"is-unassigned"),children:[$e("div",{id:te(m),role:"rowheader",className:Be("aethercal-tl-rowhead",k&&"is-active"),style:y,children:[S.resource?.color?se("span",{className:"aethercal-tl-swatch","aria-hidden":"true"}):null,se("span",{className:"aethercal-tl-rowhead-title",children:S.resource?S.resource.title:L.timelineUnassigned})]}),$e("div",{role:"gridcell",className:Be("aethercal-tl-track",$&&"is-drop-target"),"data-resource-id":S.resource?.id??"",style:F,onDragOver:ge&&S.resource?M=>M.preventDefault():void 0,onDrop:ge&&S.resource?Ie(S):void 0,onPointerDown:Le&&S.resource?Re(S):void 0,onContextMenu:x?ve:void 0,children:[h.ticks.map(M=>se("div",{className:Be("aethercal-tl-line",M.isDayStart&&"is-day-start"),style:{left:_e(M.leftFraction)},"aria-hidden":"true"},`${M.dateOnly}-${M.hour}`)),xe&&xe.resourceId===S.resource?.id?se("div",{className:"aethercal-tl-select-band",style:{left:_e(xe.leftFraction),width:_e(xe.widthFraction)},"aria-hidden":"true"}):null,S.blocks.map(M=>{let{event:B}=M,Ke=B.editable!==!1,ht=U?.id===B.id?U:null,Rt=oe?.eventId===B.id||!oe&&he===B.id&&g===S,Mt=M.allDay?L.allDay:be(ht?.start??B.start,r),ua={left:_e(M.leftFraction),width:_e(M.widthFraction),top:_e(M.lane/M.laneCount),height:_e(1/M.laneCount),...B.color?{"--ac-tl-event-accent":B.color}:{}};return $e("div",{id:Qe(B.id),className:Be("aethercal-tl-event",M.allDay&&"is-allday",!Ke&&"is-locked",M.continuesBefore&&"continues-before",M.continuesAfter&&"continues-after",b.has(B.id)&&"is-pending",P.has(B.id)&&"is-rolledback",!!ht&&"is-resizing",Rt&&"is-active",oe?.eventId===B.id&&"is-grabbed"),...D(B)?{role:"button"}:{},draggable:Ke&&ge,"data-event-id":B.id,"data-lane":M.lane,"aria-label":`${Mt} ${B.title}`,title:B.title,style:ua,onDragStart:ct=>{if(!me(B.id)){ct.preventDefault();return}ct.dataTransfer.setData("text/plain",B.id),ct.dataTransfer.effectAllowed="move"},onDragEnd:re,onClick:O?()=>O({id:B.id}):void 0,onContextMenu:x?ct=>{ct.preventDefault(),ct.stopPropagation(),x({id:B.id})}:void 0,children:[se("time",{className:"aethercal-tl-event-time",children:Mt})," ",se("span",{className:"aethercal-tl-event-title",children:B.title}),Ce&&Ke&&!M.allDay?$e(ca,{children:[se("div",{className:"aethercal-tl-resize-handle aethercal-tl-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:J(B,"start")}),se("div",{className:"aethercal-tl-resize-handle aethercal-tl-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:J(B,"end")})]}):null]},B.id)}),le!==null?se("div",{className:"aethercal-tl-now",style:{left:_e(le)},"aria-hidden":"true"}):null]})]},Xa(S))})]})]}),se(pt,{id:A,text:L.timelineKeyboardHint}),se(mt,{message:ne})]})}import{jsx as vt,jsxs as Za}from"react/jsx-runtime";function Ja(e){if(e instanceof Date)return e;if(typeof e=="string"){let t=e.trim();if(t==="")return new Date;try{return I(t)}catch{return new Date}}return new Date}function ja(e){return e instanceof Date?e:typeof e=="string"?I(e):new Date}function Vt(e){let{view:t="month",events:n,resources:a,timelineDays:r,defaultCollapsedGroupIds:i,onToggleGroup:o,anchor:u,locale:l="en",theme:c,messages:p,firstDayOfWeek:v=1,maxEventsPerDay:f=3,weekdayLabels:O,formatMore:x,unavailableLabel:b,dayStartHour:P,dayEndHour:L,allDayLabel:ne,now:T,continuesLabel:_,formatEndsLabel:z,agendaEmptyLabel:Y,onEventDrop:h,onEventResize:ue,onRangeSelect:fe,onEventClick:le,onContextMenu:q,navigation:De=!1,navigationViews:ae=!0,onRangeChange:W,onViewChange:U,pendingIds:ce,rolledBackIds:xe}=e;Ge.useEffect(()=>{dt()},[]);let Te=Ge.useMemo(()=>Ja(u),[u]),ge=Ge.useMemo(()=>yn(c),[c]),Ce=Ge.useMemo(()=>{let ve={...ne!==void 0?{allDay:ne}:{},..._!==void 0?{continues:_}:{},...z!==void 0?{endsAt:z}:{},...Y!==void 0?{noEvents:Y}:{},...b!==void 0?{unavailable:b}:{},...x!==void 0?{more:x}:{},...p};return lt(l,ve)},[l,ne,_,z,Y,b,x,p]),[Le,Q]=Ge.useState(()=>new Date);Ge.useEffect(()=>{if(T!==void 0||t!=="week"&&t!=="day"&&t!=="timeline")return;let ve=setInterval(()=>Q(new Date),6e4);return()=>clearInterval(ve)},[T,t]);let K=Ge.useMemo(()=>T!==void 0?ja(T):Le,[T,Le]),Ie=Number.isInteger(v)&&v>=0&&v<=6?v:1,me=Number.isInteger(f)&&f>=0?f:3,re=O&&O.length===7?O:void 0,J=Je(r),Re=Ge.useMemo(()=>({...P!==void 0?{dayStartHour:P}:{},...L!==void 0?{dayEndHour:L}:{}}),[P,L]),Pe=(()=>{if(t==="list")return vt(Gn,{events:n??[],locale:l,messages:Ce,themeVars:ge});if(t==="month")return vt(Yn,{events:n??[],anchor:Te,locale:l,messages:Ce,themeVars:ge,firstDayOfWeek:Ie,maxEventsPerDay:me,...re?{weekdayLabels:re}:{},...h?{onEventDrop:h}:{},...fe?{onRangeSelect:fe}:{},...le?{onEventClick:le}:{},...q?{onContextMenu:q}:{},...ce?{pendingIds:ce}:{},...xe?{rolledBackIds:xe}:{}});if(t==="timeline")return vt(Tn,{days:Kt(Te,J),resources:a??[],events:n??[],locale:l,messages:Ce,themeVars:ge,config:Re,now:K,...i?{defaultCollapsedGroupIds:i}:{},...o?{onToggleGroup:o}:{},...h?{onEventDrop:h}:{},...ue?{onEventResize:ue}:{},...fe?{onRangeSelect:fe}:{},...le?{onEventClick:le}:{},...q?{onContextMenu:q}:{},...ce?{pendingIds:ce}:{},...xe?{rolledBackIds:xe}:{}});if(t==="week"||t==="day"){let ve=t==="week"?Bt(Te,Ie):[pe(j(Te))];return vt(wn,{view:t,days:ve,events:n??[],locale:l,messages:Ce,themeVars:ge,config:Re,now:K,...h?{onEventDrop:h}:{},...ue?{onEventResize:ue}:{},...fe?{onRangeSelect:fe}:{},...le?{onEventClick:le}:{},...q?{onContextMenu:q}:{},...ce?{pendingIds:ce}:{},...xe?{rolledBackIds:xe}:{}})}return vt("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,style:ge,children:Ce.unavailable})})();return De?Za("div",{className:"aethercal-calendar-shell",style:ge,children:[vt(gn,{view:t,anchor:Te,now:K,locale:l,firstDayOfWeek:Ie,timelineDays:J,messages:Ce,showViews:ae,...W?{onRangeChange:W}:{},...U?{onViewChange:U}:{}}),Pe]}):Pe}var qa=Vt;import*as Oe from"react";function Qa(){return typeof crypto<"u"&&typeof crypto.randomUUID=="function"?crypto.randomUUID():`cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}var er=8e3,tr=900;function Rn(e){let{events:t,mutate:n,timeoutMs:a=er,rollbackFlashMs:r=tr,generateId:i=Qa}=e,[o,u]=Oe.useReducer(sn,on),l=Oe.useRef(t);l.current=t;let c=Oe.useRef(!0),p=Oe.useRef(new Map);Oe.useEffect(()=>{c.current=!0;let O=p.current;return()=>{c.current=!1;for(let x of O.values())clearTimeout(x);O.clear()}},[]),Oe.useEffect(()=>{for(let O of dn(t,o)){let x=o.overrides[O];u({type:"CLEAR",id:O,...x?{clientMutationId:x.clientMutationId}:{}})}},[t,o]);let v=Oe.useCallback((O,x)=>{let b=i(),P=l.current.find(h=>h.id===x.id),L=p.current,ne=h=>{let ue=L.get(h);ue!==void 0&&(clearTimeout(ue),L.delete(h))},T=()=>{L.set(`fl:${b}`,setTimeout(()=>{L.delete(`fl:${b}`),c.current&&u({type:"CLEAR",id:x.id,clientMutationId:b})},r))};u({type:"SUBMIT",id:x.id,clientMutationId:b,start:x.start,end:x.end,...P?.revision!==void 0?{baseRevision:P.revision}:{},..."resourceId"in x&&x.resourceId!==void 0?{resourceId:x.resourceId}:{}}),L.set(`to:${b}`,setTimeout(()=>{L.delete(`to:${b}`),c.current&&(u({type:"TIMEOUT",id:x.id,clientMutationId:b}),T())},a));let _=()=>{ne(`to:${b}`),c.current&&(u({type:"REJECT",id:x.id,clientMutationId:b}),T())},z={kind:O,clientMutationId:b,payload:{...x,client_mutation_id:b}},Y;try{Y=n(z)}catch(h){Y=Promise.reject(h instanceof Error?h:new Error(String(h)))}Y.then(h=>{if(h.id!==x.id){_();return}ne(`to:${b}`),c.current&&u({type:"RESOLVE",id:h.id,clientMutationId:b,start:h.start,end:h.end,revision:h.revision,...h.resourceId!==void 0?{resourceId:h.resourceId}:{}})}).catch(_)},[n,a,r,i]),f=Oe.useMemo(()=>ln(t,o),[t,o]);return{events:f.events,pendingIds:f.pendingIds,rolledBackIds:f.rolledBackIds,submit:v}}import{jsx as ar}from"react/jsx-runtime";function nr({events:e,mutate:t,timeoutMs:n,rollbackFlashMs:a,generateId:r,...i}){let{events:o,pendingIds:u,rolledBackIds:l,submit:c}=Rn({events:e,mutate:t,...n!==void 0?{timeoutMs:n}:{},...a!==void 0?{rollbackFlashMs:a}:{},...r?{generateId:r}:{}});return ar(Vt,{...i,events:o,pendingIds:u,rolledBackIds:l,onEventDrop:p=>c("drop",p),onEventResize:p=>c("resize",p)})}export{Vt as AetherCalendar,Zn as CALENDAR_CSS,gn as CalendarNav,mn as DEFAULT_LOCALE_MESSAGES,nr as OptimisticCalendar,zt as PRESETS,Wn as PRESET_NAMES,sa as TIMELINE_CSS,na as TIME_GRID_CSS,wn as TimeGridView,Tn as TimelineView,qa as default,fn as defaultBaseTokenCss,vn as defaultTimeGridTokenCss,hn as defaultTimelineTokenCss,dt as ensureCalendarStyles,bn as ensureTimeGridStyles,En as ensureTimelineStyles,wt as getVisibleRange,Jn as isThemePreset,I as parseLocalDateTime,lt as resolveMessages,yn as resolveThemeVars,Et as stepAnchor,Rn as useOptimisticEvents};
