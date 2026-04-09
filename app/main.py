from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import Database

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / 'static'

app = FastAPI(title='Messenger Lite')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')

db = Database()


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[int, set[WebSocket]] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        websockets = self.connections.get(user_id)
        if not websockets:
            return
        websockets.discard(websocket)
        if not websockets:
            self.connections.pop(user_id, None)

    async def send_to_user(self, user_id: int, payload: dict[str, Any]) -> None:
        websockets = list(self.connections.get(user_id, set()))
        if not websockets:
            return
        data = json.dumps(payload, ensure_ascii=False)
        dead: list[WebSocket] = []
        for websocket in websockets:
            try:
                await websocket.send_text(data)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            self.disconnect(user_id, websocket)

    async def send_to_users(self, user_ids: list[int], payload: dict[str, Any]) -> None:
        sent_to: set[int] = set()
        for user_id in user_ids:
            if user_id in sent_to:
                continue
            sent_to.add(user_id)
            await self.send_to_user(user_id, payload)


manager = ConnectionManager()


class RegisterPayload(BaseModel):
    nickname: str = Field(min_length=2, max_length=30)


class DirectDialogPayload(BaseModel):
    target_user_id: int


class SendMessagePayload(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.get('/')
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / 'index.html')


async def require_user(x_session_token: str | None = Header(default=None)) -> dict[str, Any]:
    if not x_session_token:
        raise HTTPException(status_code=401, detail='Missing session token')
    user = db.get_user_by_token(x_session_token)
    if not user:
        raise HTTPException(status_code=401, detail='Invalid session token')
    db.update_last_seen(user['id'])
    return user


@app.post('/api/register')
async def register(payload: RegisterPayload) -> dict[str, Any]:
    nickname = payload.nickname.strip()
    if len(nickname) < 2:
        raise HTTPException(status_code=400, detail='Nickname is too short')
    if any(ch in nickname for ch in '<>'):
        raise HTTPException(status_code=400, detail='Nickname contains invalid characters')

    session_token = secrets.token_urlsafe(32)
    user = db.create_or_login_user(nickname, session_token)
    return {
        'user': {
            'id': user['id'],
            'nickname': user['nickname'],
            'created_at': user['created_at'],
        },
        'session_token': user['session_token'],
    }


@app.get('/api/me')
async def get_me(x_session_token: str | None = Header(default=None)) -> dict[str, Any]:
    user = await require_user(x_session_token)
    return {
        'id': user['id'],
        'nickname': user['nickname'],
        'created_at': user['created_at'],
    }


@app.get('/api/users')
async def list_users(x_session_token: str | None = Header(default=None)) -> list[dict[str, Any]]:
    user = await require_user(x_session_token)
    return db.list_other_users(user['id'])


@app.get('/api/dialogs')
async def list_dialogs(x_session_token: str | None = Header(default=None)) -> list[dict[str, Any]]:
    user = await require_user(x_session_token)
    return db.list_dialogs(user['id'])


@app.post('/api/dialogs/direct')
async def create_direct_dialog(
    payload: DirectDialogPayload,
    x_session_token: str | None = Header(default=None),
) -> dict[str, Any]:
    user = await require_user(x_session_token)

    if payload.target_user_id == user['id']:
        raise HTTPException(status_code=400, detail='Cannot create dialog with yourself')

    target_user = db.get_user_by_id(payload.target_user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail='Target user not found')

    dialog = db.create_or_get_dialog(user['id'], payload.target_user_id)
    await manager.send_to_user(payload.target_user_id, {'type': 'dialogs_changed'})
    return dialog


@app.get('/api/dialogs/{dialog_id}/messages')
async def get_dialog_messages(dialog_id: int, x_session_token: str | None = Header(default=None)) -> list[dict[str, Any]]:
    user = await require_user(x_session_token)
    if not db.user_in_dialog(user['id'], dialog_id):
        raise HTTPException(status_code=403, detail='No access to dialog')
    return db.list_messages(dialog_id)


@app.post('/api/dialogs/{dialog_id}/messages')
async def send_message(
    dialog_id: int,
    payload: SendMessagePayload,
    x_session_token: str | None = Header(default=None),
) -> dict[str, Any]:
    user = await require_user(x_session_token)
    if not db.user_in_dialog(user['id'], dialog_id):
        raise HTTPException(status_code=403, detail='No access to dialog')

    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail='Message cannot be empty')

    message = db.add_message(dialog_id, user['id'], text)
    members = db.get_dialog_members(dialog_id)
    if not members:
        raise HTTPException(status_code=404, detail='Dialog not found')

    event = {'type': 'new_message', 'message': message}
    await manager.send_to_users(list(members), event)
    await manager.send_to_users(list(members), {'type': 'dialogs_changed'})
    return message


@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket, token: str = Query(default='')) -> None:
    user = db.get_user_by_token(token)
    if not user:
        await websocket.close(code=1008)
        return

    user_id = user['id']
    await manager.connect(user_id, websocket)
    await manager.send_to_user(user_id, {'type': 'connected', 'user_id': user_id})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({'type': 'error', 'text': 'Invalid JSON'}, ensure_ascii=False))
                continue

            event_type = payload.get('type')
            if event_type == 'ping':
                db.update_last_seen(user_id)
                await websocket.send_text(json.dumps({'type': 'pong'}, ensure_ascii=False))
            else:
                await websocket.send_text(json.dumps({'type': 'error', 'text': 'Unknown event'}, ensure_ascii=False))
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)
    except Exception:
        manager.disconnect(user_id, websocket)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
