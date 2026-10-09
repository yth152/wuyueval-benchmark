import React,{useState} from 'react';
import {Download,LoaderCircle} from 'lucide-react';
import {api} from './api';

export default function UserExport({users}){
 const [selected,setSelected]=useState([]),[includeFiles,setIncludeFiles]=useState(true),[working,setWorking]=useState(false),[note,setNote]=useState(''),[error,setError]=useState(''),[filter,setFilter]=useState(''),[downloadUrl,setDownloadUrl]=useState('');
 const visible=users.filter(u=>[u.name,u.email,u.organization].some(v=>v.toLowerCase().includes(filter.toLowerCase())));
 function choose(id,checked){setSelected(list=>checked?[...new Set([...list,id])]:list.filter(x=>x!==id));}
 async function download(scope){setWorking(true);setNote('');setError('');setDownloadUrl('');try{
   const data=await api('/admin/exports',{method:'POST',body:JSON.stringify({scope,user_ids:scope==='all'?[]:selected,include_attachments:includeFiles})});
   setDownloadUrl(data.download_url);setNote(`已生成${scope==='all'?'全部用户':selected.length+' 位用户'}的 JSON 文件（${(data.size_bytes/1048576).toFixed(2)} MB）。点击下方链接下载。`);
 }catch(e){setError(e.message);}finally{setWorking(false);}}
 return <section className="admin-panel"><h2>用户数据导出</h2><p className="admin-hint">导出用户资料、完整问答、模型返回的思考内容、引用、用量记录与对话记忆，包含已从历史列表移除的对话。生成中的任务导出当前快照。</p>
 <div className="export-toolbar"><input value={filter} onChange={e=>setFilter(e.target.value)} placeholder="按姓名、邮箱或机构筛选" aria-label="筛选导出用户"/><span>已选 {selected.length} / {users.length} 位</span><button type="button" className="secondary" disabled={working} onClick={()=>setSelected(selected.length===users.length?[]:users.map(u=>u.id))}>{selected.length===users.length?'取消全选':'选择全部'}</button></div>
 <div className="table-wrap"><table><thead><tr><th>选择</th><th>姓名 / 邮箱</th><th>机构</th><th>身份</th><th>状态</th></tr></thead><tbody>{visible.map(u=><tr key={u.id}><td><input type="checkbox" aria-label={'选择用户 '+u.email} checked={selected.includes(u.id)} disabled={working} onChange={e=>choose(u.id,e.target.checked)}/></td><td>{u.name}<small>{u.email}</small></td><td>{u.organization}</td><td>{u.identity}</td><td>{u.active?'正常':'已停用'}</td></tr>)}{!visible.length&&<tr><td colSpan={5}>没有匹配的用户</td></tr>}</tbody></table></div>
 <label className="admin-switch"><input type="checkbox" checked={includeFiles} onChange={e=>setIncludeFiles(e.target.checked)} disabled={working}/>包含附件解析文本及图片内容</label><p className="admin-hint">勾选后文件可能较大。图片以 JPEG Base64 保存；文档导出解析文本，系统未保存原始 Office/PDF 文件。密码、登录凭证和 API 密钥不导出。</p>
 <div className="export-actions"><button className="primary" type="button" disabled={working||!selected.length} onClick={()=>download('selected')}>{working?<LoaderCircle size={16} className="spin"/>:<Download size={16}/>}导出所选用户</button><button className="secondary" type="button" disabled={working||!users.length} onClick={()=>download('all')}><Download size={16}/>导出全部用户</button></div>
 {working&&<p role="status">正在整理导出文件，请稍候…</p>}{note&&<p className="form-note" role="status">{note}</p>}{downloadUrl&&<a className="secondary export-download" href={downloadUrl} download><Download size={16}/>下载 JSON 文件</a>}{error&&<p className="form-error" role="alert">{error}</p>}
 </section>;
}
