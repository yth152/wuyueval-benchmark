"""Personal settings and exports, always scoped to the authenticated account."""
import json
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask
import auth
import data_export
import store as db

router = APIRouter(prefix='/api/me')


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    theme: Literal['light','dark','system'] = 'light'
    font_size: Literal['standard','large','larger'] = 'standard'
    send_key: Literal['enter','ctrl_enter'] = 'enter'


def preferences(uid):
    row = db.query('SELECT value FROM user_preferences WHERE user_id=?',(uid,),one=True)
    return Preferences(**json.loads(row['value'])).model_dump() if row else Preferences().model_dump()


@router.get('/preferences')
def get_preferences(user=Depends(auth.current)):
    return preferences(user['id'])


@router.put('/preferences')
def save_preferences(body: Preferences,user=Depends(auth.current)):
    db.execute('INSERT OR REPLACE INTO user_preferences VALUES(?,?,?)',(user['id'],body.model_dump_json(),time.time()))
    return body.model_dump()


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=60)
    organization: str = Field(min_length=1,max_length=160)
    identity: str = Field(max_length=40)


@router.patch('/profile')
def save_profile(body: ProfileInput,user=Depends(auth.current)):
    auth.validate_profile(body.name,body.organization,body.identity)
    db.execute('UPDATE users SET name=?,organization=?,identity=? WHERE id=?',
               (body.name.strip(),body.organization.strip(),body.identity,user['id']))
    db.audit(user['id'],'update_own_profile')
    return db.query('SELECT * FROM users WHERE id=?',(user['id'],),one=True)


@router.get('/data')
def personal_data(user=Depends(auth.current)):
    uid=user['id'];cfg=db.settings()
    day=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    usage=db.query("SELECT COUNT(*) AS questions,COALESCE(SUM(charged),0) AS charged,COALESCE(SUM(CASE WHEN status IN ('queued','running') THEN reserved ELSE 0 END),0) AS reserved FROM jobs WHERE user_id=? AND day=?",(uid,day),one=True)
    return {'conversations':db.query('SELECT COUNT(*) AS n FROM conversations WHERE user_id=? AND deleted=0',(uid,),one=True)['n'],
        'files':db.query('SELECT COUNT(*) AS count,COALESCE(SUM(size),0) AS bytes FROM files WHERE user_id=?',(uid,),one=True),
        'today':usage,'limits':{k:cfg[k] for k in ('daily_tokens','daily_questions','storage_mb')}}


class PersonalExportInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    include_attachments: bool = False


@router.post('/exports')
def prepare_export(body: PersonalExportInput,user=Depends(auth.current)):
    path=data_export.build_export('selected',[user['id']],body.include_attachments,user['id'])
    export_id=uuid.uuid4().hex
    target=path.with_name('wuyu-personal-'+user['id']+'-'+export_id+'.json')
    path.rename(target)
    return {'download_url':'/api/me/exports/'+export_id,'size_bytes':target.stat().st_size,'expires_in':3600}


@router.get('/exports/{export_id}')
def download_export(export_id: str,user=Depends(auth.current)):
    if len(export_id)!=32 or any(c not in '0123456789abcdef' for c in export_id): raise HTTPException(404,'导出文件不存在。')
    path=db.DATA/'exports'/('wuyu-personal-'+user['id']+'-'+export_id+'.json')
    if not path.is_file() or path.stat().st_mtime<time.time()-3600: raise HTTPException(404,'导出文件已过期，请重新导出。')
    return FileResponse(path,media_type='application/json',filename='wuyu-my-data-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json',background=BackgroundTask(data_export.remove_export,path))
