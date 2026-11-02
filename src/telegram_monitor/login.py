"""Bounded interactive login with allowlisted, credential-free error messages."""

import getpass
import re


class SetupError(ValueError):
    """A fixed diagnostic category, never provider text or user input."""


MESSAGES = {
    "api_credentials": "Проверьте API ID и API hash на my.telegram.org.",
    "phone": "Введите телефон в международном формате, например +79991234567.",
    "account": "Нужен личный Telegram-аккаунт, который состоит в указанном чате.",
    "source": "Нет подтвержденного доступа к чату. Проверьте участие в нем в Telegram.",
    "source_changed": "Имя чата теперь относится к другому чату. Настройка остановлена.",
    "code_attempts": "Код не принят после трех попыток. Перезапустите настройку и используйте новый код.",
    "password_attempts": "Пароль не принят после трех попыток. Проверьте пароль двухэтапной проверки.",
    "ApiIdInvalidError": "Telegram отклонил API ID или API hash. Проверьте их на my.telegram.org.",
    "PhoneNumberInvalidError": "Telegram не принял номер телефона. Проверьте международный формат.",
    "PhoneNumberBannedError": "Telegram ограничил вход для этого номера.",
    "PhoneCodeExpiredError": "Код входа истек. Перезапустите настройку и введите самый новый код.",
    "PhoneCodeHashEmptyError": "Запрос кода больше недействителен. Перезапустите настройку.",
    "PhoneNumberFloodError": "Telegram ограничил запросы кода. Повторите настройку позже.",
    "ChannelPrivateError": "Telegram не разрешил чтение чата. Проверьте доступ к нему в своем аккаунте.",
    "UsernameInvalidError": "Telegram не распознал имя чата.",
    "UsernameNotOccupiedError": "Чат с таким именем не найден.",
    "AuthRestartError": "Telegram попросил начать вход заново. Перезапустите настройку.",
    "AuthKeyUnregisteredError": "Сессия Telegram недействительна. Повторите авторизацию.",
}
STAGES = {
    "configuration": "проверка параметров",
    "connect": "подключение к Telegram",
    "send_code": "запрос кода входа",
    "sign_in": "вход в аккаунт",
    "source": "проверка доступа к чату",
    "save": "сохранение подключения",
}


def failure_message(error, stage):
    if isinstance(error, (KeyboardInterrupt, EOFError)):
        reason = "Ввод прерван. Для отмены используйте Ctrl+C, для нового входа запустите настройку снова."
    elif type(error).__name__ in {"FloodWaitError", "FloodPremiumWaitError"}:
        seconds = getattr(error, "seconds", None)
        reason = "Telegram ограничил частоту запросов. Повторите позже."
        if type(seconds) is int and 0 < seconds <= 604800:
            reason = f"Telegram просит подождать {seconds} сек. Не запрашивайте новый код до окончания паузы."
    elif isinstance(error, (TimeoutError, ConnectionError)):
        reason = "Не удалось дождаться ответа Telegram. Повторите позже."
    else:
        category = str(error) if isinstance(error, SetupError) else type(error).__name__
        reason = MESSAGES.get(
            category, "Не удалось завершить этот этап. Передайте это сообщение для диагностики."
        )
    return f"Подключение не завершено (этап: {STAGES.get(stage, 'настройка')}). {reason}"


async def sign_in(client, phone, code_hash):
    from telethon.errors import (
        PasswordHashInvalidError,
        PhoneCodeEmptyError,
        PhoneCodeInvalidError,
        SessionPasswordNeededError,
    )

    for attempt in range(3):
        code = re.sub(r"\s+", "", getpass.getpass("Код входа из Telegram (ввод скрыт): "))
        try:
            if not re.fullmatch(r"[0-9]{4,8}", code):
                raise SetupError("invalid_code_format")
            await client.sign_in(phone, code=code, phone_code_hash=code_hash)
            return
        except SessionPasswordNeededError:
            break
        except (PhoneCodeEmptyError, PhoneCodeInvalidError, SetupError):
            if attempt == 2:
                raise SetupError("code_attempts") from None
            print(
                "Код не принят. Введите самый новый код еще раз; новый запрос не отправляется.",
                flush=True,
            )

    for attempt in range(3):
        password = getpass.getpass("Пароль двухэтапной проверки Telegram (ввод скрыт): ")
        try:
            await client.sign_in(password=password)
            return
        except PasswordHashInvalidError:
            if attempt == 2:
                raise SetupError("password_attempts") from None
            print("Пароль не принят. Попробуйте еще раз.", flush=True)
