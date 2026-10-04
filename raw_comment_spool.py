from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from typing import Iterator

from models import CommentAuthorRecord
from utils import format_datetime

RAW_COMMENT_COLUMNS = [
    "user_id",
    "username",
    "profile_url",
    "first_name",
    "last_name",
    "is_bot",
    "source_channel",
    "source_post_id",
    "comment_date",
]


class RawCommentSpool:
    def __init__(self, directory: Path, stem: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(
            prefix=f".{stem}_raw_comments_",
            suffix=".csv",
            dir=directory,
        )
        os.close(fd)

        self.path = Path(path)
        self.count = 0
        self._file = self.path.open("w", encoding="utf-8-sig", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=RAW_COMMENT_COLUMNS)
        self._writer.writeheader()

    def append(self, record: CommentAuthorRecord) -> None:
        self._writer.writerow(record_to_row(record))
        self.count += 1

    def close(self) -> None:
        if not self._file.closed:
            self._file.flush()
            self._file.close()

    def cleanup(self) -> None:
        self.close()
        self.path.unlink(missing_ok=True)


def record_to_row(record: CommentAuthorRecord) -> dict[str, object]:
    return {
        "user_id": record.user_id,
        "username": record.username or "",
        "profile_url": record.profile_url,
        "first_name": record.first_name or "",
        "last_name": record.last_name or "",
        "is_bot": record.is_bot,
        "source_channel": record.source_channel,
        "source_post_id": record.source_post_id,
        "comment_date": format_datetime(record.comment_date),
    }


def iter_raw_comment_rows(path: Path | None) -> Iterator[dict[str, str]]:
    if path is None:
        return

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        yield from csv.DictReader(file)
