import {useState,useEffect,useRef} from 'react';
import {api} from './api';

export const defaultPreferences={theme:'light',font_size:'standard',send_key:'enter'};
export function usePreferences(user){
 const [preferences,setPreferences]=useState(defaultPreferences),[saving,setSaving]=useState(false),[error,setError]=useState(''),[ready,setReady]=useState(false);
 const uid=useRef(user?.id);uid.current=user?.id;
 useEffect(()=>{let current=true;setReady(false);setError('');setSaving(false);setPreferences(defaultPreferences);
  if(user)api('/me/preferences').then(data=>{if(current){setPreferences(data);setReady(true);}}).catch(e=>{if(current)setError(e.message);});
  return()=>{current=false;};
 },[user?.id]);
 useEffect(()=>{const media=window.matchMedia('(prefers-color-scheme: dark)');const apply=()=>{document.documentElement.dataset.theme=preferences.theme==='system'?(media.matches?'dark':'light'):preferences.theme;document.documentElement.dataset.font=preferences.font_size;};apply();media.addEventListener('change',apply);return()=>media.removeEventListener('change',apply);},[preferences]);
 async function update(change){const account=uid.current,previous=preferences;const next={...previous,...change};setSaving(true);setError('');setPreferences(next);
  try{const result=await api('/me/preferences',{method:'PUT',body:JSON.stringify(next)});if(uid.current===account)setPreferences(result);return true;}
  catch(e){if(uid.current===account){setPreferences(previous);setError(e.message);}return false;}
  finally{if(uid.current===account)setSaving(false);}
 }
 return {preferences,update,saving,error,ready};
}
