"""Display Telegram's delivery receipt and resend only on explicit terminal input."""

import getpass
import math
import time

from .login import SetupError

DELIVERY = {
    "SentCodeTypeApp": "в служебный чат Telegram на устройствах, где вы уже вошли в этот аккаунт",
    "SentCodeTypeSms": "по SMS на указанный номер",
    "SentCodeTypeCall": "голосом во входящем телефонном звонке",
    "SentCodeTypeEmailCode": "на почту, выбранную для входа в Telegram",
    "SentCodeTypeMissedCall": "через пропущенный звонок: кодом служат последние цифры номера",
    "SentCodeTypeFlashCall": "через короткий звонок: кодом служит номер входящего звонка",
    "SentCodeTypeSmsWord": "по SMS: кодом служит слово из сообщения",
    "SentCodeTypeSmsPhrase": "по SMS: кодом служит фраза из сообщения",
}


async def resend_code(client, phone, code_hash):
    from telethon.tl.functions.auth import ResendCodeRequest

    # Unlike send_code_request, this does not silently start a new sendCode flow
    # after an expired hash. Telegram controls the permitted next delivery type.
    return await client(ResendCodeRequest(phone, code_hash))


class CodePrompt:
    def __init__(self, receipt, *, clock=time.monotonic):
        self.clock, self.resends = clock, 0
        self.update(receipt)

    def update(self, receipt):
        kind = type(getattr(receipt, "type", None)).__name__
        code_hash = getattr(receipt, "phone_code_hash", None)
        timeout = getattr(receipt, "timeout", None)
        timeout = 60 if timeout is None else timeout
        if (
            kind not in DELIVERY
            or not isinstance(code_hash, str)
            or not 0 < len(code_hash) <= 1024
            or type(timeout) is not int
            or not 0 <= timeout <= 604800
        ):
            raise SetupError("unsupported_code_delivery")
        self.receipt, self.code_hash, self.kind = receipt, code_hash, kind
        self.ready_at = self.clock() + timeout
        print(f"Telegram сообщает: код отправлен {DELIVERY[kind]}.", flush=True)
        print("Нужен код для этого входа, а не код для сайта my.telegram.org.", flush=True)
        if getattr(receipt, "next_type", None) is not None and self.resends < 2:
            print(
                f"Если код не придет, через {timeout} сек. введите resend вместо кода и нажмите Enter.",
                flush=True,
            )
        elif self.resends >= 2:
            print(
                "Лимит двух повторных запросов исчерпан. Введите полученный код или отмените вход.",
                flush=True,
            )
        else:
            print("Telegram не предлагает повторную отправку в этом запросе.", flush=True)

    async def read(self, client, phone):
        while True:
            value = getpass.getpass("Код входа или resend (ввод скрыт): ").strip()
            if value.lower() != "resend":
                return value, self.code_hash
            if self.resends >= 2:
                raise SetupError("resend_limit")
            if getattr(self.receipt, "next_type", None) is None:
                raise SetupError("resend_unavailable")
            remaining = math.ceil(self.ready_at - self.clock())
            if remaining > 0:
                print(
                    f"Повторный запрос еще не отправлен. Подождите {remaining} сек. и введите resend снова.",
                    flush=True,
                )
                continue
            print("Запрашиваю повторную отправку кода…", flush=True)
            receipt = await resend_code(client, phone, self.code_hash)
            self.resends += 1
            self.update(receipt)
