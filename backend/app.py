"""Minimal OAuth exchange + Discord guild authorization backend. No mod distribution yet."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
import secrets
import time
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title='Feuerdorf RP Auth API')
CLIENT_ID = os.environ.get('DISCORD_CLIENT_ID', '1529050206266589194')
GUILD_ID = os.environ.get('DISCORD_GUILD_ID', '1528847512100671498')
REQUIRED_ROLE = os.environ.get('DISCORD_REQUIRED_ROLE_ID', '1540374634166362145')  # mandatory authorization role
REDIRECT = 'http://127.0.0.1:8765/callback'
SESSIONS = {}  # Prototype only: process memory; use durable TTL store in production


class Exchange(BaseModel):
    code: str
    code_verifier: str
    redirect_uri: str


@app.get('/health')
def health():
    return {'ok': True}


@app.post('/auth/exchange')
def exchange(payload: Exchange):
    secret = os.environ.get('DISCORD_CLIENT_SECRET')
    bot_token = os.environ.get('DISCORD_BOT_TOKEN')
    if not secret or not bot_token:
        raise HTTPException(503, 'Discord secrets are not configured on backend')
    if payload.redirect_uri != REDIRECT:
        raise HTTPException(400, 'Invalid redirect URI')
    try:
        token = requests.post('https://discord.com/api/v10/oauth2/token', data={
            'client_id': CLIENT_ID, 'client_secret': secret,
            'grant_type': 'authorization_code', 'code': payload.code,
            'redirect_uri': REDIRECT, 'code_verifier': payload.code_verifier,
        }, timeout=15)
        if token.status_code != 200:
            raise HTTPException(401, 'Discord OAuth token exchange failed')
        access_token = token.json()['access_token']
        user = requests.get('https://discord.com/api/v10/users/@me',
                            headers={'Authorization': 'Bearer ' + access_token}, timeout=15)
        if user.status_code != 200:
            raise HTTPException(401, 'Discord identity lookup failed')
        identity = user.json()
        member = requests.get(
            f'https://discord.com/api/v10/guilds/{GUILD_ID}/members/{identity["id"]}',
            headers={'Authorization': 'Bot ' + bot_token}, timeout=15)
        if member.status_code != 200:
            raise HTTPException(403, 'Not an authorized Feuerdorf RP server member')
        required_role = REQUIRED_ROLE
        if required_role and required_role not in member.json().get('roles', []):
            raise HTTPException(403, 'Required Discord role missing')
        session = secrets.token_urlsafe(40)
        SESSIONS[session] = {'user_id': identity['id'], 'expires': time.time() + 3600}
        return {'session_token': session, 'username': identity['username'], 'expires_in': 3600}
    except requests.RequestException:
        raise HTTPException(502, 'Discord API unavailable')


# Phase 3: Private mod storage. Keep MOD_STORAGE outside any web root.
import hashlib
import json
from pathlib import Path
from fastapi import Header, Request
from fastapi.responses import StreamingResponse

MOD_STORAGE = Path(os.environ.get('MOD_STORAGE', str(Path(__file__).parent / 'private_mods'))).resolve()


def authorized(authorization: str):
    if not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Authentication required')
    token = authorization[7:]
    session = SESSIONS.get(token)
    if not session or session['expires'] < time.time():
        SESSIONS.pop(token, None)
        raise HTTPException(401, 'Session expired')
    bot = os.environ.get('DISCORD_BOT_TOKEN')
    if not bot:
        raise HTTPException(503, 'Backend not configured')
    try:
        member = requests.get(
            f'https://discord.com/api/v10/guilds/{GUILD_ID}/members/{session["user_id"]}',
            headers={'Authorization': 'Bot ' + bot}, timeout=15)
    except requests.RequestException:
        raise HTTPException(503, 'Discord membership check unavailable')
    if member.status_code == 429 or member.status_code >= 500:
        raise HTTPException(503, 'Discord role verification temporarily unavailable')
    if member.status_code != 200 or REQUIRED_ROLE not in member.json().get('roles', []):
        SESSIONS.pop(token, None)
        raise HTTPException(403, 'Required Discord role missing; access revoked')
    return session


def mod_files():
    if not MOD_STORAGE.is_dir():
        return []
    return sorted(p for p in MOD_STORAGE.iterdir()
                  if p.is_file() and not p.is_symlink() and p.suffix.lower() == '.zip')


def metadata(p):
    digest = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    return {'filename': p.name, 'size': p.stat().st_size, 'sha256': digest.hexdigest()}


@app.get('/mods/manifest')
def manifest(authorization: str = Header(default='')):
    authorized(authorization)
    source = os.environ.get('MOD_SOURCE', 'local').lower()
    if source == 'xrealm':
        manifest_path = Path(__file__).resolve().parent / 'xrealm_manifest.json'
        if not manifest_path.is_file():
            raise HTTPException(503, 'xREALM-Manifest fehlt; bitte zuerst erstellen')
        return json.loads(manifest_path.read_text(encoding='utf-8'))
    return {'mods': [metadata(p) for p in mod_files()]}


@app.get('/mods/file/{filename}')
def download(filename: str, request: Request, authorization: str = Header(default='')):
    authorized(authorization)
    found = next((p for p in mod_files() if p.name == filename), None)
    if found is None:
        raise HTTPException(404, 'Mod not found')
    size = found.stat().st_size
    start = 0
    range_header = request.headers.get('range')
    if range_header:
        import re
        match = re.fullmatch(r'bytes=(\d+)-', range_header.strip())
        if not match:
            raise HTTPException(416, 'Unsupported byte range', headers={'Content-Range': f'bytes */{size}'})
        start = int(match.group(1))
        if start >= size:
            raise HTTPException(416, 'Byte range not satisfiable', headers={'Content-Range': f'bytes */{size}'})

    def chunks():
        with found.open('rb') as stream:
            stream.seek(start)
            remaining = size - start
            while remaining:
                try:
                    authorized(authorization)
                except HTTPException:
                    return
                data = stream.read(min(1024 * 1024, remaining))
                if not data:
                    return
                remaining -= len(data)
                yield data

    safe_name = found.name.replace('"', '')
    headers = {
        'Content-Length': str(size - start),
        'Content-Disposition': f'attachment; filename="{safe_name}"',
        'Cache-Control': 'no-store',
        'Accept-Ranges': 'bytes',
        'X-Content-Type-Options': 'nosniff',
    }
    if range_header:
        headers['Content-Range'] = f'bytes {start}-{size - 1}/{size}'
    return StreamingResponse(chunks(), status_code=206 if range_header else 200,
                             media_type='application/zip', headers=headers)
