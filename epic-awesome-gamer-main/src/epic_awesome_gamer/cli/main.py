import os
import sys
import asyncio
from pathlib import Path
import logging
from dotenv import load_dotenv

load_dotenv("env.txt")

import typer
from typing import Optional

from pydantic import SecretStr
from hcaptcha_challenger.models import SCoTModelType

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Добавляем обработчик loguru для вывода в stderr и перехвата стандартного logging
from loguru import logger as loguru_logger # Импортируем loguru под другим именем
loguru_logger.add(sys.stderr, format="{time} {level} {message}", filter="epic_awesome_gamer", level="INFO")

# Create top-level application
app = typer.Typer(
    name="epic-awesome-gamer",
    help="Epic Games Collector tool",
    add_completion=False,
    invoke_without_command=True,  # Enable callback when no command is passed
)


@app.callback()
def main_callback(ctx: typer.Context):
    """
    Main callback. Shows help if no command is provided.
    """
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())
        raise typer.Exit()


@app.command(name="help", help="Show help for a specific command.")
def help_command(
    ctx: typer.Context,
    command_path: list[str] = typer.Argument(
        None, help="The command path (e.g., 'dataset collect')."
    ),
):
    """
    Provides help for commands, similar to `command --help`.

    Example: hc help dataset collect
    """
    if not command_path:
        # If `hc help` is called with no arguments, show main help
        print(ctx.parent.get_help())
        raise typer.Exit()

    # Get the full command context to search through
    current_ctx = ctx.parent

    # Navigate through the command path to find the target command
    for i, cmd in enumerate(command_path):
        found = False

        # Try to find command in current context
        if hasattr(current_ctx.command, "commands"):
            for name, command in current_ctx.command.commands.items():
                if name == cmd:
                    # Create a new context for this command
                    current_ctx = typer.Context(command, parent=current_ctx, info_name=cmd)
                    found = True
                    break

        if not found:
            # If we didn't find it as a command, it might be a typer app
            # Use --help mechanism directly
            try:
                remaining_path = command_path[i:]
                print(f"Showing help for: {' '.join(remaining_path)}")
                cmd_list = [*sys.argv[0:1], *remaining_path, "--help"]
                app(cmd_list)
                return
            except SystemExit:
                # Typer will exit after showing help
                return
            except Exception:
                print(f"Error: Command '{cmd}' not found")
                raise typer.Exit(code=1)

    # Print help for the found command
    print(current_ctx.get_help())
    raise typer.Exit()


@app.command(name="collect", help="Collect free epic games.")
def collect(
    epic_email: Optional[str] = typer.Option(
        None, "--email", envvar="EPIC_EMAIL", help="Epic Games account email (use comma to separate multiple emails)"
    ),
    epic_password: Optional[str] = typer.Option(
        None, "--password", envvar="EPIC_PASSWORD", help="Epic Games account password (use comma to separate multiple passwords)"
    ),
    gemini_api_key: Optional[str] = typer.Option(
        None, "--gemini-api-key", envvar="GEMINI_API_KEY", help="Gemini API key"
    ),
    user_data_dir: Optional[Path] = typer.Option(
        Path("tmp/.cache/user_data"), "--user-data-dir", help="Directory to store browser user data"
    ),
    all_games: bool = typer.Option(
        False, "--all-games", help="Collect all free games, but may miss weekly free games"
    ),
):
    """
    Collect free games from Epic Games Store.
    """
    from browserforge.fingerprints import Screen
    from camoufox.async_api import AsyncCamoufox
    from playwright.async_api import Page
    from hcaptcha_challenger.agent import AgentConfig

    from epic_awesome_gamer import EpicSettings
    from epic_awesome_gamer.collector import EpicAgent

    if all_games:
        logger.info("🙌 Not implemented yet.")
        return

    async def startup_epic_awesome_gamer(page: Page, email: str, password: str):
        try:
            logger.info(f"Инициализация настроек для аккаунта {email}")
            epic_settings = EpicSettings()
            solver_config = AgentConfig(
                DISABLE_BEZIER_TRAJECTORY=True,
                CHALLENGE_CLASSIFIER_MODEL='gemini-2.5-flash-preview-05-20',
                IMAGE_CLASSIFIER_MODEL='gemini-2.5-flash-preview-05-20',
                SPATIAL_POINT_REASONER_MODEL='gemini-2.5-flash-preview-05-20',
                SPATIAL_PATH_REASONER_MODEL='gemini-2.5-flash-preview-05-20',
            )

            epic_settings.EPIC_EMAIL = email
            epic_settings.EPIC_PASSWORD = SecretStr(password)
            if gemini_api_key:
                solver_config.GEMINI_API_KEY = SecretStr(gemini_api_key)

            logger.info("Создание агента для сбора игр")
            agent = EpicAgent(page, epic_settings, solver_config)
            logger.info("Начало сбора игр")
            await agent.collect_epic_games()
            logger.info("Сбор игр успешно завершен")
        except Exception as e:
            logger.error(f"Ошибка при обработке аккаунта {email}: {str(e)}")
            raise

    async def run_collector():
        try:
            # Получаем списки email и паролей
            emails = (epic_email or "").split(",")
            passwords = (epic_password or "").split(",")
            
            if not epic_email or not epic_password:
                logger.error("❌ Email и пароль не указаны. Используйте --email и --password или установите переменные окружения EPIC_EMAIL и EPIC_PASSWORD")
                return

            if len(emails) != len(passwords):
                logger.error("❌ Количество email и паролей должно совпадать")
                return

            logger.info(f"Найдено {len(emails)} аккаунтов для обработки")

            for i, (email, password) in enumerate(zip(emails, passwords)):
                email = email.strip()
                password = password.strip()
                if not email or not password:
                    continue

                logger.info(f"🔄 Обработка аккаунта {i+1}/{len(emails)}: {email}")
                
                # Создаем отдельную директорию для каждого аккаунта
                account_dir = user_data_dir / f"account_{i}"
                account_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Создана директория для данных браузера: {account_dir}")

                try:
                    async with AsyncCamoufox(
                        persistent_context=True,
                        user_data_dir=str(account_dir.resolve()),
                        screen=Screen(max_width=1920, max_height=1080),
                        humanize=0.5,
                    ) as browser:
                        logger.info("Браузер успешно запущен")
                        page = browser.pages[-1] if browser.pages else await browser.new_page()
                        await startup_epic_awesome_gamer(page, email, password)
                        logger.info(f"✅ Завершена обработка аккаунта: {email}")
                except Exception as e:
                    logger.error(f"❌ Ошибка при работе с браузером для аккаунта {email}: {str(e)}")
                    continue

        except Exception as e:
            logger.error(f"Критическая ошибка: {str(e)}")
            raise

    try:
        asyncio.run(run_collector())
    except KeyboardInterrupt:
        logger.info("Программа остановлена пользователем")
    except Exception as e:
        logger.error(f"Необработанная ошибка: {str(e)}")
        sys.exit(1)


def main():
    app()


if __name__ == "__main__":
    main()
