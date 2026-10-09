import React from 'react';
import {Plus,Trash2} from 'lucide-react';
import ModelCapabilities,{ModelTest} from './ModelCapabilities';

export default function ModelProfiles({models,onChange,disabled}){
 const update=(index,key,value)=>onChange(models.map((p,i)=>i===index?{...p,[key]:value}:p));
 const add=()=>onChange([...models,{id:'model_'+crypto.randomUUID().replaceAll('-','').slice(0,12),display_name:'新模型',base_url:'',model:'',context_window:32768,protocol:'baseline',enabled:true,api_key:'',supports_vision:false,supports_tools:true,thinking_mode:'none',max_output_tokens:null,temperature:null,system_prompt:''}]);
 return <section className="additional-models"><div className="admin-panel-title"><h2>其他可选模型</h2><button className="secondary" type="button" onClick={add} disabled={disabled||models.length>=32}><Plus size={16}/>添加模型</button></div><p className="admin-hint">用户可在问答框中切换模型。各模型可独立配置接口、密钥、能力和生成参数，平台统一管理队列与账号额度。</p>
 {models.map((p,index)=><fieldset className="model-profile" key={p.id} disabled={disabled}><legend>{p.display_name||'新模型'}</legend><div className="admin-panel-title"><label className="admin-switch"><input type="checkbox" checked={p.enabled} onChange={e=>update(index,'enabled',e.target.checked)}/>允许用户选择</label><button type="button" className="text-button" aria-label={'移除模型 '+p.display_name} onClick={()=>onChange(models.filter((_,i)=>i!==index))}><Trash2 size={15}/>移除</button></div><div className="admin-fields">
 {[["display_name","显示名称"],["model","接口 model 名称"],["base_url","API Base URL"],["api_key","API 密钥"],["context_window","模型上下文容量"]].map(([key,label])=><label key={key}>{label}<input aria-label={p.display_name+' '+label} type={key==='api_key'?'password':key==='context_window'?'number':'text'} value={p[key]??''} min={key==='context_window'?8192:undefined} max={key==='context_window'?262144:undefined} autoComplete={key==='api_key'?'new-password':'off'} onChange={e=>update(index,key,e.target.value)}/>{key==='api_key'&&<small>{p.key_configured?'已配置 · 留空保持不变':'尚未配置'}</small>}</label>)}
 <label>接口适配<select aria-label={p.display_name+' 接口适配'} value={p.protocol} onChange={e=>update(index,'protocol',e.target.value)}><option value="baseline">在线 Qwen 兼容接口</option><option value="team">自部署模型接口</option></select></label>
 </div><ModelCapabilities value={p} onChange={value=>onChange(models.map((old,i)=>i===index?value:old))}/><ModelTest id={p.id} disabled={disabled}/></fieldset>)}{!models.length&&<p className="admin-hint">尚未添加其他模型。</p>}</section>;
}
