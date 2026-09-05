"""Shared upload ownership, atomic quotas and short-lived phone links."""
from __future__ import annotations
import hashlib
import json
import secrets
import time
from typing import Any
from uuid import uuid4
from . import db
from .media_uploads import MAX_SOURCE_BYTES, MAX_ACCOUNT_BYTES, MAX_ACCOUNT_FILES


def reserve(user_id: str) -> None:
    with db.transaction() as cur:
        cur.execute(db.sql('INSERT INTO web_upload_usage (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING'), (user_id,))
        cur.execute(db.sql('UPDATE web_upload_usage SET file_count=file_count+1, byte_count=byte_count+%s WHERE user_id=%s AND file_count<%s AND byte_count+%s<=%s'),
                    (MAX_SOURCE_BYTES, user_id, MAX_ACCOUNT_FILES, MAX_SOURCE_BYTES, MAX_ACCOUNT_BYTES))
        if cur.rowcount != 1:
            raise ValueError('Лимит исходников: 50 файлов или 2 ГБ на аккаунт. Удалите ненужные файлы.')


def release(user_id: str) -> None:
    with db.transaction() as cur:
        cur.execute(db.sql('UPDATE web_upload_usage SET file_count=file_count-1, byte_count=byte_count-%s WHERE user_id=%s'), (MAX_SOURCE_BYTES, user_id))


def save(user_id: str, project_id: str, kind: str, metadata: dict[str, Any]) -> dict[str, Any]:
    asset = {**metadata, 'id': 'src_' + uuid4().hex, 'projectId': project_id, 'kind': kind}
    with db.transaction() as cur:
        cur.execute(db.sql('INSERT INTO web_upload_assets (id,user_id,project_id,kind,metadata) VALUES (%s,%s,%s,%s,%s)'),
                    (asset['id'], user_id, project_id, kind, json.dumps(asset)))
        cur.execute(db.sql('UPDATE web_upload_usage SET byte_count=byte_count-%s+%s WHERE user_id=%s'), (MAX_SOURCE_BYTES, asset['bytes'], user_id))
    return asset


def assets(user_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
    with db.read() as cur:
        if project_id is None:
            cur.execute(db.sql('SELECT metadata FROM web_upload_assets WHERE user_id=%s'), (user_id,))
        else:
            cur.execute(db.sql('SELECT metadata FROM web_upload_assets WHERE user_id=%s AND project_id=%s'), (user_id, project_id))
        return [json.loads(row[0]) for row in cur.fetchall()]


def remove(user_id: str, source_id: str) -> None:
    with db.transaction() as cur:
        cur.execute(db.sql('SELECT metadata FROM web_upload_assets WHERE id=%s AND user_id=%s'), (source_id, user_id))
        row = cur.fetchone()
        if row:
            size = json.loads(row[0])['bytes']
            cur.execute(db.sql('DELETE FROM web_upload_assets WHERE id=%s AND user_id=%s'), (source_id, user_id))
            cur.execute(db.sql('UPDATE web_upload_usage SET file_count=file_count-1, byte_count=byte_count-%s WHERE user_id=%s'), (size, user_id))


def remove_project(user_id: str, project_id: str) -> list[dict[str, Any]]:
    with db.transaction() as cur:
        cur.execute(db.sql('SELECT metadata FROM web_upload_assets WHERE user_id=%s AND project_id=%s'), (user_id, project_id))
        removed = [json.loads(row[0]) for row in cur.fetchall()]
        cur.execute(db.sql('DELETE FROM web_upload_assets WHERE user_id=%s AND project_id=%s'), (user_id, project_id))
        cur.execute(db.sql('DELETE FROM web_upload_links WHERE user_id=%s AND project_id=%s'), (user_id, project_id))
        if removed:
            cur.execute(db.sql('UPDATE web_upload_usage SET file_count=file_count-%s, byte_count=byte_count-%s WHERE user_id=%s'),
                        (len(removed), sum(int(item['bytes']) for item in removed), user_id))
    return removed


def remove_account(user_id: str) -> None:
    with db.transaction() as cur:
        cur.execute(db.sql('DELETE FROM web_upload_assets WHERE user_id=%s'), (user_id,))
        cur.execute(db.sql('DELETE FROM web_upload_links WHERE user_id=%s'), (user_id,))
        cur.execute(db.sql('DELETE FROM web_upload_usage WHERE user_id=%s'), (user_id,))


def make_link(user_id: str, project_id: str, format: str) -> tuple[str, int]:
    if format not in {'9:16', '16:9'}:
        raise ValueError('Свои видео поддерживают 9:16 и 16:9')
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + 600
    with db.transaction() as cur:
        cur.execute(db.sql('DELETE FROM web_upload_links WHERE user_id=%s AND project_id=%s'), (user_id, project_id))
        cur.execute(db.sql('INSERT INTO web_upload_links VALUES (%s,%s,%s,%s,%s,%s)'),
                    (hashlib.sha256(token.encode()).hexdigest(), user_id, project_id, format, expires, 10))
    return token, expires


def link(token: str, *, consume: bool = False) -> dict[str, Any]:
    if len(token) < 32 or len(token) > 100:
        raise ValueError('Недействительная ссылка загрузки')
    digest = hashlib.sha256(token.encode()).hexdigest()
    with db.transaction() as cur:
        if consume:
            cur.execute(db.sql('UPDATE web_upload_links SET remaining=remaining-1 WHERE token_hash=%s AND expires_at>%s AND remaining>0'), (digest, int(time.time())))
            if cur.rowcount != 1:
                raise ValueError('Ссылка истекла или лимит загрузок исчерпан. Создайте новую на компьютере.')
        cur.execute(db.sql('SELECT user_id,project_id,format,expires_at,remaining FROM web_upload_links WHERE token_hash=%s AND expires_at>%s'), (digest, int(time.time())))
        row = cur.fetchone()
        if not row: raise ValueError('Ссылка истекла. Создайте новую на компьютере.')
        return dict(zip(('userId','projectId','format','expiresAt','remaining'), row))


def restore_link(token: str) -> None:
    """Return a reserved phone slot when media validation or storage failed."""
    if len(token) < 32 or len(token) > 100:
        return
    digest = hashlib.sha256(token.encode()).hexdigest()
    with db.transaction() as cur:
        cur.execute(db.sql('UPDATE web_upload_links SET remaining=remaining+1 WHERE token_hash=%s AND expires_at>%s AND remaining<10'),
                    (digest, int(time.time())))
