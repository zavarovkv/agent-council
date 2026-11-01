"""Owner-only transport into the pinned Hermes gateway's native session authority."""

import asyncio
from contextlib import suppress

from council_owner.context import SOURCE, conversation, prompt


async def interrupt(client, session_id):
    snapshot = await client.rpc("session.resume", session_id=session_id)
    await client.rpc(
        "session.interrupt",
        session_id=session_id,
        execution_generation=snapshot["execution_generation"],
    )


async def cancel_session(session_id):
    from hermes_cli.gateway_client import connect_gateway

    async with connect_gateway() as client:
        await interrupt(client, session_id)


async def execute(job, authorized, remember, *, connect=None, pause=asyncio.sleep):
    # The same local authenticated protocol used by `hermes chat`; no second agent
    # implementation, model credentials, execution bypass or Telegram poller.
    from hermes_cli.gateway_client import GatewayClientError, connect_gateway

    async with (connect or connect_gateway)() as client:

        async def discard_events():
            while True:
                await client.events.get()

        drain = asyncio.create_task(discard_events())
        session_id = None
        submitted = False
        try:
            if not await authorized():
                raise PermissionError("Owner authority revoked")
            try:
                snapshot = await client.rpc("session.resume", title=conversation(job))
            except GatewayClientError as error:
                if str(error) != "not_found":
                    raise
                snapshot = await client.rpc(
                    "session.create",
                    request_id=job["id"],
                    source=SOURCE,
                    follow_profile_config=True,
                    title=conversation(job),
                )
            session_id = snapshot["stored_session_id"]
            remember(session_id)
            if not await authorized():
                raise PermissionError("Owner authority revoked")
            info = snapshot.get("info") or await client.rpc("session.info", session_id=session_id)
            if info.get("source") != SOURCE or info.get("profile_name") != "default":
                return "Не удалось обновить контекст этого чата. Нужна проверка сессии Council; задание не запускалось."
            pending = snapshot.get("pending", [])
            controls = snapshot.get("prompts", [])
            if any(row.get("status") == "unknown" for row in pending):
                return "Предыдущий запуск прервался с неизвестным результатом. Проверьте сессию в Council перед повтором задания."
            if controls:
                control = controls[0]
                if control.get("kind") != "clarify" or len(controls) != 1 or len(pending) != 1:
                    return "Требуется подтверждение действия в сессии Council. Автоматически подтверждать его не буду."
                admission = pending[0]["admission_id"]
                submitted = True
                await client.rpc(
                    "clarify.respond",
                    session_id=session_id,
                    execution_generation=control["execution_generation"],
                    prompt_id=control["prompt_id"],
                    answer=job["text"],
                )
            elif pending:
                return (
                    "В этом чате уже выполняется задание. Его состояние доступно в сессии Council."
                )
            else:
                submitted = True
                receipt = await client.rpc(
                    "prompt.submit",
                    session_id=session_id,
                    input_id=job["id"],
                    text=prompt(job),
                    finite=False,
                )
                admission = receipt["admission_id"]
            while True:
                if not await authorized():
                    raise PermissionError("Owner authority revoked")
                receipt = await client.rpc(
                    "prompt.receipt",
                    session_id=session_id,
                    admission_id=admission,
                    include_result=True,
                )
                if receipt.get("status") == "terminal":
                    result = receipt.get("result") or {}
                    if (
                        receipt.get("outcome") != "completed"
                        or result.get("failed")
                        or result.get("interrupted")
                    ):
                        return "Выполнение прервалось. Проверьте результат в Council перед повтором: часть действий могла успеть выполниться."
                    return (
                        result.get("final_response")
                        or "Выполнение завершилось без текстового результата. Проверьте сессию Council."
                    )
                snapshot = await client.rpc("session.resume", session_id=session_id)
                controls = snapshot.get("prompts", [])
                if controls:
                    control = controls[0]
                    if control.get("kind") == "clarify":
                        question = control.get("question") or control.get("text")
                        return (
                            "Нужно уточнение: "
                            + (
                                question
                                if isinstance(question, str) and question.strip()
                                else "откройте вопрос в сессии Council."
                            )
                            + "\nОтветьте, снова вызвав меня в этом чате."
                        )
                    return "Нужно ваше подтверждение действия в сессии Council. Ожидаю решения; задача не завершена."
                await pause(2)
        except BaseException:
            # Disconnecting a viewer does NOT stop the gateway. Explicitly interrupt
            # only this isolated conversation; never claim rollback of tool effects.
            if session_id and submitted:
                with suppress(Exception):
                    await asyncio.wait_for(interrupt(client, session_id), 10)
            raise
        finally:
            drain.cancel()
            with suppress(asyncio.CancelledError):
                await drain
