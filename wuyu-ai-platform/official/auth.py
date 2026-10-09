"""Email/password accounts; credentials never appear in public user objects."""
import hashlib
import hmac
import re
import secrets
import time
import uuid
from fastapi import HTTPException, Request
import store as db

COOKIE = 'wuyu_session'
IDENTITIES = ('政府及事业单位', '企业', '科研院所', '高校教师', '学生', '其他')


def normalize_email(email):
    email = str(email).strip().lower()
    if len(email) > 254 or not re.fullmatch(r'[^\s@<>\r\n]+@[^\s@<>\r\n]+\.[^\s@<>\r\n]+', email):
        raise HTTPException(422, '请输入有效邮箱。')
    return email


def validate_profile(name, organization, identity):
    if not name.strip() or not organization.strip() or len(name) > 60 or len(organization) > 160 or identity not in IDENTITIES:
        raise HTTPException(422, '请填写姓名、机构并选择身份。')


def hash_password(password):
    if not 8 <= len(password) <= 128 or not password.strip():
        raise HTTPException(422, '密码需为 8–128 个字符。')
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768, r=8, p=1, maxmem=64*1024*1024, dklen=32)
    return 'scrypt$32768$8$1$' + salt.hex() + '$' + value.hex()


DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


def check_password(password, encoded):
    try:
        algorithm,n,r,p,salt,expected = encoded.split('$')
        if (algorithm,n,r,p) != ('scrypt','32768','8','1'): return False
        salt = bytes.fromhex(salt); expected = bytes.fromhex(expected)
        if len(salt) != 16 or len(expected) != 32 or not 8 <= len(password) <= 128: return False
        value = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768, r=8, p=1, maxmem=64*1024*1024, dklen=32)
        return hmac.compare_digest(expected, value)
    except (ValueError, TypeError, AttributeError): return False


def limit_attempts(email, ip, action):
    now = time.time()
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('DELETE FROM password_attempts WHERE created<?', (now-3600,))
        if action == 'register':
            count = conn.execute("SELECT COUNT(*) FROM password_attempts WHERE ip=? AND action='register'", (ip,)).fetchone()[0]
            limited = count >= 10
        else:
            count = conn.execute("SELECT COUNT(*) FROM password_attempts WHERE ip=? AND action='login' AND created>?", (ip,now-600)).fetchone()[0]
            email_count = conn.execute("SELECT COUNT(*) FROM password_attempts WHERE email=? AND action='login' AND created>?", (email,now-600)).fetchone()[0]
            limited = count >= 60 or email_count >= 10
        if limited: raise HTTPException(429, '操作过于频繁，请稍后重试。')
        conn.execute('INSERT INTO password_attempts VALUES(?,?,?,?)', (email,ip,action,now))


def register(email, password, name, organization, identity, ip):
    email = normalize_email(email); validate_profile(name,organization,identity)
    limit_attempts(email,ip,'register')
    if not db.settings()['registration_open']: raise HTTPException(403, '暂未开放新用户注册。')
    encoded = hash_password(password); now = time.time(); uid = uuid.uuid4().hex
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        setting = conn.execute("SELECT value FROM settings WHERE key='registration_open'").fetchone()
        if setting and setting[0] == 'false': raise HTTPException(403, '暂未开放新用户注册。')
        if conn.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone():
            raise HTTPException(409, '该邮箱已注册，请直接登录。')
        conn.execute('INSERT INTO users VALUES(?,?,?,?,?,?,1,?)', (uid,email,name.strip(),organization.strip(),identity,'user',now))
        conn.execute('INSERT INTO user_passwords VALUES(?,?,?)', (uid,encoded,now))
        user = dict(conn.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    db.audit(uid,'register'); return user


def login(email, password, ip):
    email = normalize_email(email); limit_attempts(email,ip,'login')
    row = db.query('SELECT u.*,p.password_hash FROM users u LEFT JOIN user_passwords p ON p.user_id=u.id WHERE u.email=?', (email,), one=True)
    encoded = row.get('password_hash') if row else None
    valid = check_password(password,encoded or DUMMY_HASH)
    if not valid or not encoded or not row['active']: raise HTTPException(401, '邮箱或密码错误，或账号暂不可用。')
    row.pop('password_hash',None); return row


def create_admin(email, password, name, organization):
    """Local operator entry point only; never promoted by public registration."""
    email = normalize_email(email); validate_profile(name,organization,'其他')
    encoded = hash_password(password); uid = uuid.uuid4().hex; now = time.time()
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute("SELECT id FROM users WHERE role='admin'").fetchone(): raise ValueError('管理员已存在。需要恢复登录时使用 set-password。')
        if conn.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone(): raise ValueError('此邮箱已注册，请使用其他邮箱创建管理员。')
        conn.execute('INSERT INTO users VALUES(?,?,?,?,?,?,1,?)', (uid,email,name.strip(),organization.strip(),'其他','admin',now))
        conn.execute('INSERT INTO user_passwords VALUES(?,?,?)', (uid,encoded,now))
    db.audit(uid,'create_admin'); return uid


def set_password(email, password):
    """Server operator recovery, including accounts from the former OTP version."""
    email = normalize_email(email); encoded = hash_password(password)
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
        if not row: raise ValueError('账号不存在。')
        uid = row['id']
        conn.execute('INSERT OR REPLACE INTO user_passwords VALUES(?,?,?)', (uid,encoded,time.time()))
        conn.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        conn.execute('DELETE FROM password_attempts WHERE email=?',(email,))
    db.audit(uid,'operator_password_reset')


def current(request: Request):
    token = request.cookies.get(COOKIE, '')
    user = db.query('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.hash=? AND s.expires>? AND u.active=1',
                    (db.digest(token), time.time()), one=True) if token else None
    if not user: raise HTTPException(401, '请先登录。')
    return user


def admin(request: Request):
    user = current(request)
    if user['role'] != 'admin': raise HTTPException(403, '需要系统管理员权限。')
    return user


def issue_session(user, response, secure):
    token = secrets.token_urlsafe(36)
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT id FROM users WHERE id=? AND active=1',(user['id'],)).fetchone(): raise HTTPException(401, '账号暂不可用。')
        conn.execute('INSERT INTO sessions VALUES(?,?,?)',(db.digest(token),user['id'],time.time()+7*86400))
    response.set_cookie(COOKIE,token,max_age=7*86400,httponly=True,secure=secure,samesite='strict',path='/')
    db.audit(user['id'],'login')
