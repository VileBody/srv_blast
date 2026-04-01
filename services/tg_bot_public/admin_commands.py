"""Admin Telegram commands for credits & user management.

Access: MANAGER_CHAT_ID (owner) + any user in the admins table.
Owner can /addadmin and /removeadmin; other admins can only manage credits.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command

if TYPE_CHECKING:
    from aiogram.types import Message
    from .config import Settings
    from .credits_db import CreditsDB

log = logging.getLogger("admin_cmd")


def make_admin_router(credits_db: CreditsDB, settings: Settings) -> Router:
    router = Router(name="admin")

    async def _is_admin(msg: Message) -> bool:
        uid = msg.from_user.id if msg.from_user else 0
        if uid == settings.manager_chat_id:
            return True
        return await credits_db.is_admin(uid)

    @router.message(Command("grant"))
    async def _grant(message: Message) -> None:
        if not await _is_admin(message):
            return
        parts = (message.text or "").split()
        if len(parts) < 3:
            await message.answer("Использование: /grant <tg_id> <amount>")
            return
        try:
            tg_id = int(parts[1])
            amount = int(parts[2])
        except ValueError:
            await message.answer("tg_id и amount должны быть числами.")
            return
        new_bal = await credits_db.add_credits(tg_id, amount, "admin_grant")
        await message.answer(f"✅ Начислено {amount} кредитов пользователю {tg_id}.\nБаланс: {new_bal}")

    @router.message(Command("revoke"))
    async def _revoke(message: Message) -> None:
        if not await _is_admin(message):
            return
        parts = (message.text or "").split()
        if len(parts) < 3:
            await message.answer("Использование: /revoke <tg_id> <amount>")
            return
        try:
            tg_id = int(parts[1])
            amount = int(parts[2])
        except ValueError:
            await message.answer("tg_id и amount должны быть числами.")
            return
        new_bal = await credits_db.add_credits(tg_id, -abs(amount), "admin_revoke")
        await message.answer(f"✅ Списано {abs(amount)} кредитов у пользователя {tg_id}.\nБаланс: {new_bal}")

    @router.message(Command("balance"))
    async def _balance(message: Message) -> None:
        if not await _is_admin(message):
            return
        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.answer("Использование: /balance <tg_id>")
            return
        try:
            tg_id = int(parts[1])
        except ValueError:
            await message.answer("tg_id должен быть числом.")
            return
        bal = await credits_db.get_balance(tg_id)
        user = await credits_db.get_user(tg_id)
        uname = f"@{user['username']}" if user and user["username"] else str(tg_id)
        await message.answer(f"Баланс {uname}: {bal} кредитов")

    @router.message(Command("users"))
    async def _users(message: Message) -> None:
        if not await _is_admin(message):
            return
        users = await credits_db.list_users(limit=50)
        if not users:
            await message.answer("Пользователей нет.")
            return
        lines = ["📊 Пользователи:\n"]
        for u in users:
            uname = f"@{u['username']}" if u["username"] else str(u["tg_id"])
            lines.append(f"• {uname} (id:{u['tg_id']}) — {u['credits']} кред.")
        total = await credits_db.count_users()
        if total > 50:
            lines.append(f"\n...и ещё {total - 50}")
        await message.answer("\n".join(lines))

    @router.message(Command("addadmin"))
    async def _add_admin(message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        if uid != settings.manager_chat_id:
            return  # Only owner can add admins
        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.answer("Использование: /addadmin <tg_id> [username]")
            return
        try:
            admin_id = int(parts[1])
        except ValueError:
            await message.answer("tg_id должен быть числом.")
            return
        uname = parts[2] if len(parts) > 2 else ""
        await credits_db.add_admin(admin_id, uname)
        await message.answer(f"✅ Админ {admin_id} добавлен.")

    @router.message(Command("removeadmin"))
    async def _remove_admin(message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        if uid != settings.manager_chat_id:
            return
        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.answer("Использование: /removeadmin <tg_id>")
            return
        try:
            admin_id = int(parts[1])
        except ValueError:
            await message.answer("tg_id должен быть числом.")
            return
        await credits_db.remove_admin(admin_id)
        await message.answer(f"✅ Админ {admin_id} удалён.")

    @router.message(Command("admins"))
    async def _list_admins(message: Message) -> None:
        if not await _is_admin(message):
            return
        admins = await credits_db.list_admins()
        lines = [f"👑 Владелец: {settings.manager_chat_id}"]
        if admins:
            lines.append("\n📋 Админы:")
            for a in admins:
                uname = f"@{a['username']}" if a["username"] else str(a["tg_id"])
                lines.append(f"• {uname} (id:{a['tg_id']})")
        else:
            lines.append("\nДополнительных админов нет.")
        await message.answer("\n".join(lines))

    return router
