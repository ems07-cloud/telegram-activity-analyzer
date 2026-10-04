from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

ScanMode = Literal["channel_discussion_comments", "group_messages"]


@dataclass(slots=True)
class ApiCredentials:
    api_id: int
    api_hash: str
    session_name: str
    phone_number: str | None = None
    two_factor_password: str | None = None


@dataclass(slots=True)
class CliOptions:
    input_path: Path
    limit_posts: int
    limit_comments_per_post: int
    output_path: Path
    output_format: Literal["csv", "xlsx"]
    since: date | None
    target_users: int
    max_flood_wait: int
    dry_run: bool
    only_with_username: bool
    all_messages: bool


@dataclass(slots=True)
class AppConfig:
    credentials: ApiCredentials
    options: CliOptions


@dataclass(slots=True)
class ResolvedChannelTarget:
    input_reference: str
    source_channel_entity: Any
    discussion_group_entity: Any | None
    source_channel_label: str
    discussion_group_label: str | None
    scan_mode: ScanMode


@dataclass(slots=True)
class CommentAuthorRecord:
    user_id: int
    username: str | None
    profile_url: str
    first_name: str | None
    last_name: str | None
    is_bot: bool
    source_channel: str
    source_post_id: int
    comment_date: datetime


@dataclass(slots=True)
class UniqueUserRecord:
    user_id: int
    username: str | None
    profile_url: str
    first_name: str | None
    last_name: str | None
    is_bot: bool
    first_seen_comment_date: datetime
    last_seen_comment_date: datetime
    occurrences_count: int = 0
    source_channels_count: int = 0


@dataclass(slots=True)
class ChannelRunStats:
    input_reference: str
    source_channel: str | None
    discussion_group: str | None
    has_discussion_group: bool
    posts_scanned: int = 0
    comments_processed: int = 0
    senders_skipped: int = 0
    unique_authors: int = 0
    skipped_reason: str | None = None


@dataclass(slots=True)
class AnalysisReport:
    channels_total: int
    target_unique_users: int = 0
    target_reached: bool = False
    only_with_username: bool = True
    channels_processed: int = 0
    channels_without_discussion_group: int = 0
    channels_skipped: int = 0
    posts_scanned: int = 0
    comments_processed: int = 0
    senders_skipped: int = 0
    unique_authors_found: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    channel_stats: list[ChannelRunStats] = field(default_factory=list)


@dataclass(slots=True)
class AnalysisResult:
    unique_users: list[UniqueUserRecord]
    raw_comments_path: Path | None
    raw_comments_count: int
    report: AnalysisReport
