from __future__ import annotations

import asyncio
import getpass
from collections.abc import AsyncIterator, Awaitable, Callable

from loguru import logger
from telethon import TelegramClient
from telethon.errors import (
    AuthRestartError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodWaitError,
    MessageIdInvalidError,
    MsgIdInvalidError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeHashEmptyError,
    PhoneCodeInvalidError,
    PhoneCodeEmptyError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    RPCError,
    SessionPasswordNeededError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel, Chat, Message, PeerChannel, User

from models import ApiCredentials, ResolvedChannelTarget

AUTH_REQUEST_TIMEOUT = 60


class FloodWaitLimitExceeded(RuntimeError):
    def __init__(self, context: str, wait_seconds: int, max_wait_seconds: int) -> None:
        super().__init__(
            f"Telegram запросил FloodWait {wait_seconds} сек. для '{context}', "
            f"что больше лимита {max_wait_seconds} сек."
        )
        self.context = context
        self.wait_seconds = wait_seconds
        self.max_wait_seconds = max_wait_seconds


class TelegramAuthError(RuntimeError):
    """Ошибка авторизации Telegram, которую CLI должен показать без traceback."""


class TelegramApiClient:
    def __init__(
        self,
        credentials: ApiCredentials,
        *,
        max_retries: int = 3,
        max_flood_wait: int = 300,
    ) -> None:
        self.credentials = credentials
        self.max_retries = max_retries
        self.max_flood_wait = max_flood_wait
        self._client = self._build_client()

    def _build_client(self) -> TelegramClient:
        return TelegramClient(
            self.credentials.session_name,
            self.credentials.api_id,
            self.credentials.api_hash,
        )

    async def connect(self) -> None:
        await self._client.connect()
        if await self._client.is_user_authorized():
            logger.info("Telegram-сессия успешно инициализирована.")
            return

        try:
            await self._authorize_interactively()
        except TelegramAuthError:
            raise
        except (
            PhoneNumberInvalidError,
            PhoneNumberBannedError,
            PhoneCodeExpiredError,
            PhoneCodeInvalidError,
            PhoneCodeEmptyError,
            PhoneCodeHashEmptyError,
            SessionPasswordNeededError,
            PasswordHashInvalidError,
            AuthRestartError,
            RuntimeError,
        ) as exc:
            raise TelegramAuthError(f"Не удалось авторизоваться в Telegram: {exc}") from exc

        logger.info("Telegram-сессия успешно инициализирована.")

    async def disconnect(self) -> None:
        if self._client.is_connected():
            await self._client.disconnect()
            logger.info("Telegram-сессия закрыта.")

    async def _reset_client(self) -> None:
        if self._client.is_connected():
            await self._client.disconnect()
        self._client = self._build_client()

    async def _authorize_interactively(self) -> None:
        if self.credentials.phone_number:
            print("Использую PHONE_NUMBER из .env.", flush=True)
            phone = self.credentials.phone_number
        else:
            phone = input("Введите номер телефона Telegram в формате +79991234567: ").strip()
        if not phone:
            raise TelegramAuthError("Номер телефона не указан.")

        print(
            f"Отправляю код подтверждения Telegram. Таймаут: {AUTH_REQUEST_TIMEOUT} сек.",
            flush=True,
        )
        await self._send_login_code(phone)
        await self._sign_in_with_code(phone)

    async def _send_login_code(self, phone: str) -> None:
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info("Отправляем код подтверждения Telegram...")
                await asyncio.wait_for(
                    self._client.send_code_request(phone),
                    timeout=AUTH_REQUEST_TIMEOUT,
                )
                logger.info("Код подтверждения отправлен. Введите последний полученный код.")
                print("Код подтверждения отправлен. Введите последний полученный код.", flush=True)
                return
            except asyncio.TimeoutError as exc:
                raise TelegramAuthError(
                    f"Telegram не ответил на отправку кода за {AUTH_REQUEST_TIMEOUT} сек. "
                    "Проверьте сеть/прокси, подождите 1-2 минуты и запустите снова."
                ) from exc
            except AuthRestartError as exc:
                if attempt >= max_attempts:
                    raise TelegramAuthError(
                        "Telegram вернул AuthRestartError при отправке кода. "
                        "Подождите 1-2 минуты и запустите программу снова."
                    ) from exc
                logger.warning(
                    "Telegram попросил перезапустить авторизацию. Повтор {}/{} через 3 сек.",
                    attempt + 1,
                    max_attempts,
                )
                await self._reset_client()
                await self._client.connect()
                await asyncio.sleep(3)
            except FloodWaitError as exc:
                wait_seconds = int(exc.seconds) + 1
                if wait_seconds > self.max_flood_wait:
                    raise TelegramAuthError(
                        f"Telegram ограничил отправку кода на {wait_seconds} сек., "
                        f"это больше лимита {self.max_flood_wait} сек. "
                        "Подождите и запустите программу позже."
                    ) from exc
                logger.warning(
                    "Telegram ограничил отправку кода. Ожидание {} сек.",
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)

    async def _sign_in_with_code(self, phone: str) -> None:
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            code = input("Введите код подтверждения Telegram: ").strip().replace(" ", "")
            if not code:
                logger.warning("Код пустой. Попытка {}/{}.", attempt, max_attempts)
                continue

            try:
                await asyncio.wait_for(
                    self._client.sign_in(phone=phone, code=code),
                    timeout=AUTH_REQUEST_TIMEOUT,
                )
                return
            except SessionPasswordNeededError:
                await self._sign_in_with_two_factor_password(phone)
                return
            except asyncio.TimeoutError as exc:
                raise TelegramAuthError(
                    f"Telegram не ответил на проверку кода за {AUTH_REQUEST_TIMEOUT} сек. "
                    "Проверьте сеть/прокси и запустите программу снова."
                ) from exc
            except PhoneCodeExpiredError as exc:
                raise TelegramAuthError(
                    "Код подтверждения истек. Запустите программу снова и введите новый код."
                ) from exc
            except (PhoneCodeInvalidError, PhoneCodeEmptyError, PhoneCodeHashEmptyError) as exc:
                if attempt >= max_attempts:
                    raise TelegramAuthError(
                        "Код подтверждения не принят после нескольких попыток. "
                        "Запустите программу снова и используйте последний код из Telegram."
                    ) from exc
                logger.warning("Код не принят. Попытка {}/{}.", attempt + 1, max_attempts)
            except AuthRestartError as exc:
                raise TelegramAuthError(
                    "Telegram попросил перезапустить авторизацию после ввода кода. "
                    "Подождите 1-2 минуты и запустите программу снова."
                ) from exc

    async def _sign_in_with_two_factor_password(self, phone: str) -> None:
        print("Аккаунт защищен 2FA. Нужен облачный пароль Telegram.", flush=True)
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            password = self._read_two_factor_password(attempt)
            if not password:
                logger.warning("Пароль 2FA пустой. Попытка {}/{}.", attempt, max_attempts)
                continue

            try:
                await asyncio.wait_for(
                    self._client.sign_in(phone=phone, password=password),
                    timeout=AUTH_REQUEST_TIMEOUT,
                )
                return
            except PasswordHashInvalidError as exc:
                if self.credentials.two_factor_password or attempt >= max_attempts:
                    raise TelegramAuthError(
                        "Пароль 2FA Telegram неверный. Проверьте пароль и запустите программу снова."
                    ) from exc
                logger.warning("Пароль 2FA не принят. Попытка {}/{}.", attempt + 1, max_attempts)
            except asyncio.TimeoutError as exc:
                raise TelegramAuthError(
                    f"Telegram не ответил на проверку 2FA-пароля за {AUTH_REQUEST_TIMEOUT} сек. "
                    "Проверьте сеть/прокси и запустите программу снова."
                ) from exc

        raise TelegramAuthError("Пароль 2FA не введен.")

    def _read_two_factor_password(self, attempt: int) -> str:
        if self.credentials.two_factor_password:
            if attempt == 1:
                print("Использую TWO_FACTOR_PASSWORD из .env.", flush=True)
                return self.credentials.two_factor_password
            return ""

        try:
            return getpass.getpass("Введите пароль 2FA Telegram: ")
        except Exception:
            print(
                "Скрытый ввод пароля не сработал в этой консоли. "
                "Введите пароль 2FA Telegram обычным вводом:",
                flush=True,
            )
            return input().strip()

    async def resolve_target(self, reference: str) -> ResolvedChannelTarget | None:
        try:
            entity = await self._call_with_retry(
                lambda: self._client.get_entity(reference),
                context=f"resolve entity {reference}",
            )
        except (
            UsernameInvalidError,
            UsernameNotOccupiedError,
            ChannelPrivateError,
            ValueError,
            FloodWaitLimitExceeded,
        ) as exc:
            logger.warning("Ссылка {} пропущена: {}", reference, exc)
            return None

        if isinstance(entity, Chat):
            logger.info(
                "Ссылка {} распознана как обычная группа. Будут анализироваться сообщения группы.",
                reference,
            )
            return ResolvedChannelTarget(
                input_reference=reference,
                source_channel_entity=entity,
                discussion_group_entity=None,
                source_channel_label=self._entity_label(entity) or str(entity.id),
                discussion_group_label=None,
                scan_mode="group_messages",
            )

        if not isinstance(entity, Channel):
            logger.warning(
                "Ссылка {} пропущена: сущность не является каналом или супергруппой, поддерживаемой Telethon.",
                reference,
            )
            return None

        if getattr(entity, "broadcast", False):
            discussion_group = await self.get_linked_entity(entity)
            return ResolvedChannelTarget(
                input_reference=reference,
                source_channel_entity=entity,
                discussion_group_entity=discussion_group,
                source_channel_label=self._entity_label(entity) or str(entity.id),
                discussion_group_label=self._entity_label(discussion_group),
                scan_mode="channel_discussion_comments",
            )

        if getattr(entity, "megagroup", False):
            linked_channel = await self.get_linked_entity(entity)
            if not isinstance(linked_channel, Channel) or not getattr(linked_channel, "broadcast", False):
                logger.info(
                    "Ссылка {} распознана как группа/супергруппа без связанного канала. Будут анализироваться сообщения группы.",
                    reference,
                )
                return ResolvedChannelTarget(
                    input_reference=reference,
                    source_channel_entity=entity,
                    discussion_group_entity=None,
                    source_channel_label=self._entity_label(entity) or str(entity.id),
                    discussion_group_label=None,
                    scan_mode="group_messages",
                )

            return ResolvedChannelTarget(
                input_reference=reference,
                source_channel_entity=linked_channel,
                discussion_group_entity=entity,
                source_channel_label=self._entity_label(linked_channel)
                or str(linked_channel.id),
                discussion_group_label=self._entity_label(entity),
                scan_mode="channel_discussion_comments",
            )

        logger.warning("Ссылка {} пропущена: неподдерживаемый тип Telegram-сущности.", reference)
        return None

    async def get_linked_entity(self, entity: Channel) -> Channel | None:
        try:
            full_channel = await self._call_with_retry(
                lambda: self._client(GetFullChannelRequest(entity)),
                context=f"load full info for {self._entity_label(entity)}",
            )
        except (
            ChannelPrivateError,
            ChatAdminRequiredError,
            ValueError,
            FloodWaitLimitExceeded,
        ) as exc:
            logger.warning(
                "Не удалось получить связанную группу/чат для {}: {}",
                self._entity_label(entity),
                exc,
            )
            return None

        linked_chat_id = getattr(full_channel.full_chat, "linked_chat_id", None)
        if not linked_chat_id:
            return None

        try:
            linked_entity = await self._call_with_retry(
                lambda: self._client.get_entity(PeerChannel(linked_chat_id)),
                context=f"resolve linked chat {linked_chat_id}",
            )
        except (
            ChannelPrivateError,
            ChatAdminRequiredError,
            ValueError,
            FloodWaitLimitExceeded,
        ) as exc:
            logger.warning(
                "Не удалось разрешить связанный чат {} для {}: {}",
                linked_chat_id,
                self._entity_label(entity),
                exc,
            )
            return None

        if isinstance(linked_entity, Channel):
            return linked_entity
        return None

    async def iter_recent_messages(
        self,
        peer: Channel | Chat,
        limit: int | None,
    ) -> AsyncIterator[Message]:
        async for message in self._client.iter_messages(peer, limit=limit):
            if message is None or message.id is None:
                continue
            if getattr(message, "action", None) is not None:
                continue
            yield message

    async def iter_recent_posts(
        self,
        channel: Channel,
        limit: int | None,
    ) -> AsyncIterator[Message]:
        async for message in self.iter_recent_messages(channel, limit):
            yield message

    async def iter_post_comments(
        self,
        channel: Channel,
        post_id: int,
        limit: int,
    ) -> AsyncIterator[Message]:
        offset_id = 0
        remaining = limit

        while remaining > 0:
            batch_limit = min(100, remaining)
            messages = await self._call_with_retry(
                lambda: self._client.get_messages(
                    channel,
                    limit=batch_limit,
                    reply_to=post_id,
                    offset_id=offset_id,
                ),
                context=f"load comments for post {post_id}",
            )
            batch = list(messages)
            if not batch:
                break

            for message in batch:
                if message is None or message.id is None:
                    continue
                yield message

            remaining -= len(batch)
            offset_id = batch[-1].id
            if len(batch) < batch_limit:
                break

    async def get_message_sender(self, message: Message) -> User | None:
        sender = message.sender
        if isinstance(sender, User):
            return sender

        try:
            loaded_sender = await self._call_with_retry(
                message.get_sender,
                context=f"load sender for comment {message.id}",
            )
        except (
            ChannelPrivateError,
            ChatAdminRequiredError,
            ValueError,
            RPCError,
            FloodWaitLimitExceeded,
        ) as exc:
            logger.warning("Не удалось загрузить отправителя сообщения {}: {}", message.id, exc)
            return None

        if isinstance(loaded_sender, User):
            return loaded_sender
        logger.debug(
            "Отправитель сообщения {} пропущен: тип {}",
            message.id,
            type(loaded_sender).__name__,
        )
        return None

    async def get_comment_sender(self, message: Message) -> User | None:
        return await self.get_message_sender(message)

    async def _call_with_retry(
        self,
        operation: Callable[[], Awaitable[object]],
        *,
        context: str,
    ) -> object:
        attempt = 0
        while True:
            try:
                return await operation()
            except FloodWaitError as exc:
                wait_seconds = int(exc.seconds) + 1
                if wait_seconds > self.max_flood_wait:
                    raise FloodWaitLimitExceeded(
                        context,
                        wait_seconds,
                        self.max_flood_wait,
                    ) from exc
                logger.warning(
                    "FloodWait при попытке выполнить '{}'. Ожидание {} сек.",
                    context,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
            except (
                ConnectionError,
                asyncio.TimeoutError,
                OSError,
                RPCError,
            ) as exc:
                attempt += 1
                if isinstance(
                    exc,
                    (
                        ChannelPrivateError,
                        ChatAdminRequiredError,
                        MessageIdInvalidError,
                        MsgIdInvalidError,
                        UsernameInvalidError,
                        UsernameNotOccupiedError,
                    ),
                ):
                    raise
                if attempt > self.max_retries:
                    raise
                delay = min(2**attempt, 10)
                logger.warning(
                    "Повтор после ошибки во время '{}': {}. Попытка {}/{} через {} сек.",
                    context,
                    exc,
                    attempt,
                    self.max_retries,
                    delay,
                )
                await asyncio.sleep(delay)

    @staticmethod
    def _entity_label(entity: Channel | Chat | None) -> str | None:
        if entity is None:
            return None
        username = getattr(entity, "username", None)
        title = getattr(entity, "title", None) or str(getattr(entity, "id", "unknown"))
        if username:
            return f"https://t.me/{username}"
        return title
