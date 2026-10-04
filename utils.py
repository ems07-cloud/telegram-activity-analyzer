from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from loguru import logger

from models import AnalysisReport

LEGAL_WARNING_TEXT = (
    "ПРЕДУПРЕЖДЕНИЕ: Утилиту можно использовать только для законного и прозрачного "
    "анализа Telegram-каналов и discussion groups, к которым у вас есть законный "
    "доступ и право на обработку данных. Нельзя использовать ее для спама, массовых "
    "рассылок, обхода лимитов Telegram, скрытого сбора персональных данных, "
    "прокси-ротации, антибан-логики и любых несогласованных действий."
)


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
    )


def print_legal_warning() -> None:
    border = "=" * 88
    print(border)
    print(LEGAL_WARNING_TEXT)
    print(border)


def parse_since_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Дата должна быть в формате YYYY-MM-DD.") from exc


def ensure_output_path(path: Path, export_format: str) -> Path:
    suffix = ".xlsx" if export_format == "xlsx" else ".csv"
    if path.suffix.lower() == suffix:
        return path
    return path.with_suffix(suffix)


def read_channel_references(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Входной файл не найден: {path}")

    seen: set[str] = set()
    references: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if candidate not in seen:
            seen.add(candidate)
            references.append(candidate)

    return references


def build_profile_url(username: str | None) -> str:
    if not username:
        return ""
    return f"https://t.me/{username}"


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat(sep=" ", timespec="seconds")


def report_to_text(report: AnalysisReport) -> str:
    lines = [
        "Отчет по анализу",
        f"Начало: {format_datetime(report.started_at)}",
        f"Завершение: {format_datetime(report.finished_at)}",
        f"Цель по уникальным авторам: {report.target_unique_users or 'не задана'}",
        f"Цель достигнута: {'да' if report.target_reached else 'нет'}",
        f"Только с username: {'да' if report.only_with_username else 'нет'}",
        f"Всего источников: {report.channels_total}",
        f"Обработано источников: {report.channels_processed}",
        f"Пропущено источников: {report.channels_skipped}",
        f"Каналов без discussion group: {report.channels_without_discussion_group}",
        f"Просмотрено публикаций/сообщений: {report.posts_scanned}",
        f"Обработано комментариев/сообщений: {report.comments_processed}",
        f"Пропущено сообщений без доступного автора: {report.senders_skipped}",
        f"Найдено уникальных авторов: {report.unique_authors_found}",
    ]
    return "\n".join(lines)


def report_path_for(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}_report.txt")


def raw_comments_csv_path_for(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}_raw_comments.csv")


class ProgressBar:
    def __init__(self, total: int | None, prefix: str, width: int = 28) -> None:
        self.total = None if total is None else max(total, 0)
        self.prefix = prefix
        self.width = width
        self.current = 0
        self._render()

    def advance(self, step: int = 1) -> None:
        if self.total == 0:
            return
        if self.total is None:
            self.current += step
            if self.current == 1 or self.current % 100 == 0:
                self._render()
            return
        self.current = min(self.total, self.current + step)
        self._render()

    def finish(self) -> None:
        if self.total == 0:
            return
        self._render()
        sys.stdout.write("\n")
        sys.stdout.flush()

    def _render(self) -> None:
        if self.total == 0:
            return
        if self.total is None:
            sys.stdout.write(f"\r{self.prefix}: обработано {self.current}")
            sys.stdout.flush()
            return

        ratio = self.current / self.total
        filled = int(self.width * ratio)
        bar = "#" * filled + "-" * (self.width - filled)
        sys.stdout.write(
            f"\r{self.prefix}: [{bar}] {self.current}/{self.total} ({ratio:.0%})"
        )
        sys.stdout.flush()
