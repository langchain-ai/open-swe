import { agentsLangGraphApiUrl } from "./api"

const THREAD_PATH_RE =
  /^\/agents\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i

/**
 * Inline head script that starts the thread's `getState` request while the HTML
 * is still parsing and hands the in-flight response to the SDK. The SDK's own
 * call cannot be issued until the bundle has booted, which is most of the delay
 * before a transcript can paint.
 */
export function threadStateWarmupScript(pathname: string): string | null {
  const threadId = THREAD_PATH_RE.exec(pathname)?.[1]
  if (!threadId) return null
  const url = JSON.stringify(
    `${agentsLangGraphApiUrl}/threads/${threadId}/state`
  )
  return `(function(){
if(document.readyState!=="loading")return;
var url=new URL(${url},location.href).href;
var pending=fetch(url,{credentials:"include"});
pending.catch(function(){});
var original=window.fetch;
var timer=setTimeout(release,15000);
function release(){clearTimeout(timer);pending=null;if(window.fetch===patched)window.fetch=original}
function patched(input,init){
var method=String((init&&init.method)||(input&&input.method)||"GET").toUpperCase();
if(pending&&method==="GET"){
var href=typeof input==="string"?input:(input&&input.url)||String(input);
try{if(new URL(href,location.href).href===url){var warmed=pending;release();return warmed}}catch(e){}
}
return original.apply(window,arguments)}
window.fetch=patched;
})();`
}
