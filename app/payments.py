"""Предоплата переводом по СБП без эквайринга: клиент переводит сам, администратор подтверждает поступление."""
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .db import format_phone

STATUS = {
    "pending": "⏳ ждём оплату",
    "claimed": "🔎 клиент оплатил, проверьте",
    "paid": "✅ оплачено",
    "cancelled": "🚫 отменено",
}


def rub(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₽"


def ref(payment_id: int) -> str:
    """Короткий код для комментария к переводу — по нему легко найти платёж в банке."""
    return f"Т-{payment_id}"


def client_request_text(payment: dict, details: str) -> str:
    return (
        f"💳 <b>Предоплата за сеанс: {rub(payment['amount'])}</b>\n\n"
        f"Переведите по СБП с любого банка:\n{escape(details)}\n\n"
        f"В комментарии к переводу укажите: <code>{ref(payment['id'])}</code>\n\n"
        "После перевода нажмите кнопку ниже 👇"
    )


def claim_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Я оплатил(а)", callback_data=f"pay:claim:{payment_id}")
    ]])


def admin_check_text(payment: dict) -> str:
    return (
        f"💳 <b>Клиент сообщает об оплате</b>\n\n"
        f"👤 {escape(payment['name'])}, {format_phone(payment['phone'])}\n"
        f"💰 {rub(payment['amount'])} · комментарий <code>{ref(payment['id'])}</code>\n\n"
        "Проверьте поступление в приложении банка и нажмите кнопку."
    )


def admin_check_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Деньги пришли", callback_data=f"pay:ok:{payment_id}"),
        InlineKeyboardButton(text="❌ Не пришли", callback_data=f"pay:no:{payment_id}"),
    ]])
