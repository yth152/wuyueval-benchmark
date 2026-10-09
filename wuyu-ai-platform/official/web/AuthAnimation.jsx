import React,{useEffect,useState} from 'react';
import {Pause,Play} from 'lucide-react';
import flow from './auth-flow.png';
import FlowCanvas from './FlowCanvas';

// Decorative motion stays independent of the form and stops in background tabs.
export default function AuthAnimation(){
 const [paused,setPaused]=useState(false);
 const [reduced,setReduced]=useState(()=>window.matchMedia('(prefers-reduced-motion: reduce)').matches);
 const [hidden,setHidden]=useState(document.hidden);
 useEffect(()=>{
  const media=window.matchMedia('(prefers-reduced-motion: reduce)');
  const motion=()=>setReduced(media.matches),visibility=()=>setHidden(document.hidden);
  media.addEventListener('change',motion);
  document.addEventListener('visibilitychange',visibility);
  return()=>{media.removeEventListener('change',motion);document.removeEventListener('visibilitychange',visibility);};
 },[]);
 return <div className="auth-animation" data-paused={paused||reduced||hidden}>
  <div className="auth-flow-scene" aria-hidden="true">
   <img className="auth-flow-image" src={flow} alt="" decoding="async" fetchPriority="high"/>
   <FlowCanvas paused={paused||reduced||hidden}/>
   <div className="auth-flow-light"/>
  </div>
  {!reduced&&<button type="button" className="auth-motion-toggle" aria-label={paused?'播放背景动画':'暂停背景动画'} aria-pressed={paused} title={paused?'播放背景动画':'暂停背景动画'} onClick={()=>setPaused(value=>!value)}>{paused?<Play size={13}/>:<Pause size={13}/>}</button>}
 </div>;
}

