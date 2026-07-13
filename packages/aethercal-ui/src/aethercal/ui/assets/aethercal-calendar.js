function Ae(e){return String(e).padStart(2,"0")}function A(e){let t=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(e.trim());if(!t)throw new Error(`invalid ISO datetime: ${e}`);let[,n,a,r,i,o,u]=t,d=Number(n),c=Number(a),p=Number(r),f=Number(i??"0"),h=Number(o??"0"),M=Number(u??"0");if(c<1||c>12||p<1||p>31||f>23||h>59||M>59)throw new Error(`out-of-range ISO datetime: ${e}`);let x=new Date(d,c-1,p,f,h,M);if(x.getFullYear()!==d||x.getMonth()!==c-1||x.getDate()!==p)throw new Error(`nonexistent calendar date: ${e}`);return x}function j(e){return`${e.getFullYear()}-${Ae(e.getMonth()+1)}-${Ae(e.getDate())}T${Ae(e.getHours())}:${Ae(e.getMinutes())}:${Ae(e.getSeconds())}`}function pe(e){let t=A(e);return`${t.getFullYear()}-${Ae(t.getMonth()+1)}-${Ae(t.getDate())}`}function kn(e){return`${e.getFullYear()}-${Ae(e.getMonth()+1)}-${Ae(e.getDate())}`}function nt(e){let t=A(e.start),n=A(e.end),a=new Date(t.getFullYear(),t.getMonth(),t.getDate()),r=a;if(n.getTime()>t.getTime()){let i=new Date(n.getTime()-1),o=new Date(i.getFullYear(),i.getMonth(),i.getDate());o.getTime()>a.getTime()&&(r=o)}return{startKey:kn(a),lastKey:kn(r)}}function ya(e,t){return(e.getDay()-t+7)%7}function at(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate());return n.setDate(n.getDate()-ya(n,t)),n}function Yt(e,t){return Array.from({length:t},(n,a)=>{let r=new Date(e.getFullYear(),e.getMonth(),e.getDate()+a);return`${r.getFullYear()}-${Ae(r.getMonth()+1)}-${Ae(r.getDate())}`})}function Kt(e,t=1){return Yt(at(e,t),7)}function Wt(e,t=1){let n=new Date(e.getFullYear(),e.getMonth(),1);return Yt(at(n,t),42)}function Xt(e,t){return Yt(new Date(e.getFullYear(),e.getMonth(),e.getDate()),t)}function ke(e,t){let n=A(`${pe(e)}T00:00:00`),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()+t);return`${a.getFullYear()}-${Ae(a.getMonth()+1)}-${Ae(a.getDate())}`}function Jt(e,t){let n=new Date(e.getFullYear(),e.getMonth(),e.getDate()),a=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((a.getTime()-n.getTime())/864e5)}function mt(e,t){let n=A(e.start),a=A(e.end),r=A(t),i=Jt(n,r),o=new Date(n.getFullYear(),n.getMonth(),n.getDate()+i,n.getHours(),n.getMinutes(),n.getSeconds()),u=new Date(a.getFullYear(),a.getMonth(),a.getDate()+i,a.getHours(),a.getMinutes(),a.getSeconds()),d={id:e.id,start:j(o),end:j(u)};return e.revision!==void 0&&(d.revision=e.revision),d}var ha=370;function Sn(e){return String(e).padStart(2,"0")}function An(e){return`${e.getFullYear()}-${Sn(e.getMonth()+1)}-${Sn(e.getDate())}`}function ba(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate()+t)}function Da(e){let{startKey:t,lastKey:n}=nt(e),a=[],r=A(t);for(let i=0;i<ha&&An(r)<=n;i+=1)a.push(An(r)),r=ba(r,1);return{keys:a,startKey:t,lastKey:n}}function jt(e){let t=new Map;return e.forEach((n,a)=>{let{keys:r,startKey:i,lastKey:o}=Da(n),u=A(n.start).getTime(),d=A(n.end).getTime();for(let c of r){let p={entry:{event:n,isContinuation:c!==i,continuesAfter:c!==o},startMs:u,endMs:d,index:a},f=t.get(c);f?f.push(p):t.set(c,[p])}}),[...t.keys()].sort().map(n=>{let a=t.get(n);return a.sort((r,i)=>r.startMs-i.startMs||r.endMs-i.endMs||r.index-i.index),{date:n,entries:a.map(r=>r.entry)}})}function rt(e,t,n,a){let r=n*a;if(r<=0)return e;let i=Math.min(Math.max(e,0),r-1),o=i-i%a,u=Math.min(o+a-1,r-1);switch(t){case"ArrowLeft":return i>o?i-1:i;case"ArrowRight":return i<u?i+1:i;case"ArrowUp":{let d=i-a;return d>=0?d:i}case"ArrowDown":{let d=i+a;return d<r?d:i}case"Home":return o;case"End":return u;default:return i}}var it=60,Ne=15;function Zt(e,t,n){return Math.min(n,Math.max(t,e))}function Ct(e,t){let n=A(`${e}T00:00:00`);return new Date(n.getFullYear(),n.getMonth(),n.getDate(),0,t,0)}function Qt(e,t){return new Date(e.getFullYear(),e.getMonth(),e.getDate(),e.getHours(),e.getMinutes()+t,e.getSeconds())}function It(e,t){return t==null||(e.resourceId=t),e}function ot(e,t,n=Ne){let a=t.dayStartHour*it,r=t.dayEndHour*it,i=a+Zt(e,0,1)*t.windowMinutes,o=n>0?n:Ne,u=a+Math.round((i-a)/o)*o;return Zt(u,a,r)}function kt(e,t){return Zt(e,t.dayStartHour*it,t.dayEndHour*it)}var qt=24*it;function en(e,t,n,a){let r=t+n,i=e;for(;r<0;)r+=qt,i=ke(i,-1);for(;r>qt;)r-=qt,i=ke(i,1);return{dateOnly:i,minuteOfDay:kt(r,a)}}function st(e,t,n,a){if(n===null)return It(mt(e,t),a);let r=A(e.start),i=A(e.end),o=Ct(t,n),u=Jt(r,i),d=r.getHours()*it+r.getMinutes(),p=i.getHours()*it+i.getMinutes()-d,f=new Date(o.getFullYear(),o.getMonth(),o.getDate()+u,o.getHours(),o.getMinutes()+p,0),h={id:e.id,start:j(o),end:j(f)};return e.revision!==void 0&&(h.revision=e.revision),It(h,a)}function $e(e,t,n,a,r={}){let i=r.minDurationMinutes??Ne,o=A(e.start),u=A(e.end),d=Ct(n,a),c=o,p=u;if(t==="end"){let h=Qt(o,i);p=d.getTime()>=h.getTime()?d:h}else{let h=Qt(u,-i);c=d.getTime()<=h.getTime()?d:h}let f={id:e.id,start:j(c),end:j(p)};return e.revision!==void 0&&(f.revision=e.revision),f}function Xe(e,t,n={}){let a=n.minDurationMinutes??Ne;if(e.minuteOfDay===null||t.minuteOfDay===null){let[p,f]=e.dateOnly<=t.dateOnly?[e.dateOnly,t.dateOnly]:[t.dateOnly,e.dateOnly],h=A(`${p}T00:00:00`),M=A(`${f}T00:00:00`),x=new Date(M.getFullYear(),M.getMonth(),M.getDate()+1),b={start:j(h),end:j(x),allDay:!0};return It(b,e.resourceId)}let i=Ct(e.dateOnly,e.minuteOfDay??0),o=Ct(t.dateOnly,t.minuteOfDay??0),u=i.getTime()<=o.getTime()?i:o,d=i.getTime()<=o.getTime()?o:i;d.getTime()===u.getTime()&&(d=Qt(u,a));let c={start:j(u),end:j(d),allDay:!1};return It(c,e.resourceId)}var Ve=60,wa=24*Ve,Ea=864e5;function St(e,t,n){return Math.min(n,Math.max(t,e))}function bt(e={}){let t=e.dayStartHour,n=e.dayEndHour,a=Number.isFinite(t)&&t!==void 0?St(Math.trunc(t),0,23):0,r=Number.isFinite(n)&&n!==void 0?St(Math.trunc(n),1,24):24,[i,o]=r>a?[a,r]:[0,24];return{dayStartHour:i,dayEndHour:o,windowMinutes:(o-i)*Ve}}function On(e){let t=[],n=[];for(let a of e)a.allDay===!0?t.push(a):n.push(a);return{allDay:t,timed:n}}function lt(e,t){let n=A(e),a=new Date(n.getFullYear(),n.getMonth(),n.getDate()),r=Math.round((a.getTime()-t.getTime())/Ea),i=n.getHours()*Ve+n.getMinutes()+n.getSeconds()/60;return r*wa+i}function At(e,t){let n=e.map(d=>{let[c,p]=t(d);return{item:d,start:c,end:p}});n.sort((d,c)=>d.start!==c.start?d.start-c.start:c.end-d.end);let a=[],r=[],i=[],o=Number.NEGATIVE_INFINITY,u=()=>{let d=r.length;for(let c of i)a[c].laneCount=d;r=[],i=[],o=Number.NEGATIVE_INFINITY};for(let d of n){i.length>0&&d.start>=o&&u();let c=r.findIndex(p=>!(p.start<d.end&&d.start<p.end));c===-1?(c=r.length,r.push({start:d.start,end:d.end})):r[c]={start:d.start,end:d.end},i.push(a.length),a.push({item:d.item,lane:c,laneCount:1}),o=Math.max(o,d.end)}return u(),a}function Ln(e){return At(e,t=>[A(t.start).getTime(),A(t.end).getTime()])}function Dt(e,t,n){let a=A(`${t}T00:00:00`),r=n.dayStartHour*Ve,i=n.dayEndHour*Ve,o=e.filter(u=>{let d=lt(u.start,a);return!(lt(u.end,a)<=r||d>=i)});return Ln(o).map(({item:u,lane:d,laneCount:c})=>{let p=lt(u.start,a),f=lt(u.end,a),h=St(p,r,i),M=St(f,h,i),{startKey:x,lastKey:b}=nt(u);return{event:u,lane:d,laneCount:c,topFraction:(h-r)/n.windowMinutes,heightFraction:(M-h)/n.windowMinutes,isContinuation:t!==x,continuesAfter:t!==b}})}function xa(e){let t=[];for(let n=e.dayStartHour;n<e.dayEndHour;n+=1)t.push({hour:n,topFraction:(n-e.dayStartHour)*Ve/e.windowMinutes});return t}function tn(e,t,n={}){let a="windowMinutes"in n?n:bt(n),{allDay:r,timed:i}=On(t),o=i.map(d=>({event:d,startTs:A(d.start).getTime(),endTs:A(d.end).getTime()}));return{columns:e.map(d=>{let c=A(`${d}T00:00:00`),p=c.getTime(),f=new Date(c.getFullYear(),c.getMonth(),c.getDate()+1).getTime(),h=o.filter(x=>x.startTs>=f?!1:x.endTs>p?!0:x.startTs===x.endTs&&x.startTs>=p).map(x=>x.event),M=r.filter(x=>{let{startKey:b,lastKey:N}=nt(x);return b<=d&&d<=N});return{dateOnly:d,allDay:M,timed:Dt(h,d,a)}}),hourMarks:xa(a),config:a}}function nn(e,t={}){let n="windowMinutes"in t?t:bt(t),a=e.getHours()*Ve+e.getMinutes()+e.getSeconds()/60,r=n.dayStartHour*Ve,i=n.dayEndHour*Ve;return a<r||a>=i?null:(a-r)/n.windowMinutes}var Je=60,Ot=7,Pn=1,Nn=31;function wt(e,t,n){return Math.min(n,Math.max(t,e))}function je(e){return e===void 0||!Number.isFinite(e)?Ot:wt(Math.trunc(e),Pn,Nn)}function Lt(e){return"windowMinutes"in e?e:bt(e)}function Fn(e){if(e.allDay!==!0)return{start:e.start,end:e.end};let{startKey:t,lastKey:n}=nt(e);return{start:`${t}T00:00:00`,end:`${ke(n,1)}T00:00:00`}}function Ta(e,t,n){let a=n.dayStartHour*Je,r=n.dayEndHour*Je,i=[];return t.forEach((o,u)=>{let d=A(`${o}T00:00:00`),c=lt(e.start,d),p=lt(e.end,d);if(p<=a||c>=r)return;let f=wt(c,a,r),h=wt(p,f,r),M=u*n.windowMinutes;i.push({startMin:M+(f-a),endMin:M+(h-a),clippedStart:c<a,clippedEnd:p>r})}),i}function Ra(e){let t=[];for(let n of e){let a=t[t.length-1];a&&a.endMin===n.startMin?(a.endMin=n.endMin,a.clippedEnd=n.clippedEnd):t.push({...n})}return t}function Gn(e,t,n){let a=t.length*n.windowMinutes;if(a<=0)return[];let r=[];for(let o of e){let u=Ra(Ta(o,t,n));u.length>0&&r.push({item:o,runs:u})}return At(r,o=>[o.runs[0].startMin,o.runs[o.runs.length-1].endMin]).flatMap(({item:o,lane:u,laneCount:d})=>o.runs.map(c=>({event:o.item.event,lane:u,laneCount:d,leftFraction:c.startMin/a,widthFraction:(c.endMin-c.startMin)/a,allDay:o.item.event.allDay===!0,continuesBefore:c.clippedStart,continuesAfter:c.clippedEnd})))}function an(e,t,n={}){return Gn([{event:e,...Fn(e)}],t,Lt(n))}function rn(e,t,n,a={}){let r=Lt(a),i=new Set(a.collapsedGroupIds??[]),o=[],u=new Set;for(let R of e)u.has(R.id)||(u.add(R.id),o.push(R));let d=[],c=new Map;for(let R of o){let _=R.groupId?R.groupId:void 0;if(_===void 0){d.push({kind:"solo",resource:R});continue}let z=c.get(_);z?z.push(R):(c.set(_,[R]),d.push({kind:"group",id:_}))}let p=new Map,f=[];for(let R of t){let _={event:R,...Fn(R)},z=R.resourceId;if(z!==void 0&&u.has(z)){let Y=p.get(z);Y?Y.push(_):p.set(z,[_])}else f.push(_)}let h=(R,_,z)=>{let Y=Gn(z,n,r);return{resource:R,groupId:_,blocks:Y,laneCount:Y.reduce((v,ue)=>Math.max(v,ue.laneCount),1)}},M=[];for(let R of d){if(R.kind==="solo"){M.push({kind:"row",row:h(R.resource,null,p.get(R.resource.id)??[])});continue}let _=c.get(R.id)??[],z=i.has(R.id);if(M.push({kind:"group",group:{id:R.id,collapsed:z,resourceCount:_.length}}),!z)for(let Y of _)M.push({kind:"row",row:h(Y,R.id,p.get(Y.id)??[])})}let x=h(null,null,f);x.blocks.length>0&&M.push({kind:"row",row:x});let b=n.length,N=n.map((R,_)=>({dateOnly:R,leftFraction:b>0?_/b:0,widthFraction:b>0?1/b:0})),P=b*r.windowMinutes,ne=[];return P>0&&n.forEach((R,_)=>{let z=_*r.windowMinutes;for(let Y=r.dayStartHour;Y<r.dayEndHour;Y+=1){let v=(Y-r.dayStartHour)*Je;ne.push({dateOnly:R,hour:Y,leftFraction:(z+v)/P,isDayStart:Y===r.dayStartHour})}}),{days:[...n],items:M,dayHeaders:N,ticks:ne,config:r}}function dt(e,t,n={},a=Ne){let r=Lt(n);if(t.length===0||r.windowMinutes<=0)return null;let i=t.length*r.windowMinutes,o=wt(e,0,1)*i,u=Math.min(Math.floor(o/r.windowMinutes),t.length-1),d=o-u*r.windowMinutes,c=r.dayStartHour*Je,p=r.dayEndHour*Je,f=a>0?a:Ne,h=c+Math.round(d/f)*f;return{dateOnly:t[u],minuteOfDay:wt(h,c,p)}}function on(e,t,n={}){let a=Lt(n),r=t.indexOf(pe(j(e)));if(r===-1)return null;let i=e.getHours()*Je+e.getMinutes()+e.getSeconds()/60,o=a.dayStartHour*Je,u=a.dayEndHour*Je;if(i<o||i>=u)return null;let d=t.length*a.windowMinutes;return d<=0?null:(r*a.windowMinutes+(i-o))/d}var Ma=1;function Et(e,t,n=Ma,a){let r=t.getFullYear(),i=t.getMonth(),o=t.getDate(),u,d;switch(e){case"week":{u=at(t,n),d=new Date(u.getFullYear(),u.getMonth(),u.getDate()+7);break}case"day":{u=new Date(r,i,o),d=new Date(r,i,o+1);break}case"timeline":{u=new Date(r,i,o),d=new Date(r,i,o+je(a));break}default:{u=new Date(r,i,1),d=new Date(r,i+1,1);break}}return{view:e,from:j(u),to:j(d)}}function Pt(e,t,n,a){let r=e.getFullYear(),i=e.getMonth(),o=e.getDate();switch(t){case"week":return new Date(r,i,o+7*n);case"day":return new Date(r,i,o+n);case"timeline":return new Date(r,i,o+je(a)*n);default:return new Date(r,i+n,1)}}var Nt={status:"idle"};function Ft(e){return e.status==="dragging"}function sn(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"DROP":case"DRAG_CANCEL":return Nt}}var pt={status:"idle"};function xt(e,t){switch(t.type){case"DRAG_START":return{status:"dragging",eventId:t.eventId};case"RESIZE_START":return{status:"resizing",eventId:t.eventId,edge:t.edge};case"SELECT_START":return{status:"selecting",anchor:t.point,current:t.point};case"SELECT_MOVE":return e.status!=="selecting"?e:{status:"selecting",anchor:e.anchor,current:t.point};case"COMMIT":case"CANCEL":return pt}}var ln={overrides:{},appliedRevision:{}};function Ca(e,t){let n={...e};return delete n[t],n}function dn(e,t){switch(t.type){case"SUBMIT":{let n=t.baseRevision??Number.NEGATIVE_INFINITY,a=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;return{overrides:{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"pending",start:t.start,end:t.end,...t.baseRevision!==void 0?{revision:t.baseRevision}:{},...t.resourceId!==void 0?{resourceId:t.resourceId}:{}}},appliedRevision:{...e.appliedRevision,[t.id]:Math.max(a,n)}}}case"RESOLVE":{let n=e.appliedRevision[t.id]??Number.NEGATIVE_INFINITY;if(t.revision<=n)return e;let a=e.overrides[t.id],r=a!==void 0&&a.clientMutationId===t.clientMutationId&&a.status==="pending",i=t.resourceId??a?.resourceId;return{overrides:r?{...e.overrides,[t.id]:{clientMutationId:t.clientMutationId,status:"committed",start:t.start,end:t.end,revision:t.revision,...i!==void 0?{resourceId:i}:{}}}:e.overrides,appliedRevision:{...e.appliedRevision,[t.id]:t.revision}}}case"REJECT":case"TIMEOUT":{let n=e.overrides[t.id];return!n||n.clientMutationId!==t.clientMutationId||n.status!=="pending"?e:{...e,overrides:{...e.overrides,[t.id]:{...n,status:"rolledback"}}}}case"CLEAR":{let n=e.overrides[t.id];return!n||t.clientMutationId&&n.clientMutationId!==t.clientMutationId?e:{...e,overrides:Ca(e.overrides,t.id)}}}}function cn(e,t){let n=new Set,a=new Set,r=o=>o.resourceId!==void 0?{resourceId:o.resourceId}:void 0;return{events:e.map(o=>{let u=t.overrides[o.id];return u?u.status==="pending"?(n.add(o.id),{...o,start:u.start,end:u.end,...r(u)}):u.status==="rolledback"?(a.add(o.id),o):o.revision!==void 0&&u.revision!==void 0&&o.revision>=u.revision?o:{...o,start:u.start,end:u.end,...u.revision!==void 0?{revision:u.revision}:{},...r(u)}:o}),pendingIds:n,rolledBackIds:a}}function un(e,t){let n=new Map(e.map(r=>[r.id,r])),a=[];for(let[r,i]of Object.entries(t.overrides)){if(i.status!=="committed")continue;let o=n.get(r);o&&o.revision!==void 0&&i.revision!==void 0&&o.revision>=i.revision&&a.push(r)}return a}import*as Ge from"react";import*as Gt from"react";var gn=new Date(2023,0,1);function _n(e,t){let n=new Intl.DateTimeFormat(e,{weekday:"short"});return Array.from({length:7},(a,r)=>{let i=(t+r)%7,o=new Date(gn.getFullYear(),gn.getMonth(),gn.getDate()+i);return n.format(o)})}function mn(e,t){return new Intl.DateTimeFormat(t,{month:"long",year:"numeric"}).format(e)}function zn(e,t,n){let a=new Intl.DateTimeFormat(n,{month:"short",day:"numeric"}).format(e),r=new Intl.DateTimeFormat(n,{month:"short",day:"numeric",year:"numeric"}).format(t);return`${a} \u2013 ${r}`}function Hn(e,t,n,a,r=Ot){if(e==="day")return new Intl.DateTimeFormat(n,{dateStyle:"full"}).format(t);if(e==="week"){let i=at(t,a),o=new Date(i.getFullYear(),i.getMonth(),i.getDate()+6);return zn(i,o,n)}if(e==="timeline"){let i=je(r),o=new Date(t.getFullYear(),t.getMonth(),t.getDate()),u=new Date(o.getFullYear(),o.getMonth(),o.getDate()+i-1);return i===1?new Intl.DateTimeFormat(n,{dateStyle:"full"}).format(o):zn(o,u,n)}return mn(t,n)}function Tt(e,t){return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(A(e))}function De(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric",minute:"2-digit"}).format(A(e))}function $n(e,t){return new Intl.DateTimeFormat(t,{weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(A(e))}import{jsx as qe,jsxs as Un}from"react/jsx-runtime";function Ia(...e){return e.filter(Boolean).join(" ")}function ka(e,t,n){let{event:a,isContinuation:r,continuesAfter:i}=e;return a.allDay===!0?n.allDay:r?i?n.continues:n.endsAt(De(a.end,t)):De(a.start,t)}function Sa({entry:e,locale:t,messages:n}){let{event:a,isContinuation:r,continuesAfter:i}=e,o=ka(e,t,n),u=a.color?{"--ac-event-accent":a.color}:void 0;return Un("li",{className:Ia("aethercal-agenda-event",r&&"is-continuation"),"data-event-id":a.id,"aria-label":`${o} ${a.title}`,style:u,...a.allDay===!0?{"data-all-day":""}:{},...r?{"data-continuation":""}:{},...i?{"data-continues-after":""}:{},children:[qe("span",{className:"aethercal-agenda-event-time",children:o}),qe("span",{className:"aethercal-agenda-event-title",children:a.title})]})}function Vn({events:e,locale:t,messages:n,themeVars:a}){let r=Gt.useMemo(()=>jt(e),[e]),i=Gt.useId();return r.length===0?qe("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:qe("p",{className:"aethercal-agenda-empty",children:n.noEvents})}):qe("div",{className:"aethercal-calendar aethercal-agenda","data-view":"list",style:a,children:r.map(o=>{let u=`${i}-${o.date}`;return Un("section",{className:"aethercal-agenda-day",role:"group","aria-labelledby":u,"data-date":o.date,children:[qe("div",{className:"aethercal-agenda-day-title",id:u,children:$n(o.date,t)}),qe("ul",{className:"aethercal-agenda-day-events",role:"list",children:o.entries.map((d,c)=>qe(Sa,{entry:d,locale:t,messages:n},`${d.event.id}-${c}`))})]},o.date)})})}import{jsx as Ze,jsxs as Bn}from"react/jsx-runtime";var Aa=["month","week","day","list","timeline"];function pn({view:e,anchor:t,now:n,locale:a,firstDayOfWeek:r,timelineDays:i,messages:o,showViews:u=!0,onRangeChange:d,onViewChange:c}){let p=M=>{d?.(Et(e,M,r,i))},f=M=>Pt(t,e,M,i),h=Hn(e,t,a,r,i);return Bn("div",{className:"aethercal-nav",role:"toolbar","aria-label":o.navToolbar,children:[Bn("div",{className:"aethercal-nav-group",children:[Ze("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":o.navPrevious,onClick:()=>p(f(-1)),children:Ze("span",{"aria-hidden":"true",children:"\u2039"})}),Ze("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-today",onClick:()=>p(n),children:o.navToday}),Ze("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-arrow","aria-label":o.navNext,onClick:()=>p(f(1)),children:Ze("span",{"aria-hidden":"true",children:"\u203A"})})]}),Ze("span",{className:"aethercal-nav-title","aria-live":"polite",children:h}),u?Ze("div",{className:"aethercal-nav-views",children:Aa.map(M=>Ze("button",{type:"button",className:"aethercal-nav-btn aethercal-nav-view","aria-pressed":M===e,onClick:()=>c?.(Et(M,t,r,i)),children:o.viewNames[M]},M))}):null]})}var Oa={allDay:"All day",continues:"Continues",endsAt:e=>`ends ${e}`,more:e=>`+${e} more`,noEvents:"No events",unavailable:"This view is not available yet.",keyboardHint:"Use the arrow keys to move between days. Press Enter on an event to grab it, the arrow keys to move or resize it, Enter to drop, and Escape to cancel.",grabbedMoveHint:e=>`Grabbed ${e}. Use the arrow keys to move it, Enter to drop, Escape to cancel.`,grabbedResizeHint:e=>`Resizing ${e}. Use the up and down arrow keys to change its duration, Enter to confirm, Escape to cancel.`,movedTo:e=>`Moved to ${e}`,resizedTo:e=>`Duration ${e}`,dropped:e=>`Dropped on ${e}`,resized:e=>`Duration set to ${e}`,createHere:e=>`Create an event on ${e}`,cancelled:"Cancelled",navToolbar:"Calendar navigation",navPrevious:"Previous",navNext:"Next",navToday:"Today",viewNames:{month:"Month",week:"Week",day:"Day",list:"Agenda",timeline:"Timeline"},timelineResources:"Resources",timelineUnassigned:"Unassigned",timelineEmpty:"No resources to show",timelineGroupCount:e=>e===1?"1 resource":`${e} resources`,groupExpanded:e=>`${e} expanded`,groupCollapsed:e=>`${e} collapsed`,timelineKeyboardHint:"Use the up and down arrow keys to move between resources and the left and right arrow keys to move between days. Press Enter on a group to expand or collapse it, or on an event to grab it; then use the left and right arrow keys to change its time, the up and down arrow keys to move it to another resource, Enter to drop it, and Escape to cancel."},La={allDay:"Todo el d\xEDa",continues:"Contin\xFAa",endsAt:e=>`termina ${e}`,more:e=>`+${e} m\xE1s`,noEvents:"Sin eventos",unavailable:"Esta vista a\xFAn no est\xE1 disponible.",keyboardHint:"Usa las flechas para moverte entre los d\xEDas. Pulsa Enter sobre un evento para agarrarlo, las flechas para moverlo o cambiar su duraci\xF3n, Enter para soltarlo y Escape para cancelar.",grabbedMoveHint:e=>`Agarraste el evento ${e}. Usa las flechas para moverlo, Enter para soltarlo y Escape para cancelar.`,grabbedResizeHint:e=>`Est\xE1s cambiando la duraci\xF3n de ${e}. Usa las flechas hacia arriba y abajo para ajustarla, Enter para confirmar y Escape para cancelar.`,movedTo:e=>`Movido a ${e}`,resizedTo:e=>`Duraci\xF3n ${e}`,dropped:e=>`Soltado en ${e}`,resized:e=>`Duraci\xF3n establecida en ${e}`,createHere:e=>`Crear un evento en ${e}`,cancelled:"Cancelado",navToolbar:"Navegaci\xF3n del calendario",navPrevious:"Anterior",navNext:"Siguiente",navToday:"Hoy",viewNames:{month:"Mes",week:"Semana",day:"D\xEDa",list:"Agenda",timeline:"Cronograma"},timelineResources:"Recursos",timelineUnassigned:"Sin asignar",timelineEmpty:"No hay recursos para mostrar",timelineGroupCount:e=>e===1?"1 recurso":`${e} recursos`,groupExpanded:e=>`${e} desplegado`,groupCollapsed:e=>`${e} plegado`,timelineKeyboardHint:"Usa las flechas hacia arriba y abajo para moverte entre los recursos, y las flechas izquierda y derecha para moverte entre los d\xEDas. Pulsa Enter sobre un grupo para desplegarlo o plegarlo, o sobre un evento para agarrarlo; luego usa las flechas izquierda y derecha para cambiar su hora, las flechas hacia arriba y abajo para moverlo a otro recurso, Enter para soltarlo y Escape para cancelar."},fn={en:Oa,es:La};function Pa(e){return e.toLowerCase().split("-")[0]??""}function ct(e,t,n=fn){let a=e.toLowerCase(),r=n[a]??n[Pa(e)]??n.en??fn.en;return t?{...r,...t}:r}import*as Q from"react";import{jsx as Yn}from"react/jsx-runtime";function ft({message:e}){return Yn("div",{className:"aethercal-sr-only","aria-live":"polite","aria-atomic":"true",children:e})}function vt({id:e,text:t}){return Yn("div",{id:e,className:"aethercal-sr-only",children:t})}import{jsx as Kn,jsxs as Fa}from"react/jsx-runtime";function Na(...e){return e.filter(Boolean).join(" ")}function zt({event:e,timeLabel:t,onDragStart:n,onDragEnd:a,canDrag:r=!0,isPending:i,isRolledBack:o,onClick:u,onContextMenu:d,id:c,interactive:p,isActive:f,isGrabbed:h}){let M=e.editable!==!1,x=M&&r,b=e.color?{"--ac-event-accent":e.color}:void 0,N=t?`${t} ${e.title}`:e.title;return Fa("div",{className:Na("aethercal-event",!M&&"is-locked",i&&"is-pending",o&&"is-rolledback",f&&"is-active",h&&"is-grabbed"),...c?{id:c}:{},...p?{role:"button"}:{},draggable:x,"data-event-id":e.id,"aria-label":N,title:e.title,style:b,onDragStart:P=>{if(!x){P.preventDefault();return}P.dataTransfer.setData("text/plain",e.id),P.dataTransfer.effectAllowed="move",n(e.id)},onDragEnd:a,onClick:u,onContextMenu:d?P=>{P.preventDefault(),P.stopPropagation(),d()}:void 0,children:[t?Kn("time",{className:"aethercal-event-time",children:t}):null,t?" ":null,Kn("span",{className:"aethercal-event-title",children:e.title})]})}import{Fragment as Ha,jsx as ze,jsxs as _t}from"react/jsx-runtime";var Wn=new Set,yt=7,Xn=6;function Jn(...e){return e.filter(Boolean).join(" ")}function Ga(e){let t=[];for(let n=0;n<e.length;n+=yt)t.push(e.slice(n,n+yt));return t}function za(e){let t=new Map;for(let n of e){let a=pe(n.start),r=t.get(a);r?r.push(n):t.set(a,[n])}return t}function _a(e){return{start:`${e}T00:00:00`,end:`${ke(e,1)}T00:00:00`,allDay:!0}}function jn(e){let{events:t,anchor:n,locale:a,firstDayOfWeek:r,messages:i,weekdayLabels:o,maxEventsPerDay:u,themeVars:d,onEventDrop:c,onRangeSelect:p,onEventClick:f,onContextMenu:h,pendingIds:M=Wn,rolledBackIds:x=Wn}=e,b=Q.useMemo(()=>Wt(n,r),[n,r]),N=Q.useMemo(()=>Ga(b),[b]),P=Q.useMemo(()=>o??_n(a,r),[o,a,r]),ne=Q.useMemo(()=>za(t),[t]),R=n.getMonth(),_=pe(j(new Date)),z=Q.useMemo(()=>pe(j(n)),[n]),[Y,v]=Q.useReducer(sn,Nt),[ue,fe]=Q.useState(()=>new Set),le=Q.useId(),[q,we]=Q.useState(z),[ae,W]=Q.useState(null),[B,ce]=Q.useState(null),[xe,Te]=Q.useState("");Q.useEffect(()=>{b.includes(q)||(we(z),W(null),ce(null))},[b,q,z]);let ge=Q.useCallback(G=>!!f||G.editable!==!1&&!!c,[f,c]);Q.useEffect(()=>{let G=new Set((ne.get(q)??[]).filter(L=>ge(L)).map(L=>L.id));B&&!G.has(B.eventId)?(ce(null),W(null)):!B&&ae!==null&&!G.has(ae)&&W(null)},[ne,q,ae,B,ge]);let Ce=G=>`${le}-c-${G}`,Le=(G,L)=>`${le}-e-${G}-${L}`,ee=`${le}-hint`,K=B?Le(q,B.eventId):ae?Le(q,ae):Ce(q),Ie=Q.useCallback(G=>{fe(L=>{let V=new Set(L);return V.add(G),V})},[]),me=Q.useCallback(G=>L=>{if(L.preventDefault(),!Ft(Y)){v({type:"DROP"});return}let V=Y.eventId,de=L.dataTransfer.getData("text/plain");if(v({type:"DROP"}),de&&de!==V||!c)return;let ie=t.find(Z=>Z.id===V);!ie||ie.editable===!1||c(mt(ie,G))},[Y,t,c]),re=!!c,J=Q.useCallback(G=>{if(!B)return;let L=ke(B.targetDate,G),V=b[0],de=b[b.length-1];L<V||L>de||(Te(i.movedTo(Tt(L,a))),ce({...B,targetDate:L,moved:!0}))},[B,b,a,i]),Re=Q.useCallback(()=>{if(!B)return;if(!B.moved){W(B.eventId),ce(null);return}let G=t.find(L=>L.id===B.eventId);G&&G.editable!==!1&&c&&(c(mt(G,B.targetDate)),Te(i.dropped(Tt(B.targetDate,a)))),we(B.targetDate),W(null),ce(null)},[B,t,c,i,a]),Pe={ArrowLeft:-1,ArrowRight:1,ArrowUp:-yt,ArrowDown:yt},ve=Q.useCallback(G=>{let{key:L}=G,V=L==="Enter"||L===" "||L==="Spacebar";if(B){if(L in Pe){G.preventDefault(),J(Pe[L]);return}if(V){G.preventDefault(),Re();return}if(L==="Escape"){G.preventDefault(),ce(null),Te(i.cancelled);return}return}let de=ne.get(q)??[],ie=de.filter(Z=>ge(Z));if(ae){let Z=ie.findIndex(X=>X.id===ae);if(L==="ArrowDown"){G.preventDefault(),Z>=0&&Z<ie.length-1&&W(ie[Z+1].id);return}if(L==="ArrowUp"){G.preventDefault(),Z>0?W(ie[Z-1].id):W(null);return}if(V){G.preventDefault();let X=ie.find(ye=>ye.id===ae);if(!X)return;X.editable!==!1&&c?(ce({eventId:X.id,targetDate:q,moved:!1}),Te(i.grabbedMoveHint(X.title))):f&&f({id:X.id});return}if(L==="Escape"){G.preventDefault(),W(null);return}if(L==="ArrowLeft"||L==="ArrowRight"||L==="Home"||L==="End"){G.preventDefault(),W(null);let X=rt(b.indexOf(q),L,Xn,yt);we(b[X]);return}return}if(L in Pe||L==="Home"||L==="End"){G.preventDefault();let Z=rt(b.indexOf(q),L,Xn,yt);we(b[Z]);return}V&&(ie.length>0?(G.preventDefault(),Ie(q),W(ie[0].id)):de.length===0&&p&&(G.preventDefault(),p(_a(q)),Te(i.createHere(Tt(q,a)))))},[B,ae,q,b,ne,ge,c,f,p,J,Re,Ie,i,a,Pe]);return _t(Ha,{children:[_t("div",{className:Jn("aethercal-calendar",Ft(Y)&&"is-dragging"),role:"grid","aria-label":mn(n,a),"aria-describedby":ee,"aria-activedescendant":K,tabIndex:0,"data-view":"month",style:d,onKeyDown:ve,children:[ze("div",{className:"aethercal-weekdays",role:"row",children:P.map((G,L)=>ze("div",{role:"columnheader",className:"aethercal-weekday",children:G},L))}),N.map((G,L)=>ze("div",{className:"aethercal-week",role:"row",children:G.map(V=>{let de=ne.get(V)??[],ie=ue.has(V),Z=ie?de:de.slice(0,u),X=de.length-Z.length,ye=new Date(`${V}T00:00:00`).getMonth()!==R,he=V===_,oe=!ae&&!B&&V===q,Se=B?.targetDate===V;return _t("div",{id:Ce(V),role:"gridcell",className:Jn("aethercal-day",ye&&"is-outside",he&&"is-today",oe&&"is-active",Se&&"is-drop-target"),"data-date":V,onDragOver:re?te=>te.preventDefault():void 0,onDrop:re?me(V):void 0,onContextMenu:h?te=>{te.target.closest("[data-event-id], button")||(te.preventDefault(),h({start:`${V}T00:00:00`}))}:void 0,children:[ze("span",{className:"aethercal-sr-only",children:Tt(V,a)}),ze("div",{className:"aethercal-day-head",children:ze("span",{className:"aethercal-day-number","aria-hidden":"true",children:Number(V.slice(-2))})}),_t("div",{className:"aethercal-day-events",children:[Z.map(te=>{let et=B?.eventId===te.id||!B&&ae===te.id;return ze(zt,{id:Le(V,te.id),event:te,interactive:ge(te),isActive:et,isGrabbed:B?.eventId===te.id,timeLabel:te.allDay?null:De(te.start,a),canDrag:re,onDragStart:l=>v({type:"DRAG_START",eventId:l}),onDragEnd:()=>v({type:"DRAG_CANCEL"}),isPending:M.has(te.id),isRolledBack:x.has(te.id),...f?{onClick:()=>f({id:te.id})}:{},...h?{onContextMenu:()=>h({id:te.id})}:{}},te.id)}),X>0&&!ie?ze("button",{type:"button",className:"aethercal-more",onClick:()=>Ie(V),children:i.more(X)}):null]})]},V)})},L))]}),ze(vt,{id:ee,text:i.keyboardHint}),ze(ft,{message:xe})]})}var qn={light:{"--ac-fg":"#1f2328","--ac-muted":"#5f6672","--ac-faint":"#676e79","--ac-bg":"#ffffff","--ac-header-fg":"#4b5563","--ac-border":"#e5e7eb","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#fafafa","--ac-today-marker-bg":"#111827","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#eef1f4","--ac-event-fg":"#1f2328","--ac-event-accent":"#64748b","--ac-more-fg":"#4b5563","--ac-focus":"#2563eb","--ac-rollback":"#b91c1c","--ac-tg-now":"#dc2626"},dark:{"--ac-fg":"#e6e8eb","--ac-muted":"#9aa1ab","--ac-faint":"#868e99","--ac-bg":"#14161a","--ac-header-fg":"#b3b9c2","--ac-border":"#2a2e35","--ac-cell-bg":"#171a1f","--ac-cell-bg-outside":"#111318","--ac-today-marker-bg":"#e6e8eb","--ac-today-marker-fg":"#14161a","--ac-event-bg":"#242a32","--ac-event-fg":"#e6e8eb","--ac-event-accent":"#8b98a9","--ac-more-fg":"#b3b9c2","--ac-focus":"#6ea8fe","--ac-rollback":"#f87171","--ac-tg-now":"#f87171"},midnight:{"--ac-fg":"#dfe4ea","--ac-muted":"#8b95a1","--ac-faint":"#828a95","--ac-bg":"#0b0f14","--ac-header-fg":"#a7b0bd","--ac-border":"#1c232c","--ac-cell-bg":"#0e131a","--ac-cell-bg-outside":"#090d12","--ac-today-marker-bg":"#dfe4ea","--ac-today-marker-fg":"#0b0f14","--ac-event-bg":"#17212c","--ac-event-fg":"#dfe4ea","--ac-event-accent":"#7f8ea3","--ac-more-fg":"#a7b0bd","--ac-focus":"#74a9ff","--ac-rollback":"#fb7185","--ac-tg-now":"#fb7185"},high_contrast:{"--ac-fg":"#000000","--ac-muted":"#000000","--ac-faint":"#1a1a1a","--ac-bg":"#ffffff","--ac-header-fg":"#000000","--ac-border":"#000000","--ac-cell-bg":"#ffffff","--ac-cell-bg-outside":"#ffffff","--ac-today-marker-bg":"#000000","--ac-today-marker-fg":"#ffffff","--ac-event-bg":"#e0e0e0","--ac-event-fg":"#000000","--ac-event-accent":"#000000","--ac-more-fg":"#000000","--ac-focus":"#0033cc","--ac-rollback":"#b00000","--ac-tg-now":"#d00000"}};var Ht=qn,Zn=["light","dark","midnight","high_contrast"],Va=new Set(Zn),Ua={"--ac-font":'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',"--ac-radius":"8px","--ac-cell-min-height":"96px"},Ba={"--ac-tg-gutter":"56px","--ac-tg-body-height":"640px","--ac-tg-hour-min-height":"44px","--ac-tg-line":"var(--ac-border)","--ac-tg-event-bg":"var(--ac-event-bg)","--ac-tg-event-fg":"var(--ac-event-fg)","--ac-tg-event-accent":"var(--ac-event-accent)"},Ya={"--ac-tl-rowhead-width":"168px","--ac-tl-lane-height":"30px","--ac-tl-body-height":"560px","--ac-tl-line":"var(--ac-border)","--ac-tl-event-bg":"var(--ac-event-bg)","--ac-tl-event-fg":"var(--ac-event-fg)","--ac-tl-event-accent":"var(--ac-event-accent)","--ac-tl-group-bg":"var(--ac-cell-bg-outside)","--ac-tl-now":"var(--ac-tg-now)"},Qn=["--ac-tg-now"],Ka=/[;{}<>]/;function ea(e){return typeof e=="string"&&Va.has(e)}function vn(e){return Object.entries(e).map(([t,n])=>`  ${t}: ${n};`).join(`
`)}function Wa(){let e={};for(let[t,n]of Object.entries(Ht.light))Qn.includes(t)||(e[t]=n);return e}function ta(){let e={};for(let t of Qn){let n=Ht.light[t];n!==void 0&&(e[t]=n)}return e}function yn(){return vn({...Ua,...Wa()})}function hn(){return vn({...Ba,...ta()})}function bn(){return vn({...Ya,...ta()})}function Xa(e){let t={};for(let[n,a]of Object.entries(e))n.startsWith("--ac-")&&(typeof a!="string"||a.trim()===""||Ka.test(a)||(t[n]=a));return t}function Dn(e){return e===void 0?{}:typeof e=="string"?ea(e)?{...Ht[e]}:{}:Xa(e)}var na="aethercal-calendar-styles",aa=`
:where(.aethercal-calendar, .aethercal-calendar-shell) {
${yn()}
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
`;function ut(){if(typeof document>"u"||document.getElementById(na))return;let e=document.createElement("style");e.id=na,e.textContent=aa,document.head.appendChild(e)}import*as $ from"react";function Fe(e,t){return new Intl.DateTimeFormat(t,{weekday:"short",day:"numeric"}).format(A(e))}function ra(e,t){return new Intl.DateTimeFormat(t,{hour:"numeric"}).format(new Date(2001,0,1,e))}function ia(e,t){if(e.length===0)return"";let n=A(e[0]);if(e.length===1)return new Intl.DateTimeFormat(t,{dateStyle:"full"}).format(n);let a=A(e[e.length-1]),r=new Intl.DateTimeFormat(t,{month:"short",day:"numeric",year:"numeric"});return`${r.format(n)} \u2013 ${r.format(a)}`}var oa="aethercal-timegrid-styles",sa=`
:where(.aethercal-timegrid) {
${hn()}
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
`;function wn(){if(typeof document>"u"||document.getElementById(oa))return;let e=document.createElement("style");e.id=oa,e.textContent=sa,document.head.appendChild(e)}import{Fragment as ca,jsx as Ee,jsxs as Qe}from"react/jsx-runtime";function $t(...e){return e.filter(Boolean).join(" ")}var Ue=e=>`${e*100}%`,la=new Set;function da(e){let t=A(e);return t.getHours()*60+t.getMinutes()}function En(e,t,n){let a=A(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}function Ja(e,t,n,a){let{event:r,isContinuation:i,continuesAfter:o}=e;return i?o?n:a(De(r.end,t)):De(r.start,t)}function Vt(e,t){let n=t.getBoundingClientRect();return n.height>0?(e-n.top)/n.height:0}function xn(e){let{view:t,days:n,events:a,locale:r,config:i,now:o,themeVars:u,onEventDrop:d,onEventResize:c,onRangeSelect:p,onEventClick:f,onContextMenu:h,pendingIds:M=la,rolledBackIds:x=la}=e,b=$.useMemo(()=>{if(e.messages)return e.messages;let l={...e.allDayLabel!==void 0?{allDay:e.allDayLabel}:{},...e.continuesLabel!==void 0?{continues:e.continuesLabel}:{},...e.formatEndsLabel!==void 0?{endsAt:e.formatEndsLabel}:{}};return ct(r,l)},[e.messages,e.allDayLabel,e.continuesLabel,e.formatEndsLabel,r]);$.useEffect(()=>{ut(),wn()},[]);let N=$.useMemo(()=>tn(n,a,i),[n,a,i]),P=$.useMemo(()=>nn(o,i),[o,i]),ne=$.useMemo(()=>pe(j(o)),[o]),[R,_]=$.useReducer(xt,pt),z=$.useRef(null),[Y,v]=$.useState(null),[ue,fe]=$.useState(null),le=!!d,q=!!c,we=!!p,ae=R.status==="dragging",W=$.useCallback((l,g)=>D=>{if(D.preventDefault(),R.status!=="dragging"){_({type:"COMMIT"});return}let I=R.eventId,k=D.dataTransfer.getData("text/plain");if(_({type:"COMMIT"}),k&&k!==I||!d)return;let T=a.find(Me=>Me.id===I);if(!T||T.editable===!1)return;let w=null;if(g&&T.allDay!==!0){let be=D.currentTarget.getBoundingClientRect();be.height>0&&Number.isFinite(D.clientY)&&(w=ot((D.clientY-be.top)/be.height,N.config))}d(st(T,l,w))},[R,a,d,N.config]),B=$.useCallback(l=>{z.current?.kind!=="resize"&&_({type:"DRAG_START",eventId:l})},[]),ce=$.useCallback(()=>_({type:"CANCEL"}),[]),xe=$.useCallback((l,g)=>D=>{if(!c||l.editable===!1||D.button!==0||z.current)return;let I=D.currentTarget.closest(".aethercal-tg-col");I?.dataset.date&&(D.preventDefault(),D.stopPropagation(),z.current={kind:"resize",pointerId:D.pointerId,eventId:l.id,edge:g,dateOnly:I.dataset.date,colEl:I,payload:null},D.currentTarget.setPointerCapture?.(D.pointerId),_({type:"RESIZE_START",eventId:l.id,edge:g}))},[c]),Te=$.useCallback(l=>g=>{if(!p||g.button!==0||z.current||g.target.closest("[data-event-id], button"))return;let D=g.currentTarget,I=ot(Vt(g.clientY,D),N.config);z.current={kind:"select",pointerId:g.pointerId,anchorDate:l,anchorCol:D,anchorMinute:I,currentDate:l,currentCol:D,currentMinute:I},D.setPointerCapture?.(g.pointerId),_({type:"SELECT_START",point:{dateOnly:l,minuteOfDay:I}})},[p,N.config]),ge=R.status==="resizing"||R.status==="selecting";$.useLayoutEffect(()=>{if(!ge)return;let l=T=>{let w=z.current;if(!(!w||T.pointerId!==w.pointerId))if(w.kind==="resize"){let Me=document.elementFromPoint(T.clientX,T.clientY)?.closest(".aethercal-tg-col"),be=Me?.dataset.date?Me:w.colEl,Ye=ot(Vt(T.clientY,be),N.config),tt=a.find(m=>m.id===w.eventId);if(!tt)return;let s=$e(tt,w.edge,be.dataset.date??w.dateOnly,Ye);w.payload=s,v(s)}else{let Me=document.elementFromPoint(T.clientX,T.clientY)?.closest(".aethercal-tg-col"),be=Me?.dataset.date?Me:w.currentCol;w.currentCol=be,w.currentDate=be.dataset.date??w.anchorDate,w.currentMinute=ot(Vt(T.clientY,be),N.config);let Ye=Xe({dateOnly:w.anchorDate,minuteOfDay:w.anchorMinute},{dateOnly:w.currentDate,minuteOfDay:w.currentMinute}),s=(w.currentDate===w.anchorDate?Dt([{id:"__sel",title:"",start:Ye.start,end:Ye.end}],w.anchorDate,N.config):[])[0];fe(s?{dateOnly:w.anchorDate,topFraction:s.topFraction,heightFraction:s.heightFraction}:null)}},g=T=>{let w=z.current;z.current=null,v(null),fe(null),T&&w&&(w.kind==="resize"&&w.payload&&c&&c(w.payload),w.kind==="select"&&p&&(w.currentDate!==w.anchorDate||w.currentMinute!==w.anchorMinute)&&p(Xe({dateOnly:w.anchorDate,minuteOfDay:w.anchorMinute},{dateOnly:w.currentDate,minuteOfDay:w.currentMinute}))),_({type:T?"COMMIT":"CANCEL"})},D=T=>{z.current&&T.pointerId!==z.current.pointerId||g(!0)},I=T=>{z.current&&T.pointerId!==z.current.pointerId||g(!1)},k=T=>{T.key==="Escape"&&g(!1)};return window.addEventListener("pointermove",l),window.addEventListener("pointerup",D),window.addEventListener("pointercancel",I),window.addEventListener("keydown",k),()=>{window.removeEventListener("pointermove",l),window.removeEventListener("pointerup",D),window.removeEventListener("pointercancel",I),window.removeEventListener("keydown",k)}},[ge,a,N.config,c,p]);let Ce=$.useCallback((l,g)=>D=>{if(!h||D.target.closest("[data-event-id], button"))return;if(D.preventDefault(),!g){h({start:`${l}T00:00:00`});return}let I=ot(Vt(D.clientY,D.currentTarget),N.config),k=A(`${l}T00:00:00`),T=new Date(k.getFullYear(),k.getMonth(),k.getDate(),0,I,0);h({start:j(T)})},[h,N.config]),Le=$.useId(),ee=$.useMemo(()=>N.columns.map(l=>l.dateOnly),[N.columns]),[K,Ie]=$.useState(()=>(ee.includes(ne)?ne:ee[0])??""),[me,re]=$.useState(null),[J,Re]=$.useState(null),[Pe,ve]=$.useState("");$.useEffect(()=>{ee.includes(K)||(Ie(ee[0]??""),re(null),Re(null))},[ee,K]);let G=l=>`${Le}-col-${l}`,L=(l,g)=>`${Le}-e-${l}-${g}`,V=`${Le}-hint`,de=Ne,ie=$.useCallback(l=>!!f||l.editable!==!1&&!!(d||c),[f,d,c]),Z=$.useMemo(()=>{let l=N.columns.find(g=>g.dateOnly===K);return l?[...l.allDay,...l.timed.map(g=>g.event)]:[]},[N.columns,K]),X=$.useMemo(()=>Z.filter(l=>ie(l)),[Z,ie]);$.useEffect(()=>{let l=new Set(X.map(g=>g.id));J&&!l.has(J.eventId)?(Re(null),re(null)):!J&&me!==null&&!l.has(me)&&re(null)},[X,me,J]);let ye=J?L(K,J.eventId):me?L(K,me):G(K),he=$.useCallback(l=>{let g=J;if(!g)return;let D=g.dateOnly,I=g.minute,k=a.find(w=>w.id===g.eventId),T=k?.allDay===!0;if(!T&&(l==="ArrowUp"||l==="ArrowDown")){let w=en(D,I,l==="ArrowUp"?-de:de,N.config);D=w.dateOnly,I=w.minuteOfDay}else l==="ArrowLeft"?D=ke(D,-1):l==="ArrowRight"&&(D=ke(D,1));if(!(D===g.dateOnly&&I===g.minute)){if(k)if(g.kind==="move")ve(b.movedTo(T?Fe(D,r):`${Fe(D,r)} ${En(D,I,r)}`));else{let w=$e(k,"end",D,I);ve(b.resizedTo(`${De(w.start,r)} \u2013 ${De(w.end,r)}`))}Re({...g,dateOnly:D,minute:I,moved:!0})}},[J,de,N.config,a,b,r]),oe=$.useCallback(()=>{let l=J;if(!l)return;if(!l.moved){re(l.eventId),Re(null);return}let g=a.find(D=>D.id===l.eventId);if(g&&g.editable!==!1&&l.kind==="move"&&d){let D=st(g,l.dateOnly,g.allDay===!0?null:l.minute);d(D);let I=pe(D.start);Ie(ee.includes(I)?I:K),re(null),ve(b.dropped(g.allDay===!0?Fe(l.dateOnly,r):En(l.dateOnly,l.minute,r)))}else if(g&&g.editable!==!1&&l.kind==="resize"&&c){let D=$e(g,"end",l.dateOnly,l.minute);c(D),re(l.eventId),ve(b.resized(`${De(D.start,r)} \u2013 ${De(D.end,r)}`))}else re(l.eventId);Re(null)},[J,a,d,c,ee,K,b,r]),Se=$.useCallback(l=>{let{key:g}=l,D=g==="Enter"||g===" "||g==="Spacebar",I=g==="ArrowUp"||g==="ArrowDown"||g==="ArrowLeft"||g==="ArrowRight";if(J){if(I){l.preventDefault(),he(g);return}if(D){l.preventDefault(),oe();return}if(g==="Escape"){l.preventDefault(),Re(null),ve(b.cancelled);return}return}if(me){let k=X.findIndex(T=>T.id===me);if(g==="ArrowDown"){l.preventDefault(),k>=0&&k<X.length-1&&re(X[k+1].id);return}if(g==="ArrowUp"){l.preventDefault(),k>0?re(X[k-1].id):re(null);return}if(g==="ArrowLeft"||g==="ArrowRight"){l.preventDefault(),re(null);let T=ee.indexOf(K);Ie(ee[rt(T,g,1,ee.length)]);return}if(D){l.preventDefault();let T=X.find(w=>w.id===me);if(!T)return;T.editable!==!1&&d?(Re({kind:"move",eventId:T.id,dateOnly:pe(T.start),minute:da(T.start),moved:!1}),ve(b.grabbedMoveHint(T.title))):f&&f({id:T.id});return}if((g==="r"||g==="R")&&c){l.preventDefault();let T=X.find(w=>w.id===me);T&&T.allDay!==!0&&T.editable!==!1&&(Re({kind:"resize",eventId:T.id,dateOnly:pe(T.end),minute:da(T.end),moved:!1}),ve(b.grabbedResizeHint(T.title)));return}if(g==="Escape"){l.preventDefault(),re(null);return}return}if(g==="ArrowLeft"||g==="ArrowRight"||g==="Home"||g==="End"){l.preventDefault();let k=ee.indexOf(K);Ie(ee[rt(k,g,1,ee.length)]);return}if(g==="ArrowDown"){X.length>0&&(l.preventDefault(),re(X[0].id));return}if(D){if(X.length>0)l.preventDefault(),re(X[0].id);else if(Z.length===0&&p){let k=N.config.dayEndHour*60,T=kt(N.config.dayStartHour*60,N.config),w=Math.min(T+60,k);w>T&&(l.preventDefault(),p(Xe({dateOnly:K,minuteOfDay:T},{dateOnly:K,minuteOfDay:w})),ve(b.createHere(`${Fe(K,r)} ${En(K,T,r)}`)))}}},[J,me,K,Z,X,ee,d,c,f,p,he,oe,N.config,b,r]),te={"--ac-tg-cols":N.columns.length,"--ac-tg-hours":N.config.dayEndHour-N.config.dayStartHour,...u??{}},et=b.allDay;return Qe(ca,{children:[Qe("div",{className:$t("aethercal-calendar","aethercal-timegrid",ae&&"is-dragging",R.status==="resizing"&&"is-resizing",R.status==="selecting"&&"is-selecting"),role:"grid","aria-label":ia(n,r),"aria-describedby":V,"aria-activedescendant":ye,tabIndex:0,"data-view":t,style:te,onKeyDown:Se,children:[Qe("div",{className:"aethercal-tg-head",role:"row",children:[Ee("div",{className:"aethercal-tg-corner"}),N.columns.map(l=>Ee("div",{role:"columnheader",className:$t("aethercal-tg-colhead",l.dateOnly===ne&&"is-today"),"data-date":l.dateOnly,children:Ee("span",{className:"aethercal-tg-colhead-date",children:Fe(l.dateOnly,r)})},l.dateOnly))]}),Qe("div",{className:"aethercal-tg-allday",role:"row",children:[Ee("div",{className:"aethercal-tg-rowhead",role:"rowheader",children:et}),N.columns.map(l=>Ee("div",{role:"gridcell",className:"aethercal-tg-allday-cell","data-date":l.dateOnly,onDragOver:le?g=>g.preventDefault():void 0,onDrop:le?W(l.dateOnly,!1):void 0,onContextMenu:h?Ce(l.dateOnly,!1):void 0,children:l.allDay.map(g=>{let D=J?.eventId===g.id&&l.dateOnly===K||!J&&me===g.id&&l.dateOnly===K;return Ee(zt,{id:L(l.dateOnly,g.id),event:g,interactive:ie(g),isActive:D,isGrabbed:J?.eventId===g.id&&l.dateOnly===K,timeLabel:null,canDrag:le,onDragStart:B,onDragEnd:ce,isPending:M.has(g.id),isRolledBack:x.has(g.id),...f?{onClick:()=>f({id:g.id})}:{},...h?{onContextMenu:()=>h({id:g.id})}:{}},g.id)})},l.dateOnly))]}),Qe("div",{className:"aethercal-tg-body",role:"row",tabIndex:0,children:[Ee("div",{className:"aethercal-tg-gutter",role:"presentation","aria-hidden":"true",children:N.hourMarks.map(l=>Ee("div",{className:"aethercal-tg-hour",style:{top:Ue(l.topFraction)},children:ra(l.hour,r)},l.hour))}),N.columns.map(l=>{let g=!me&&!J&&l.dateOnly===K,D=J?.dateOnly===l.dateOnly;return Qe("div",{id:G(l.dateOnly),role:"gridcell",className:$t("aethercal-tg-col",l.dateOnly===ne&&"is-today",g&&"is-active",D&&"is-drop-target"),"data-date":l.dateOnly,onDragOver:le?I=>I.preventDefault():void 0,onDrop:le?W(l.dateOnly,!0):void 0,onPointerDown:we?Te(l.dateOnly):void 0,onContextMenu:h?Ce(l.dateOnly,!0):void 0,children:[N.hourMarks.map(I=>Ee("div",{className:"aethercal-tg-line",style:{top:Ue(I.topFraction)},"aria-hidden":"true"},I.hour)),ue&&ue.dateOnly===l.dateOnly?Ee("div",{className:"aethercal-tg-select-band",style:{top:Ue(ue.topFraction),height:Ue(ue.heightFraction)},"aria-hidden":"true"}):null,l.timed.map(I=>{let{event:k}=I,T=k.editable!==!1,w=Ja(I,r,b.continues,b.endsAt),Me=Y?.id===k.id?Y:null,be=Me?Dt([{...k,start:Me.start,end:Me.end}],l.dateOnly,N.config)[0]:void 0,Ye=be?be.topFraction:I.topFraction,tt=be?be.heightFraction:I.heightFraction,s=J?.eventId===k.id&&l.dateOnly===K||!J&&me===k.id&&l.dateOnly===K,m=J?.eventId===k.id&&l.dateOnly===K,S={top:Ue(Ye),height:Ue(tt),left:Ue(I.lane/I.laneCount),width:Ue(1/I.laneCount),...k.color?{"--ac-tg-event-accent":k.color}:{}};return Qe("div",{id:L(l.dateOnly,k.id),className:$t("aethercal-tg-event",!T&&"is-locked",M.has(k.id)&&"is-pending",x.has(k.id)&&"is-rolledback",!!Me&&"is-resizing",s&&"is-active",m&&"is-grabbed"),...ie(k)?{role:"button"}:{},draggable:T&&le,"data-event-id":k.id,"data-lane":I.lane,"data-lane-count":I.laneCount,"aria-label":`${w} ${k.title}`,title:k.title,style:S,onDragStart:E=>{if(!le||z.current?.kind==="resize"){E.preventDefault();return}E.dataTransfer.setData("text/plain",k.id),E.dataTransfer.effectAllowed="move",B(k.id)},onDragEnd:ce,onClick:f?()=>f({id:k.id}):void 0,onContextMenu:h?E=>{E.preventDefault(),E.stopPropagation(),h({id:k.id})}:void 0,children:[Ee("time",{className:"aethercal-tg-event-time",children:w})," ",Ee("span",{className:"aethercal-tg-event-title",children:k.title}),q&&T?Qe(ca,{children:[Ee("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:xe(k,"start")}),Ee("div",{className:"aethercal-tg-resize-handle aethercal-tg-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:xe(k,"end")})]}):null]},k.id)}),P!==null&&l.dateOnly===ne?Ee("div",{className:"aethercal-now-indicator",style:{top:Ue(P)},"aria-hidden":"true"}):null]},l.dateOnly)})]})]}),Ee(vt,{id:V,text:b.keyboardHint}),Ee(ft,{message:Pe})]})}import*as F from"react";var ua="aethercal-timeline-styles",ga=`
:where(.aethercal-timeline) {
${bn()}
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
`;function Tn(){if(typeof document>"u"||document.getElementById(ua))return;let e=document.createElement("style");e.id=ua,e.textContent=ga,document.head.appendChild(e)}import{Fragment as fa,jsx as se,jsxs as He}from"react/jsx-runtime";function Be(...e){return e.filter(Boolean).join(" ")}var _e=e=>`${e*100}%`,ma=new Set,ja="unassigned",qa=e=>e.resource?`r:${e.resource.id}`:ja;function Ut(e,t){let n=t.getBoundingClientRect();return n.width>0?(e-n.left)/n.width:0}function pa(e){let t=A(e);return t.getHours()*60+t.getMinutes()}function Rn(e,t,n){let a=A(`${e}T00:00:00`),r=new Date(a.getFullYear(),a.getMonth(),a.getDate(),0,t,0);return new Intl.DateTimeFormat(n,{hour:"numeric",minute:"2-digit"}).format(r)}function Mn(e){let{days:t,resources:n,events:a,locale:r,config:i,now:o,themeVars:u,defaultCollapsedGroupIds:d,onToggleGroup:c,onEventDrop:p,onEventResize:f,onRangeSelect:h,onEventClick:M,onContextMenu:x,pendingIds:b=ma,rolledBackIds:N=ma}=e,P=F.useMemo(()=>e.messages??ct(r),[e.messages,r]);F.useEffect(()=>{ut(),Tn()},[]);let[ne,R]=F.useState(""),[_,z]=F.useState(()=>new Set(d??[])),Y=F.useMemo(()=>[..._],[_]),v=F.useMemo(()=>rn(n,a,t,{...i,collapsedGroupIds:Y}),[n,a,t,i,Y]),ue=F.useMemo(()=>v.items.flatMap(s=>s.kind==="row"?[s.row]:[]),[v.items]),fe=F.useMemo(()=>ue.filter(s=>s.resource!==null),[ue]),le=F.useMemo(()=>on(o,t,v.config),[o,t,v.config]),q=F.useMemo(()=>pe(j(o)),[o]),[we,ae]=F.useReducer(xt,pt),W=F.useRef(null),[B,ce]=F.useState(null),[xe,Te]=F.useState(null),ge=!!p,Ce=!!f,Le=!!h,ee=F.useCallback((s,m)=>{let{windowMinutes:S,dayStartHour:E}=v.config,H=t.length*S;if(H<=0)return 0;let O=t.indexOf(s);return((O===-1?0:O)*S+(m-E*60))/H},[t,v.config]),K=F.useCallback(s=>{let m=!_.has(s);z(S=>{let E=new Set(S);return E.has(s)?E.delete(s):E.add(s),E}),c?.(s,m),R(m?P.groupCollapsed(s):P.groupExpanded(s))},[_,c,P]),Ie=F.useCallback(s=>m=>{if(m.preventDefault(),we.status!=="dragging"){ae({type:"COMMIT"});return}let S=we.eventId,E=m.dataTransfer.getData("text/plain");if(ae({type:"COMMIT"}),E&&E!==S||!p||!s.resource)return;let H=a.find(C=>C.id===S);if(!H||H.editable===!1)return;let O=dt(Ut(m.clientX,m.currentTarget),t,v.config);if(!O)return;let y=H.allDay===!0?null:O.minuteOfDay;p(st(H,O.dateOnly,y,s.resource.id))},[we,a,p,t,v.config]),me=F.useCallback(s=>!p||W.current?.kind==="resize"?!1:(ae({type:"DRAG_START",eventId:s}),!0),[p]),re=F.useCallback(()=>ae({type:"CANCEL"}),[]),J=F.useCallback((s,m)=>S=>{if(!f||s.editable===!1||S.button!==0||W.current)return;let E=S.currentTarget.closest(".aethercal-tl-track");E&&(S.preventDefault(),S.stopPropagation(),W.current={kind:"resize",pointerId:S.pointerId,eventId:s.id,edge:m,trackEl:E,payload:null},S.currentTarget.setPointerCapture?.(S.pointerId),ae({type:"RESIZE_START",eventId:s.id,edge:m}))},[f]),Re=F.useCallback(s=>m=>{if(!h||m.button!==0||!s.resource||W.current||m.target.closest("[data-event-id], button"))return;let S=m.currentTarget,E=dt(Ut(m.clientX,S),t,v.config);if(!E)return;let H=E.minuteOfDay??0;W.current={kind:"select",pointerId:m.pointerId,resourceId:s.resource.id,trackEl:S,anchorDate:E.dateOnly,anchorMinute:H,currentDate:E.dateOnly,currentMinute:H},S.setPointerCapture?.(m.pointerId),ae({type:"SELECT_START",point:{dateOnly:E.dateOnly,minuteOfDay:H,resourceId:s.resource.id}})},[h,t,v.config]),Pe=we.status==="resizing"||we.status==="selecting";F.useLayoutEffect(()=>{if(!Pe)return;let s=O=>{let y=W.current;if(!y||O.pointerId!==y.pointerId)return;let C=dt(Ut(O.clientX,y.trackEl),t,v.config);if(!C)return;if(y.kind==="resize"){let We=a.find(Mt=>Mt.id===y.eventId);if(!We)return;let Rt=$e(We,y.edge,C.dateOnly,C.minuteOfDay??0);y.payload=Rt,ce(Rt);return}y.currentDate=C.dateOnly,y.currentMinute=C.minuteOfDay??0;let U=ee(y.anchorDate,y.anchorMinute),Ke=ee(y.currentDate,y.currentMinute);Te({resourceId:y.resourceId,leftFraction:Math.min(U,Ke),widthFraction:Math.abs(Ke-U)})},m=O=>{let y=W.current;W.current=null,ce(null),Te(null),O&&y&&(y.kind==="resize"&&y.payload&&f&&f(y.payload),y.kind==="select"&&h&&(y.currentDate!==y.anchorDate||y.currentMinute!==y.anchorMinute)&&h(Xe({dateOnly:y.anchorDate,minuteOfDay:y.anchorMinute,resourceId:y.resourceId},{dateOnly:y.currentDate,minuteOfDay:y.currentMinute,resourceId:y.resourceId}))),ae({type:O?"COMMIT":"CANCEL"})},S=O=>{W.current&&O.pointerId!==W.current.pointerId||m(!0)},E=O=>{W.current&&O.pointerId!==W.current.pointerId||m(!1)},H=O=>{O.key==="Escape"&&m(!1)};return window.addEventListener("pointermove",s),window.addEventListener("pointerup",S),window.addEventListener("pointercancel",E),window.addEventListener("keydown",H),()=>{window.removeEventListener("pointermove",s),window.removeEventListener("pointerup",S),window.removeEventListener("pointercancel",E),window.removeEventListener("keydown",H)}},[Pe,a,t,v.config,ee,f,h]);let ve=F.useCallback(s=>{if(!x||s.target.closest("[data-event-id], button"))return;let m=dt(Ut(s.clientX,s.currentTarget),t,v.config);if(!m)return;s.preventDefault();let S=A(`${m.dateOnly}T00:00:00`),E=new Date(S.getFullYear(),S.getMonth(),S.getDate(),0,m.minuteOfDay??0,0);x({start:j(E)})},[x,t,v.config]),G=F.useId(),L=`${G}-hint`,V=Ne,[de,ie]=F.useState(0),[Z,X]=F.useState(0),[ye,he]=F.useState(null),[oe,Se]=F.useState(null),te=s=>`${G}-i-${s}`,et=s=>`${G}-e-${s}`;F.useEffect(()=>{de>v.items.length-1&&(ie(Math.max(0,v.items.length-1)),he(null),Se(null))},[v.items.length,de]),F.useEffect(()=>{Z>t.length-1&&X(Math.max(0,t.length-1))},[t.length,Z]);let l=v.items[de],g=l?.kind==="row"?l.row:void 0,D=F.useCallback(s=>!!M||s.editable!==!1&&!!(p||f),[M,p,f]),I=F.useMemo(()=>(g?.blocks??[]).map(s=>s.event).filter(s=>D(s)),[g,D]),k=F.useMemo(()=>{let s=v.dayHeaders[Z];if(!s||!g)return[];let m=s.leftFraction,S=s.leftFraction+s.widthFraction,E=1e-9;return g.blocks.filter(H=>{let O=H.leftFraction,y=H.leftFraction+H.widthFraction;return y>O?O<S-E&&y>m+E:O>=m-E&&O<S-E}).map(H=>H.event).filter(H=>D(H))},[v.dayHeaders,Z,g,D]);F.useEffect(()=>{let s=new Set(I.map(m=>m.id));oe&&!s.has(oe.eventId)?(Se(null),he(null)):!oe&&ye!==null&&!s.has(ye)&&he(null)},[I,ye,oe]);let T=v.items.length===0?void 0:oe?et(oe.eventId):ye?et(ye):te(de),w=F.useCallback(s=>fe.find(m=>m.resource?.id===s)?.resource?.title??s,[fe]),Me=F.useCallback(s=>{let m=oe;if(!m)return;let S=a.find(C=>C.id===m.eventId);if(!S)return;let E=S.allDay===!0,H=m.dateOnly,O=m.minute,y=m.kind==="move"?m.resourceId:"";if(s==="ArrowLeft"||s==="ArrowRight")if(E)H=ke(H,s==="ArrowLeft"?-1:1);else{let C=s==="ArrowLeft"?-V:V,U=dt(ee(H,O+C),t,v.config,V);if(!U)return;H=U.dateOnly,O=U.minuteOfDay??O}else if(m.kind==="move"&&(s==="ArrowUp"||s==="ArrowDown")){let C=fe.findIndex(Ke=>Ke.resource?.id===y),U=s==="ArrowUp"?C-1:C+1;if(C===-1||U<0||U>=fe.length)return;y=fe[U].resource.id}else return;if(!(H===m.dateOnly&&O===m.minute&&(m.kind!=="move"||y===m.resourceId)))if(m.kind==="move"){let C=E?Fe(H,r):`${Fe(H,r)} ${Rn(H,O,r)}`;R(P.movedTo(`${w(y)} \xB7 ${C}`)),Se({...m,dateOnly:H,minute:O,resourceId:y,moved:!0})}else{let C=$e(S,"end",H,O);R(P.resizedTo(`${De(C.start,r)} \u2013 ${De(C.end,r)}`)),Se({...m,dateOnly:H,minute:O,moved:!0})}},[oe,a,V,t,v.config,fe,ee,w,P,r]),be=F.useCallback(()=>{let s=oe;if(!s)return;if(!s.moved){he(s.eventId),Se(null);return}let m=a.find(S=>S.id===s.eventId);if(m&&m.editable!==!1&&s.kind==="move"&&p){let S=m.allDay===!0?null:s.minute;p(st(m,s.dateOnly,S,s.resourceId)),R(P.dropped(`${w(s.resourceId)} \xB7 ${m.allDay===!0?Fe(s.dateOnly,r):Rn(s.dateOnly,s.minute,r)}`)),he(null)}else if(m&&m.editable!==!1&&s.kind==="resize"&&f){let S=$e(m,"end",s.dateOnly,s.minute);f(S),R(P.resized(`${De(S.start,r)} \u2013 ${De(S.end,r)}`)),he(s.eventId)}else he(s.eventId);Se(null)},[oe,a,p,f,w,P,r]),Ye=F.useCallback(s=>{let{key:m}=s,S=m==="Enter"||m===" "||m==="Spacebar",E=m==="ArrowUp"||m==="ArrowDown"||m==="ArrowLeft"||m==="ArrowRight",H=v.items.length-1;if(oe){if(E){s.preventDefault(),Me(m);return}if(S){s.preventDefault(),be();return}m==="Escape"&&(s.preventDefault(),Se(null),R(P.cancelled));return}if(ye){let O=I.findIndex(y=>y.id===ye);if(m==="ArrowRight"){s.preventDefault(),O>=0&&O<I.length-1&&he(I[O+1].id);return}if(m==="ArrowLeft"){s.preventDefault(),O>0?he(I[O-1].id):he(null);return}if(m==="ArrowUp"||m==="ArrowDown"){s.preventDefault(),he(null),ie(y=>Math.min(Math.max(y+(m==="ArrowUp"?-1:1),0),H));return}if(S){s.preventDefault();let y=I.find(C=>C.id===ye);if(!y)return;y.editable!==!1&&p&&g?.resource?(Se({kind:"move",eventId:y.id,dateOnly:pe(y.start),minute:pa(y.start),resourceId:g.resource.id,moved:!1}),R(P.grabbedMoveHint(y.title))):M&&M({id:y.id});return}if((m==="r"||m==="R")&&f){s.preventDefault();let y=I.find(C=>C.id===ye);y&&y.allDay!==!0&&y.editable!==!1&&(Se({kind:"resize",eventId:y.id,dateOnly:pe(y.end),minute:pa(y.end),moved:!1}),R(P.grabbedResizeHint(y.title)));return}m==="Escape"&&(s.preventDefault(),he(null));return}if(m==="ArrowUp"||m==="ArrowDown"){s.preventDefault(),ie(O=>Math.min(Math.max(O+(m==="ArrowUp"?-1:1),0),H));return}if(m==="ArrowLeft"||m==="ArrowRight"){s.preventDefault(),X(O=>Math.min(Math.max(O+(m==="ArrowLeft"?-1:1),0),Math.max(0,t.length-1)));return}if(m==="Home"||m==="End"){s.preventDefault(),X(m==="Home"?0:Math.max(0,t.length-1));return}if(S){if(l?.kind==="group"){s.preventDefault(),K(l.group.id);return}if(k.length>0){s.preventDefault(),he(k[0].id);return}if(g?.resource&&h&&t.length>0){let O=t[Math.min(Z,t.length-1)],y=v.config.dayStartHour*60,C=Math.min(y+60,v.config.dayEndHour*60);C>y&&(s.preventDefault(),h(Xe({dateOnly:O,minuteOfDay:y,resourceId:g.resource.id},{dateOnly:O,minuteOfDay:C,resourceId:g.resource.id})),R(P.createHere(`${g.resource.title} \xB7 ${Fe(O,r)} ${Rn(O,y,r)}`)))}}},[oe,ye,I,k,l,g,v.items.length,v.config,t,Z,p,f,M,h,Me,be,K,P,r]),tt={...u??{}};return He(fa,{children:[He("div",{className:Be("aethercal-calendar","aethercal-timeline",we.status==="dragging"&&"is-dragging",we.status==="resizing"&&"is-resizing",we.status==="selecting"&&"is-selecting"),role:"grid","aria-label":P.viewNames.timeline,"aria-describedby":L,...T!==void 0?{"aria-activedescendant":T}:{},tabIndex:0,"data-view":"timeline",style:tt,onKeyDown:Ye,children:[He("div",{className:"aethercal-tl-head",role:"row",children:[se("div",{className:"aethercal-tl-corner",role:"columnheader",children:P.timelineResources}),se("div",{className:"aethercal-tl-days",children:v.dayHeaders.map(s=>se("div",{role:"columnheader",className:Be("aethercal-tl-dayhead",s.dateOnly===q&&"is-today"),"data-date":s.dateOnly,style:{left:_e(s.leftFraction),width:_e(s.widthFraction)},children:se("span",{children:Fe(s.dateOnly,r)})},s.dateOnly))})]}),He("div",{className:"aethercal-tl-body",role:"rowgroup",tabIndex:0,children:[v.items.length===0?se("div",{className:"aethercal-tl-row aethercal-tl-row-empty",role:"row",children:se("div",{role:"gridcell",className:"aethercal-tl-empty",children:P.timelineEmpty})}):null,v.items.map((s,m)=>{let S=!ye&&!oe&&m===de;if(s.kind==="group"){let{group:C}=s;return se("div",{role:"row",className:Be("aethercal-tl-group",C.collapsed&&"is-collapsed"),children:se("div",{className:"aethercal-tl-group-head",role:"rowheader",children:He("button",{type:"button",id:te(m),className:Be("aethercal-tl-group-toggle",S&&"is-active"),"aria-expanded":!C.collapsed,tabIndex:-1,onClick:()=>K(C.id),children:[se("span",{className:"aethercal-tl-caret","aria-hidden":"true",children:"\u25BE"}),se("span",{children:C.id})," ",se("span",{className:"aethercal-tl-group-count",children:P.timelineGroupCount(C.resourceCount)})]})})},`g:${C.id}`)}let{row:E}=s,H=oe?.kind==="move"&&E.resource?.id===oe.resourceId,O={"--ac-tl-lanes":E.laneCount},y=E.resource?.color?{"--ac-tl-row-accent":E.resource.color}:{};return He("div",{role:"row",className:Be("aethercal-tl-row",!E.resource&&"is-unassigned"),children:[He("div",{id:te(m),role:"rowheader",className:Be("aethercal-tl-rowhead",S&&"is-active"),style:y,children:[E.resource?.color?se("span",{className:"aethercal-tl-swatch","aria-hidden":"true"}):null,se("span",{className:"aethercal-tl-rowhead-title",children:E.resource?E.resource.title:P.timelineUnassigned})]}),He("div",{role:"gridcell",className:Be("aethercal-tl-track",H&&"is-drop-target"),"data-resource-id":E.resource?.id??"",style:O,onDragOver:ge&&E.resource?C=>C.preventDefault():void 0,onDrop:ge&&E.resource?Ie(E):void 0,onPointerDown:Le&&E.resource?Re(E):void 0,onContextMenu:x?ve:void 0,children:[v.ticks.map(C=>se("div",{className:Be("aethercal-tl-line",C.isDayStart&&"is-day-start"),style:{left:_e(C.leftFraction)},"aria-hidden":"true"},`${C.dateOnly}-${C.hour}`)),xe&&xe.resourceId===E.resource?.id?se("div",{className:"aethercal-tl-select-band",style:{left:_e(xe.leftFraction),width:_e(xe.widthFraction)},"aria-hidden":"true"}):null,E.blocks.map(C=>{let{event:U}=C,Ke=U.editable!==!1,We=B?.id===U.id?B:null,Rt=oe?.eventId===U.id||!oe&&ye===U.id&&g===E,Mt=C.allDay?P.allDay:De(We?.start??U.start,r),In=We?an({...U,start:We.start,end:We.end},t,v.config)[0]:void 0,va={left:_e(In?.leftFraction??C.leftFraction),width:_e(In?.widthFraction??C.widthFraction),top:_e(C.lane/C.laneCount),height:_e(1/C.laneCount),...U.color?{"--ac-tl-event-accent":U.color}:{}};return He("div",{id:et(U.id),className:Be("aethercal-tl-event",C.allDay&&"is-allday",!Ke&&"is-locked",C.continuesBefore&&"continues-before",C.continuesAfter&&"continues-after",b.has(U.id)&&"is-pending",N.has(U.id)&&"is-rolledback",!!We&&"is-resizing",Rt&&"is-active",oe?.eventId===U.id&&"is-grabbed"),...D(U)?{role:"button"}:{},draggable:Ke&&ge,"data-event-id":U.id,"data-lane":C.lane,"aria-label":`${Mt} ${U.title}`,title:U.title,style:va,onDragStart:gt=>{if(!me(U.id)){gt.preventDefault();return}gt.dataTransfer.setData("text/plain",U.id),gt.dataTransfer.effectAllowed="move"},onDragEnd:re,onClick:M?()=>M({id:U.id}):void 0,onContextMenu:x?gt=>{gt.preventDefault(),gt.stopPropagation(),x({id:U.id})}:void 0,children:[se("time",{className:"aethercal-tl-event-time",children:Mt})," ",se("span",{className:"aethercal-tl-event-title",children:U.title}),Ce&&Ke&&!C.allDay?He(fa,{children:[se("div",{className:"aethercal-tl-resize-handle aethercal-tl-resize-handle-start","data-edge":"start","aria-hidden":"true",draggable:!1,onPointerDown:J(U,"start")}),se("div",{className:"aethercal-tl-resize-handle aethercal-tl-resize-handle-end","data-edge":"end","aria-hidden":"true",draggable:!1,onPointerDown:J(U,"end")})]}):null]},U.id)}),le!==null?se("div",{className:"aethercal-tl-now",style:{left:_e(le)},"aria-hidden":"true"}):null]})]},qa(E))})]})]}),se(vt,{id:L,text:P.timelineKeyboardHint}),se(ft,{message:ne})]})}import{jsx as ht,jsxs as tr}from"react/jsx-runtime";function Za(e){if(e instanceof Date)return e;if(typeof e=="string"){let t=e.trim();if(t==="")return new Date;try{return A(t)}catch{return new Date}}return new Date}function Qa(e){return e instanceof Date?e:typeof e=="string"?A(e):new Date}function Bt(e){let{view:t="month",events:n,resources:a,timelineDays:r,defaultCollapsedGroupIds:i,onToggleGroup:o,anchor:u,locale:d="en",theme:c,messages:p,firstDayOfWeek:f=1,maxEventsPerDay:h=3,weekdayLabels:M,formatMore:x,unavailableLabel:b,dayStartHour:N,dayEndHour:P,allDayLabel:ne,now:R,continuesLabel:_,formatEndsLabel:z,agendaEmptyLabel:Y,onEventDrop:v,onEventResize:ue,onRangeSelect:fe,onEventClick:le,onContextMenu:q,navigation:we=!1,navigationViews:ae=!0,onRangeChange:W,onViewChange:B,pendingIds:ce,rolledBackIds:xe}=e;Ge.useEffect(()=>{ut()},[]);let Te=Ge.useMemo(()=>Za(u),[u]),ge=Ge.useMemo(()=>Dn(c),[c]),Ce=Ge.useMemo(()=>{let ve={...ne!==void 0?{allDay:ne}:{},..._!==void 0?{continues:_}:{},...z!==void 0?{endsAt:z}:{},...Y!==void 0?{noEvents:Y}:{},...b!==void 0?{unavailable:b}:{},...x!==void 0?{more:x}:{},...p};return ct(d,ve)},[d,ne,_,z,Y,b,x,p]),[Le,ee]=Ge.useState(()=>new Date);Ge.useEffect(()=>{if(R!==void 0||t!=="week"&&t!=="day"&&t!=="timeline")return;let ve=setInterval(()=>ee(new Date),6e4);return()=>clearInterval(ve)},[R,t]);let K=Ge.useMemo(()=>R!==void 0?Qa(R):Le,[R,Le]),Ie=Number.isInteger(f)&&f>=0&&f<=6?f:1,me=Number.isInteger(h)&&h>=0?h:3,re=M&&M.length===7?M:void 0,J=je(r),Re=Ge.useMemo(()=>({...N!==void 0?{dayStartHour:N}:{},...P!==void 0?{dayEndHour:P}:{}}),[N,P]),Pe=(()=>{if(t==="list")return ht(Vn,{events:n??[],locale:d,messages:Ce,themeVars:ge});if(t==="month")return ht(jn,{events:n??[],anchor:Te,locale:d,messages:Ce,themeVars:ge,firstDayOfWeek:Ie,maxEventsPerDay:me,...re?{weekdayLabels:re}:{},...v?{onEventDrop:v}:{},...fe?{onRangeSelect:fe}:{},...le?{onEventClick:le}:{},...q?{onContextMenu:q}:{},...ce?{pendingIds:ce}:{},...xe?{rolledBackIds:xe}:{}});if(t==="timeline")return ht(Mn,{days:Xt(Te,J),resources:a??[],events:n??[],locale:d,messages:Ce,themeVars:ge,config:Re,now:K,...i?{defaultCollapsedGroupIds:i}:{},...o?{onToggleGroup:o}:{},...v?{onEventDrop:v}:{},...ue?{onEventResize:ue}:{},...fe?{onRangeSelect:fe}:{},...le?{onEventClick:le}:{},...q?{onContextMenu:q}:{},...ce?{pendingIds:ce}:{},...xe?{rolledBackIds:xe}:{}});if(t==="week"||t==="day"){let ve=t==="week"?Kt(Te,Ie):[pe(j(Te))];return ht(xn,{view:t,days:ve,events:n??[],locale:d,messages:Ce,themeVars:ge,config:Re,now:K,...v?{onEventDrop:v}:{},...ue?{onEventResize:ue}:{},...fe?{onRangeSelect:fe}:{},...le?{onEventClick:le}:{},...q?{onContextMenu:q}:{},...ce?{pendingIds:ce}:{},...xe?{rolledBackIds:xe}:{}})}return ht("div",{className:"aethercal-calendar aethercal-unavailable",role:"status","data-view":t,style:ge,children:Ce.unavailable})})();return we?tr("div",{className:"aethercal-calendar-shell",style:ge,children:[ht(pn,{view:t,anchor:Te,now:K,locale:d,firstDayOfWeek:Ie,timelineDays:J,messages:Ce,showViews:ae,...W?{onRangeChange:W}:{},...B?{onViewChange:B}:{}}),Pe]}):Pe}var er=Bt;import*as Oe from"react";function nr(){return typeof crypto<"u"&&typeof crypto.randomUUID=="function"?crypto.randomUUID():`cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}var ar=8e3,rr=900;function Cn(e){let{events:t,mutate:n,timeoutMs:a=ar,rollbackFlashMs:r=rr,generateId:i=nr}=e,[o,u]=Oe.useReducer(dn,ln),d=Oe.useRef(t);d.current=t;let c=Oe.useRef(!0),p=Oe.useRef(new Map);Oe.useEffect(()=>{c.current=!0;let M=p.current;return()=>{c.current=!1;for(let x of M.values())clearTimeout(x);M.clear()}},[]),Oe.useEffect(()=>{for(let M of un(t,o)){let x=o.overrides[M];u({type:"CLEAR",id:M,...x?{clientMutationId:x.clientMutationId}:{}})}},[t,o]);let f=Oe.useCallback((M,x)=>{let b=i(),N=d.current.find(v=>v.id===x.id),P=p.current,ne=v=>{let ue=P.get(v);ue!==void 0&&(clearTimeout(ue),P.delete(v))},R=()=>{P.set(`fl:${b}`,setTimeout(()=>{P.delete(`fl:${b}`),c.current&&u({type:"CLEAR",id:x.id,clientMutationId:b})},r))};u({type:"SUBMIT",id:x.id,clientMutationId:b,start:x.start,end:x.end,...N?.revision!==void 0?{baseRevision:N.revision}:{},..."resourceId"in x&&x.resourceId!==void 0?{resourceId:x.resourceId}:{}}),P.set(`to:${b}`,setTimeout(()=>{P.delete(`to:${b}`),c.current&&(u({type:"TIMEOUT",id:x.id,clientMutationId:b}),R())},a));let _=()=>{ne(`to:${b}`),c.current&&(u({type:"REJECT",id:x.id,clientMutationId:b}),R())},z={kind:M,clientMutationId:b,payload:{...x,client_mutation_id:b}},Y;try{Y=n(z)}catch(v){Y=Promise.reject(v instanceof Error?v:new Error(String(v)))}Y.then(v=>{if(v.id!==x.id){_();return}ne(`to:${b}`),c.current&&u({type:"RESOLVE",id:v.id,clientMutationId:b,start:v.start,end:v.end,revision:v.revision,...v.resourceId!==void 0?{resourceId:v.resourceId}:{}})}).catch(_)},[n,a,r,i]),h=Oe.useMemo(()=>cn(t,o),[t,o]);return{events:h.events,pendingIds:h.pendingIds,rolledBackIds:h.rolledBackIds,submit:f}}import{jsx as or}from"react/jsx-runtime";function ir({events:e,mutate:t,timeoutMs:n,rollbackFlashMs:a,generateId:r,...i}){let{events:o,pendingIds:u,rolledBackIds:d,submit:c}=Cn({events:e,mutate:t,...n!==void 0?{timeoutMs:n}:{},...a!==void 0?{rollbackFlashMs:a}:{},...r?{generateId:r}:{}});return or(Bt,{...i,events:o,pendingIds:u,rolledBackIds:d,onEventDrop:p=>c("drop",p),onEventResize:p=>c("resize",p)})}export{Bt as AetherCalendar,aa as CALENDAR_CSS,pn as CalendarNav,fn as DEFAULT_LOCALE_MESSAGES,ir as OptimisticCalendar,Ht as PRESETS,Zn as PRESET_NAMES,ga as TIMELINE_CSS,sa as TIME_GRID_CSS,xn as TimeGridView,Mn as TimelineView,er as default,yn as defaultBaseTokenCss,hn as defaultTimeGridTokenCss,bn as defaultTimelineTokenCss,ut as ensureCalendarStyles,wn as ensureTimeGridStyles,Tn as ensureTimelineStyles,Et as getVisibleRange,ea as isThemePreset,A as parseLocalDateTime,ct as resolveMessages,Dn as resolveThemeVars,Pt as stepAnchor,Cn as useOptimisticEvents};
