export type Envelope<T=unknown>={project_instance_id:string;revision:number;data:T};
let csrf=sessionStorage.getItem('papyrus-csrf')||'';
let revision=0;
export const setRevision=(r:number)=>{revision=Math.max(revision,r)};
export class ApiError extends Error{status:number;constructor(message:string,status:number){super(message);this.name='ApiError';this.status=status}}
const parse=async<T>(response:Response):Promise<T>=>{const body=await response.json().catch(()=>({}));if(!response.ok)throw new ApiError(body?.detail?.message||body?.detail||body?.error?.message||`Request failed (${response.status})`,response.status);if(body.revision!==undefined)setRevision(body.revision);return body};
export function newId(){const bytes=new Uint8Array(16);if(globalThis.crypto?.getRandomValues)globalThis.crypto.getRandomValues(bytes);else for(let i=0;i<bytes.length;i++)bytes[i]=Math.floor(Math.random()*256);bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;const hex=[...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');return`${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`}
export async function pair(token:string,device_name:string){const value=await parse<{csrf:string}>(await fetch('/api/v1/auth/pair',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token,device_name})}));csrf=value.csrf;sessionStorage.setItem('papyrus-csrf',csrf);return value}
export async function session(){const value=await parse<Envelope<any>>(await fetch('/api/v1/session'));csrf=value.data.csrf;sessionStorage.setItem('papyrus-csrf',csrf);return value}
export async function snapshot<T>(name:string,query=''){return parse<Envelope<T>>(await fetch(`/api/v1/snapshot/${name}${query}`))}
export async function details<T>(sourceId:string){return parse<Envelope<T>>(await fetch(`/api/v1/source/${sourceId}`))}
export async function command<T>(domain:string,operation:string,payload:any={},read=false){return parse<Envelope<T>>(await fetch(`/api/v1/command/${domain}/${operation}`,{method:'POST',headers:{'content-type':'application/json',...(csrf?{'x-papyrus-csrf':csrf}:{})},body:JSON.stringify({command_id:newId(),expected_revision:read?null:revision,payload})}))}
export async function lease(action:'acquire'|'heartbeat'|'release'){return parse<Envelope<any>>(await fetch(`/api/v1/lease/${action}`,{method:'POST',headers:{'x-papyrus-csrf':csrf}}))}
export async function upload(file:File){const form=new FormData();form.append('file',file);return parse<Envelope<any>>(await fetch('/api/v1/uploads',{method:'POST',headers:{'x-papyrus-csrf':csrf},body:form}))}
export function events(onEvent:(event:any)=>void){const protocol=location.protocol==='https:'?'wss':'ws';const ws=new WebSocket(`${protocol}://${location.host}/api/v1/events`);ws.onmessage=e=>{const item=JSON.parse(e.data);if(item.revision!==undefined)setRevision(item.revision);onEvent(item)};return ws}
