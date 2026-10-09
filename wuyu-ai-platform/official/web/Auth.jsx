import React,{useState,useEffect} from 'react';
import {ArrowRight,LoaderCircle} from 'lucide-react';
import {api} from './api';
import logo from './logo.png';
import AuthAnimation from './AuthAnimation';

export default function Auth({onSuccess}){
  const [config,setConfig]=useState(null),[registering,setRegistering]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [form,setForm]=useState({email:'',password:'',confirm:'',name:'',organization:'',identity:'科研院所'});
  useEffect(()=>{api('/public').then(setConfig).catch(e=>setError(e.message));},[]);
  const field=key=>({value:form[key],onChange:e=>setForm({...form,[key]:e.target.value})});
  function changeMode(){setRegistering(!registering);setError('');setForm(f=>({...f,password:'',confirm:''}));}
  async function submit(event){
    event.preventDefault();setError('');
    if(registering&&form.password!==form.confirm){setError('两次输入的密码不一致。');return;}
    if(registering&&!config?.registration_open){setError('暂未开放新用户注册。');return;}
    setBusy(true);
    try{
      const body={email:form.email,password:form.password,...(registering?{name:form.name,organization:form.organization,identity:form.identity}:{})};
      const result=await api('/auth/'+(registering?'register':'login'),{method:'POST',body:JSON.stringify(body)});
      onSuccess(result.user);
    }catch(e){setError(e.message);}finally{setBusy(false);}
  }
  return <main className="auth-page"><div className="auth-layout">
    <section className="auth-art"><div className="auth-brand"><img src={logo} alt="无隅循环 Logo"/><strong>无隅AI平台</strong></div><AuthAnimation/><div className="auth-art-copy"><h2>让每一次探索<br/>都有专业的回应</h2></div></section>
    <section className="auth-right"><div className={`auth-box ${registering?'register-box':'signin-box'}`}><h1>{registering?'创建账号':'欢迎回来'}</h1><p className="auth-subtitle">{registering?'注册无隅 AI，开启专业探索':'登录无隅 AI，继续专业探索'}</p>
      <form className="login-form" onSubmit={submit}>
        <label>邮箱<input {...field('email')} type="email" required maxLength={254} autoComplete="username" placeholder="请输入邮箱地址"/></label>
        <label>密码<input {...field('password')} type="password" required minLength={8} maxLength={128} autoComplete={registering?'new-password':'current-password'} placeholder={registering?'设置密码，至少 8 个字符':'请输入密码'}/></label>
        {registering&&<><label>确认密码<input {...field('confirm')} type="password" required minLength={8} maxLength={128} autoComplete="new-password" placeholder="请再次输入密码"/></label><div className="auth-profile"><label>姓名<input {...field('name')} required maxLength={60} autoComplete="name" placeholder="请输入姓名"/></label><label>身份<select {...field('identity')}>{(config?.identities||['科研院所']).map(identity=><option key={identity}>{identity}</option>)}</select></label></div><label>所在机构<input {...field('organization')} required maxLength={160} autoComplete="organization" placeholder="请输入单位、学校或机构名称"/></label></>}
        {error&&<p className="form-error" role="alert">{error}</p>}
        <button className="auth-submit" disabled={busy||(registering&&!config?.registration_open)}>{busy?<LoaderCircle size={18} className="spin"/>:<ArrowRight size={18}/>} {registering?'注册并登录':'登录'}</button>
      </form>
      <p className="auth-switch">{registering?'已有账号？':'还没有账号？'}<button disabled={busy} onClick={changeMode}>{registering?'去登录':'立即注册'}</button></p>
    </div></section>
  </div></main>;
}
