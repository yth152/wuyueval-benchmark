import React,{useEffect,useRef,useState} from 'react';
import {Mic,Square,X,LoaderCircle} from 'lucide-react';
import workletUrl from './recorder-worklet.js?url';

export default function VoiceInput({config,disabled,contextKey,onText,onActive,onNotice}){
 const [phase,setPhase]=useState('idle'),[partial,setPartial]=useState(''),[seconds,setSeconds]=useState(0);
 const session=useRef(null),callbacks=useRef({onText,onActive,onNotice});callbacks.current={onText,onActive,onNotice};
 const alive=s=>session.current===s&&!s.closed;
 function release(s){clearInterval(s.timer);clearTimeout(s.deadline);clearTimeout(s.flushTimer);s.stream?.getTracks().forEach(t=>t.stop());s.node?.disconnect();s.source?.disconnect();s.context?.close().catch(()=>{});if(s.socket?.readyState<2)s.socket.close();}
 function close(){const s=session.current;if(s){s.closed=true;release(s);session.current=null;}setPhase('idle');setPartial('');callbacks.current.onActive(false);}
 function fail(s,text){if(!alive(s))return;close();callbacks.current.onNotice(text);}
 useEffect(()=>{close();return()=>{const s=session.current;if(s){s.closed=true;release(s);session.current=null;}callbacks.current.onActive(false);};},[contextKey]);
 useEffect(()=>{const hide=()=>{if(document.hidden)close();};document.addEventListener('visibilitychange',hide);window.addEventListener('pagehide',hide);return()=>{document.removeEventListener('visibilitychange',hide);window.removeEventListener('pagehide',hide);};},[]);
 function stop(s){if(!alive(s)||s.ending)return;s.ending=true;setPhase('finishing');clearInterval(s.timer);s.node.port.postMessage({type:'flush'});s.stream?.getTracks().forEach(t=>t.stop());s.flushTimer=setTimeout(()=>fail(s,'录音收尾超时，请重试。'),3000);}
 async function start(){
  if(disabled||session.current)return;
  if(!config?.ready){callbacks.current.onNotice('管理员尚未启用语音输入。');return;}
  const s={closed:false};session.current=s;setPhase('connecting');setPartial('');setSeconds(0);callbacks.current.onActive(true);
  try{
   if(!navigator.mediaDevices?.getUserMedia||!window.AudioWorkletNode)throw Error('录音需要 HTTPS 或本机 localhost，以及支持麦克风的浏览器。');
   s.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
   if(!alive(s)){release(s);return;}
   s.context=new AudioContext();await s.context.resume();await s.context.audioWorklet.addModule(workletUrl);
   if(!alive(s)){release(s);return;}
   s.socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/voice/stream`);
   s.deadline=setTimeout(()=>fail(s,'语音服务连接超时，请重试。'),20000);
   s.socket.onmessage=event=>{
    if(!alive(s))return;
    let data;try{data=JSON.parse(event.data);}catch{return fail(s,'语音服务返回格式异常。');}
    if(data.type==='ready'){
     clearTimeout(s.deadline);s.limit=Math.min(config.max_seconds,data.max_seconds);s.sent=0;s.source=s.context.createMediaStreamSource(s.stream);s.node=new AudioWorkletNode(s.context,'wuyu-pcm-recorder');
     s.node.port.onmessage=({data})=>{
      if(!alive(s)||s.socket.readyState!==WebSocket.OPEN)return;
      if(data.type==='pcm'){if(s.socket.bufferedAmount>256000)return fail(s,'上传速度不足，请缩短录音后重试。');const remaining=s.limit*32000-s.sent;if(remaining>0){const chunk=data.buffer.byteLength>remaining?data.buffer.slice(0,remaining):data.buffer;s.socket.send(chunk);s.sent+=chunk.byteLength;}if(s.sent>=s.limit*32000)stop(s);}
      if(data.type==='flushed'){clearTimeout(s.flushTimer);s.socket.send(JSON.stringify({type:'end'}));s.node.disconnect();s.source.disconnect();s.context.close().catch(()=>{});s.deadline=setTimeout(()=>fail(s,'语音识别超时，请重试。'),30000);}
     };
     s.source.connect(s.node);s.node.connect(s.context.destination);s.started=Date.now();setPhase('recording');
     s.timer=setInterval(()=>{const elapsed=(Date.now()-s.started)/1000;setSeconds(Math.floor(elapsed));if(elapsed>=s.limit-.25)stop(s);},100);
    }else if(data.type==='partial')setPartial(data.text);
    else if(data.type==='final'){const value=data.text?.trim();close();if(value)callbacks.current.onText(value);else callbacks.current.onNotice('未识别到语音，请重新录制。');}
    else if(data.type==='error')fail(s,data.detail||'语音识别失败。');
   };
   s.socket.onerror=()=>fail(s,'无法连接语音服务，请检查网络或联系管理员。');
   s.socket.onclose=()=>{if(alive(s))fail(s,'语音连接已关闭，请重新录制。');};
  }catch(error){fail(s,error.name==='NotAllowedError'?'请允许浏览器使用麦克风后重试。':error.name==='NotFoundError'?'未找到麦克风，请连接设备后重试。':error.message||'无法开始录音。');}
 }
 return <div className="voice-input"><button type="button" className={`icon-button voice-button ${phase==='recording'?'recording':''}`} aria-label="语音输入" title={config?.ready?'语音输入':'管理员尚未启用语音输入'} disabled={disabled||phase!=='idle'} onClick={start}><Mic size={21}/></button>{phase!=='idle'&&<div className="voice-panel" role="region" aria-label="语音录入"><div className="voice-panel-row">{phase==='recording'?<span className="voice-live-dot"/>:<LoaderCircle size={16} className="spin"/>}<strong>{phase==='connecting'?'正在连接麦克风…':phase==='recording'?`正在录音 · ${seconds}s`:'正在转写…'}</strong>{phase==='recording'&&<button type="button" className="secondary" onClick={()=>stop(session.current)}><Square size={13}/>完成录音</button>}<button type="button" className="icon-button" aria-label="取消录音" onClick={close}><X size={16}/></button></div><p aria-live="polite">{partial||'识别文字会填入输入框，核对后发送。'}</p></div>}</div>;
}
