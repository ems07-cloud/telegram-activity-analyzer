from __future__ import annotations

import asyncio

from config import ConfigError, load_app_config
from exporters import ExportError
from loguru import logger
from models import AppConfig
from telegram_client import TelegramAuthError
from utils import LEGAL_WARNING_TEXT, print_legal_warning, read_channel_references, setup_logging


async def run(config: AppConfig) -> int:
    from channel_analyzer import ChannelAnalyzer
    from exporters import export_results
    from telegram_client import TelegramApiClient
    from utils import report_to_text

    setup_logging()

    print_legal_warning()
    logger.warning(LEGAL_WARNING_TEXT.replace("\n", " "))

    channel_references = read_channel_references(config.options.input_path)
    if not channel_references:
        raise ConfigError(
            f"Во входном файле нет ссылок на каналы или группы: {config.options.input_path}"
        )

    logger.info(
        "Загружено {} ссылок на каналы/группы из {}",
        len(channel_references),
        config.options.input_path,
    )

    telegram_client = TelegramApiClient(
        config.credentials,
        max_flood_wait=config.options.max_flood_wait,
    )

    try:
        await telegram_client.connect()
        analyzer = ChannelAnalyzer(telegram_client, config.options)
        result = await analyzer.analyze(channel_references)
    finally:
        await telegram_client.disconnect()

    print()
    print(report_to_text(result.report))

    if config.options.dry_run:
        logger.info("Включен режим dry-run. Экспорт пропущен.")
        return 0

    exported_files = export_results(
        result=result,
        output_path=config.options.output_path,
        export_format=config.options.output_format,
    )
    for exported_file in exported_files:
        logger.info("Файл сохранен: {}", exported_file)

    return 0


def main() -> int:
    try:
        config = load_app_config()
        return asyncio.run(run(config))
    except ConfigError as exc:
        setup_logging()
        logger.error(str(exc))
        return 2
    except FileNotFoundError as exc:
        setup_logging()
        logger.error(str(exc))
        return 2
    except ExportError as exc:
        setup_logging()
        logger.error(str(exc))
        return 2
    except TelegramAuthError as exc:
        setup_logging()
        logger.error(str(exc))
        return 2
    except KeyboardInterrupt:
        setup_logging()
        logger.warning("Выполнение прервано пользователем.")
        return 130
    except Exception as exc:  # pragma: no cover - defensive guard for CLI entrypoint
        setup_logging()
        logger.exception("Необработанная ошибка: {}", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
