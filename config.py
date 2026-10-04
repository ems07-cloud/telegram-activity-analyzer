from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from models import ApiCredentials, AppConfig, CliOptions
from utils import LEGAL_WARNING_TEXT, ensure_output_path, parse_since_date


class ConfigError(ValueError):
    """Ошибка конфигурации CLI или окружения."""


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Значение должно быть целым числом.") from exc

    if parsed <= 0:
        raise argparse.ArgumentTypeError("Значение должно быть больше нуля.")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Значение должно быть целым числом.") from exc

    if parsed < 0:
        raise argparse.ArgumentTypeError("Значение должно быть не меньше нуля.")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Консольная утилита для анализа авторов комментариев в Telegram-каналах "
            "и discussion groups, а также сообщений обычных групп, к которым у вас есть законный доступ."
        ),
        epilog=LEGAL_WARNING_TEXT,
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("channels.txt"),
        help="Путь к текстовому файлу со ссылками на Telegram-каналы, discussion groups или обычные группы.",
    )
    parser.add_argument(
        "--limit-posts",
        type=_positive_int,
        default=100,
        help=(
            "Сколько последних постов канала или сообщений группы анализировать, "
            "если --target-users отключен."
        ),
    )
    parser.add_argument(
        "--limit-comments-per-post",
        type=_positive_int,
        default=1000,
        help="Максимум комментариев у каждого поста канала. Для обычных групп параметр не используется.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("result.csv"),
        help="Путь к основному файлу экспорта.",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("csv", "xlsx"),
        default="csv",
        help="Формат экспорта.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Запустить анализ без записи файлов экспорта.",
    )
    parser.add_argument(
        "--max-flood-wait",
        type=_non_negative_int,
        default=300,
        help=(
            "Максимальное ожидание FloodWait в секундах. Если Telegram просит больше, "
            "текущий источник пропускается. Значение 0 отключает ожидание."
        ),
    )
    parser.add_argument(
        "--target-users",
        type=_non_negative_int,
        default=450,
        help=(
            "Сколько уникальных пользователей нужно найти до остановки. "
            "По умолчанию 450. Значение 0 отключает остановку по количеству."
        ),
    )
    username_filter_group = parser.add_mutually_exclusive_group()
    username_filter_group.add_argument(
        "--only-with-username",
        dest="only_with_username",
        action="store_true",
        default=True,
        help="Оставить только пользователей с публичным username. Включено по умолчанию.",
    )
    username_filter_group.add_argument(
        "--include-without-username",
        dest="only_with_username",
        action="store_false",
        help="Включить в выгрузку пользователей без публичного username.",
    )
    parser.add_argument(
        "--all-messages",
        action="store_true",
        help=(
            "Для обычных групп: пройти по всей доступной истории сообщений и собрать "
            "всех авторов. Для каналов с комментариями параметр не используется."
        ),
    )
    parser.add_argument(
        "--since",
        type=parse_since_date,
        help="Брать только комментарии начиная с указанной даты (YYYY-MM-DD).",
    )
    return parser


def _load_credentials() -> ApiCredentials:
    load_dotenv()

    api_id_raw = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    session_name = os.getenv("SESSION_NAME", "").strip()
    phone_number = os.getenv("PHONE_NUMBER", "").strip() or None
    two_factor_password = os.getenv("TWO_FACTOR_PASSWORD", "").strip() or None

    if not api_id_raw:
        raise ConfigError("В файле .env отсутствует API_ID.")
    if not api_hash:
        raise ConfigError("В файле .env отсутствует API_HASH.")
    if not session_name:
        raise ConfigError("В файле .env отсутствует SESSION_NAME.")

    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise ConfigError("API_ID должен быть целым числом.") from exc

    return ApiCredentials(
        api_id=api_id,
        api_hash=api_hash,
        session_name=session_name,
        phone_number=phone_number,
        two_factor_password=two_factor_password,
    )


def load_app_config() -> AppConfig:
    parser = build_parser()
    args = parser.parse_args()

    credentials = _load_credentials()
    output_path = ensure_output_path(args.output, args.output_format)

    options = CliOptions(
        input_path=args.input,
        limit_posts=args.limit_posts,
        limit_comments_per_post=args.limit_comments_per_post,
        output_path=output_path,
        output_format=args.output_format,
        since=args.since,
        target_users=args.target_users,
        max_flood_wait=args.max_flood_wait,
        dry_run=args.dry_run,
        only_with_username=args.only_with_username,
        all_messages=args.all_messages,
    )
    return AppConfig(credentials=credentials, options=options)
