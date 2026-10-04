from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loguru import logger
from telethon.errors import ChannelPrivateError, ChatAdminRequiredError, RPCError

from models import (
    AnalysisReport,
    AnalysisResult,
    ChannelRunStats,
    CliOptions,
    CommentAuthorRecord,
    ResolvedChannelTarget,
    UniqueUserRecord,
)
from raw_comment_spool import RawCommentSpool
from telegram_client import FloodWaitLimitExceeded, TelegramApiClient
from utils import ProgressBar, build_profile_url

RECOVERABLE_SOURCE_ERRORS = (
    ChannelPrivateError,
    ChatAdminRequiredError,
    RPCError,
    ValueError,
    FloodWaitLimitExceeded,
    ConnectionError,
    OSError,
    asyncio.TimeoutError,
)


class ChannelAnalyzer:
    def __init__(self, telegram_client: TelegramApiClient, options: CliOptions) -> None:
        self.telegram_client = telegram_client
        self.options = options

    async def analyze(self, references: list[str]) -> AnalysisResult:
        report = AnalysisReport(
            channels_total=len(references),
            target_unique_users=self.options.target_users,
            only_with_username=self.options.only_with_username,
            started_at=datetime.now(timezone.utc),
        )
        unique_users: dict[int, UniqueUserRecord] = {}
        source_sets: dict[int, set[str]] = {}
        raw_comments_spool = (
            None
            if self.options.dry_run
            else RawCommentSpool(self.options.output_path.parent, self.options.output_path.stem)
        )

        channels_progress = ProgressBar(total=len(references), prefix="Источники")

        try:
            try:
                for index, reference in enumerate(references, start=1):
                    if self._target_reached(unique_users):
                        logger.info(
                            "Цель в {} уникальных пользователей достигнута. Остальные источники пропущены.",
                            self.options.target_users,
                        )
                        break

                    logger.info(
                        "Обработка ссылки {}/{}: {}",
                        index,
                        len(references),
                        reference,
                    )
                    channel_stats = await self._analyze_reference(
                        reference=reference,
                        raw_comments_spool=raw_comments_spool,
                        unique_users=unique_users,
                        source_sets=source_sets,
                        report=report,
                    )
                    report.channel_stats.append(channel_stats)
                    channels_progress.advance()
                    if self._target_reached(unique_users):
                        logger.info(
                            "Цель в {} уникальных пользователей достигнута.",
                            self.options.target_users,
                        )
                        break
            finally:
                channels_progress.finish()
        except Exception:
            if raw_comments_spool is not None:
                raw_comments_spool.cleanup()
            raise
        finally:
            if raw_comments_spool is not None:
                raw_comments_spool.close()

        report.unique_authors_found = len(unique_users)
        report.target_reached = self._target_reached(unique_users)
        report.finished_at = datetime.now(timezone.utc)

        unique_user_rows = sorted(unique_users.values(), key=lambda item: item.user_id)

        return AnalysisResult(
            unique_users=unique_user_rows,
            raw_comments_path=raw_comments_spool.path if raw_comments_spool is not None else None,
            raw_comments_count=raw_comments_spool.count if raw_comments_spool is not None else 0,
            report=report,
        )

    async def _analyze_reference(
        self,
        *,
        reference: str,
        raw_comments_spool: RawCommentSpool | None,
        unique_users: dict[int, UniqueUserRecord],
        source_sets: dict[int, set[str]],
        report: AnalysisReport,
    ) -> ChannelRunStats:
        try:
            target = await self.telegram_client.resolve_target(reference)
        except RECOVERABLE_SOURCE_ERRORS as exc:
            report.channels_skipped += 1
            logger.warning("Источник {} пропущен из-за ошибки Telegram: {}", reference, exc)
            return ChannelRunStats(
                input_reference=reference,
                source_channel=None,
                discussion_group=None,
                has_discussion_group=False,
                skipped_reason=f"Ошибка доступа или чтения источника: {exc}",
            )

        if target is None:
            report.channels_skipped += 1
            return ChannelRunStats(
                input_reference=reference,
                source_channel=None,
                discussion_group=None,
                has_discussion_group=False,
                skipped_reason="Ссылка не была разрешена или тип сущности не поддерживается.",
            )

        report.channels_processed += 1
        channel_stats = ChannelRunStats(
            input_reference=reference,
            source_channel=target.source_channel_label,
            discussion_group=target.discussion_group_label,
            has_discussion_group=target.discussion_group_entity is not None,
        )

        if target.scan_mode == "group_messages":
            return await self._analyze_group_messages(
                target=target,
                channel_stats=channel_stats,
                raw_comments_spool=raw_comments_spool,
                unique_users=unique_users,
                source_sets=source_sets,
                report=report,
            )

        if target.discussion_group_entity is None:
            report.channels_without_discussion_group += 1
            channel_stats.skipped_reason = "Связанная discussion group не найдена."
            logger.info(
                "Комментарии для {} пропущены: связанная discussion group отсутствует.",
                target.source_channel_label,
            )
            return channel_stats

        return await self._analyze_discussion_comments(
            target=target,
            channel_stats=channel_stats,
            raw_comments_spool=raw_comments_spool,
            unique_users=unique_users,
            source_sets=source_sets,
            report=report,
        )

    async def _analyze_discussion_comments(
        self,
        *,
        target: ResolvedChannelTarget,
        channel_stats: ChannelRunStats,
        raw_comments_spool: RawCommentSpool | None,
        unique_users: dict[int, UniqueUserRecord],
        source_sets: dict[int, set[str]],
        report: AnalysisReport,
    ) -> ChannelRunStats:
        post_limit = self._history_limit_for_target()
        post_progress = ProgressBar(
            total=post_limit,
            prefix=f"Посты {target.source_channel_label}",
        )
        channel_unique_user_ids: set[int] = set()

        try:
            async for post in self.telegram_client.iter_recent_posts(
                target.source_channel_entity,
                post_limit,
            ):
                if self._target_reached(unique_users):
                    break

                channel_stats.posts_scanned += 1
                report.posts_scanned += 1
                post_progress.advance()

                replies_info = getattr(post, "replies", None)
                replies_count = getattr(replies_info, "replies", 0) if replies_info else 0
                if replies_count <= 0:
                    continue

                try:
                    async for comment in self.telegram_client.iter_post_comments(
                        target.source_channel_entity,
                        post.id,
                        self.options.limit_comments_per_post,
                    ):
                        comment_date = comment.date
                        if comment_date is None:
                            continue
                        if self.options.since and comment_date.date() < self.options.since:
                            break

                        sender = await self.telegram_client.get_message_sender(comment)
                        if sender is None:
                            channel_stats.senders_skipped += 1
                            report.senders_skipped += 1
                            continue
                        if self.options.only_with_username and not sender.username:
                            continue

                        record = CommentAuthorRecord(
                            user_id=sender.id,
                            username=sender.username,
                            profile_url=build_profile_url(sender.username),
                            first_name=sender.first_name,
                            last_name=sender.last_name,
                            is_bot=bool(sender.bot),
                            source_channel=target.source_channel_label,
                            source_post_id=post.id,
                            comment_date=comment_date,
                        )
                        if raw_comments_spool is not None:
                            raw_comments_spool.append(record)
                        channel_stats.comments_processed += 1
                        report.comments_processed += 1
                        channel_unique_user_ids.add(record.user_id)
                        self._upsert_unique_user(record, unique_users, source_sets)
                        if self._target_reached(unique_users):
                            break
                except (
                    ChannelPrivateError,
                    ChatAdminRequiredError,
                    RPCError,
                    ValueError,
                ) as exc:
                    logger.warning(
                        "Не удалось получить комментарии для {} пост {}: {}",
                        target.source_channel_label,
                        post.id,
                        exc,
                    )
                    continue
                if self._target_reached(unique_users):
                    break
        except RECOVERABLE_SOURCE_ERRORS as exc:
            channel_stats.skipped_reason = f"Обработка источника остановлена из-за ошибки: {exc}"
            logger.warning(
                "Обработка {} остановлена из-за ошибки Telegram: {}",
                target.source_channel_label,
                exc,
            )
        finally:
            post_progress.finish()

        channel_stats.unique_authors = len(channel_unique_user_ids)
        logger.info(
            "Завершена обработка {}: постов={}, комментариев={}, уникальных авторов={}",
            target.source_channel_label,
            channel_stats.posts_scanned,
            channel_stats.comments_processed,
            channel_stats.unique_authors,
        )
        return channel_stats

    async def _analyze_group_messages(
        self,
        *,
        target: ResolvedChannelTarget,
        channel_stats: ChannelRunStats,
        raw_comments_spool: RawCommentSpool | None,
        unique_users: dict[int, UniqueUserRecord],
        source_sets: dict[int, set[str]],
        report: AnalysisReport,
    ) -> ChannelRunStats:
        message_limit = (
            None
            if self.options.all_messages or self.options.target_users > 0
            else self.options.limit_posts
        )
        message_progress = ProgressBar(
            total=message_limit,
            prefix=f"Сообщения {target.source_channel_label}",
        )
        channel_unique_user_ids: set[int] = set()

        try:
            async for message in self.telegram_client.iter_recent_messages(
                target.source_channel_entity,
                message_limit,
            ):
                if self._target_reached(unique_users):
                    break

                channel_stats.posts_scanned += 1
                report.posts_scanned += 1
                message_progress.advance()

                message_date = message.date
                if message_date is None:
                    continue
                if self.options.since and message_date.date() < self.options.since:
                    break

                sender = await self.telegram_client.get_message_sender(message)
                if sender is None:
                    channel_stats.senders_skipped += 1
                    report.senders_skipped += 1
                    continue
                if self.options.only_with_username and not sender.username:
                    continue

                record = CommentAuthorRecord(
                    user_id=sender.id,
                    username=sender.username,
                    profile_url=build_profile_url(sender.username),
                    first_name=sender.first_name,
                    last_name=sender.last_name,
                    is_bot=bool(sender.bot),
                    source_channel=target.source_channel_label,
                    source_post_id=message.id,
                    comment_date=message_date,
                )
                if raw_comments_spool is not None:
                    raw_comments_spool.append(record)
                channel_stats.comments_processed += 1
                report.comments_processed += 1
                channel_unique_user_ids.add(record.user_id)
                self._upsert_unique_user(record, unique_users, source_sets)
                if self._target_reached(unique_users):
                    break
        except RECOVERABLE_SOURCE_ERRORS as exc:
            channel_stats.skipped_reason = f"Обработка источника остановлена из-за ошибки: {exc}"
            logger.warning(
                "Обработка группы {} остановлена из-за ошибки Telegram: {}",
                target.source_channel_label,
                exc,
            )
        finally:
            message_progress.finish()

        channel_stats.unique_authors = len(channel_unique_user_ids)
        logger.info(
            "Завершена обработка группы {}: сообщений={}, найдено записей={}, уникальных авторов={}",
            target.source_channel_label,
            channel_stats.posts_scanned,
            channel_stats.comments_processed,
            channel_stats.unique_authors,
        )
        return channel_stats

    def _history_limit_for_target(self) -> int | None:
        if self.options.target_users > 0:
            return None
        return self.options.limit_posts

    def _target_reached(self, unique_users: dict[int, UniqueUserRecord]) -> bool:
        return self.options.target_users > 0 and len(unique_users) >= self.options.target_users

    @staticmethod
    def _upsert_unique_user(
        record: CommentAuthorRecord,
        unique_users: dict[int, UniqueUserRecord],
        source_sets: dict[int, set[str]],
    ) -> None:
        existing = unique_users.get(record.user_id)
        if existing is None:
            unique_users[record.user_id] = UniqueUserRecord(
                user_id=record.user_id,
                username=record.username,
                profile_url=record.profile_url,
                first_name=record.first_name,
                last_name=record.last_name,
                is_bot=record.is_bot,
                first_seen_comment_date=record.comment_date,
                last_seen_comment_date=record.comment_date,
                occurrences_count=1,
                source_channels_count=1,
            )
            source_sets[record.user_id] = {record.source_channel}
            return

        existing.occurrences_count += 1
        existing.first_seen_comment_date = min(
            existing.first_seen_comment_date,
            record.comment_date,
        )
        existing.last_seen_comment_date = max(
            existing.last_seen_comment_date,
            record.comment_date,
        )
        if not existing.username and record.username:
            existing.username = record.username
            existing.profile_url = record.profile_url
        if not existing.first_name and record.first_name:
            existing.first_name = record.first_name
        if not existing.last_name and record.last_name:
            existing.last_name = record.last_name

        source_sets.setdefault(record.user_id, set()).add(record.source_channel)
        existing.source_channels_count = len(source_sets[record.user_id])
