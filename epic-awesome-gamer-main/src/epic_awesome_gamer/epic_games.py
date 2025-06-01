# -*- coding: utf-8 -*-
# Time       : 2022/1/16 0:25
# Author     : QIN2DIM
# GitHub     : https://github.com/QIN2DIM
# Description:
import os
from contextlib import suppress
from pathlib import Path
from typing import List
import asyncio
import requests

from hcaptcha_challenger.agent import AgentConfig, AgentV
from loguru import logger
from playwright.async_api import expect, TimeoutError, Page, FrameLocator
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from tenacity import *

from epic_awesome_gamer.game_types import PromotionGame

# fmt:off
URL_CLAIM = "https://store.epicgames.com/en-US/free-games"
URL_LOGIN = f"https://www.epicgames.com/id/login?lang=en-US&noHostRedirect=true&redirectUrl={URL_CLAIM}"
URL_CART = "https://store.epicgames.com/en-US/cart"
URL_CART_SUCCESS = "https://store.epicgames.com/en-US/cart/success"
# fmt:on


class EpicSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    cache_dir: Path = Path("tmp/.cache")

    EPIC_EMAIL: str = Field(
        description="Epic 游戏账号，需要关闭多步验证",
    )
    EPIC_PASSWORD: SecretStr = Field(
        description=" Epic 游戏密码，需要关闭多步验证",
    )
    # APPRISE_SERVERS: str | None = Field(
    #     default="", description="System notification by Apprise\nhttps://github.com/caronc/apprise"
    # )


class EpicGames:

    def __init__(self, page: Page, epic_settings: EpicSettings, solver_config: AgentConfig):
        self.page = page
        self.settings = epic_settings
        self.solver_config = solver_config

        self._promotions: List[PromotionGame] = []
        logger.info("EpicGames initialized.")

    @staticmethod
    async def _agree_license(page: Page):
        logger.info("Attempting to agree to license.")
        with suppress(TimeoutError):
            await page.click("//label[@for='agree']", timeout=29000)
            accept = page.locator("//button//span[text()='Accept']")
            if await accept.is_enabled():
                await accept.click()
                logger.info("License accepted.")
            else:
                logger.info("License acceptance not required or button not enabled.")

    @staticmethod
    async def _active_purchase_container(page: Page):
        logger.info("Activating purchase container.")
        wpc = page.frame_locator("//iframe[@class='']")
        payment_btn = wpc.locator("//div[@class='payment-order-confirm']")
        with suppress(Exception):
            await expect(payment_btn).to_be_attached(timeout=29000)
        await page.wait_for_timeout(2000)
        await payment_btn.click(timeout=29000)
        logger.info("Purchase container activated.")

        return wpc, payment_btn

    @staticmethod
    async def _uk_confirm_order(wpc: FrameLocator):
        logger.info("Attempting to confirm UK order.")
        # <-- Handle UK confirm-order
        with suppress(TimeoutError):
            accept = wpc.locator(
                "//button[contains(@class, 'payment-confirm__btn payment-btn--primary')]"
            )
            if await accept.is_enabled(timeout=5000):
                await accept.click()
                logger.info("UK order confirmed.")
                return True
            else:
                logger.info("UK order confirmation not required or button not enabled.")

    @staticmethod
    async def add_promotion_to_cart(page: Page, urls: List[str]) -> tuple[bool, List[PromotionGame]]:
        logger.info(f"Attempting to add promotions to cart. URLs: {urls}")
        has_pending_free_promotion = False
        added_games = []

        for url in urls:
            logger.info(f"Navigating to promotion URL: {url}")
            await page.goto(url, wait_until="load", timeout=29000)
            logger.info(f"Arrived at: {page.url}")

            btn_list = page.locator("//aside//button")
            aside_btn_count = await btn_list.count()
            texts = ""
            for i in range(aside_btn_count):
                btn = btn_list.nth(i)
                btn_text_content = await btn.text_content()
                texts += btn_text_content

            if "In Library" in texts:
                logger.success(f"✅ Already in the library - {url=}")
                continue

            purchase_btn = page.locator("//aside//button[@data-testid='purchase-cta-button']")
            purchase_status = await purchase_btn.text_content()
            if "Buy Now" in purchase_status or "Get" not in purchase_status:
                logger.debug(f"❌ Not available for purchase - {url=}")
                continue

            add_to_cart_btn = page.locator("//aside//button[@data-testid='add-to-cart-cta-button']")
            try:
                text = await add_to_cart_btn.text_content()
                if text == "View In Cart":
                    logger.debug(f"🙌 Already in the shopping cart - {url=}")
                    has_pending_free_promotion = True
                elif text == "Add To Cart":
                    logger.info(f"Clicking Add To Cart for {url}")
                    await add_to_cart_btn.click()
                    logger.debug(f"🙌 Add to the shopping cart - {url=}")
                    await expect(add_to_cart_btn).to_have_text("View In Cart")
                    has_pending_free_promotion = True

            except Exception as err:
                logger.warning(f"Failed to add promotion to cart - {err}")
                continue

        logger.info(f"Finished adding promotions to cart. has_pending_free_promotion: {has_pending_free_promotion}")
        return has_pending_free_promotion, added_games

    async def _empty_cart(self, page: Page, wait_rerender: int = 30) -> bool | None:
        """
        URL_CART = "https://store.epicgames.com/en-US/cart"
        URL_WISHLIST = "https://store.epicgames.com/en-US/wishlist"
        //span[text()='Your Cart is empty.']

        Args:
            wait_rerender:
            page:

        Returns:

        """
        logger.info("Attempting to empty cart of paid games.")
        has_paid_free = False

        try:
            cards = await page.query_selector_all("//div[@data-testid='offer-card-layout-wrapper']")
            logger.info(f"Found {len(cards)} items in cart.")

            for card in cards:
                is_free = await card.query_selector("//span[text()='Free']")
                if not is_free:
                    has_paid_free = True
                    wishlist_btn = await card.query_selector(
                        "//button//span[text()='Move to wishlist']"
                    )
                    logger.info("Moving paid game to wishlist.")
                    await wishlist_btn.click()

            if has_paid_free and wait_rerender:
                logger.info(f"Paid games moved, waiting for re-render. Remaining attempts: {wait_rerender}")
                wait_rerender -= 1
                await page.wait_for_timeout(2000)
                return await self._empty_cart(page, wait_rerender)
            logger.info("Cart is empty of paid games.")
            return True
        except TimeoutError as err:
            logger.warning("Failed to empty shopping cart", err=err)
            return False

    async def _authorize(self, page: Page, retry_times: int = 3):
        logger.info(f"Attempting to authorize. Retry attempts left: {retry_times}")
        if not retry_times:
            logger.error("Authorization failed after multiple retries.")
            return

        logger.info("Clearing browser cookies and cache.")
        await page.context.clear_cookies()
        await page.goto("about:blank")
        await page.context.clear_permissions()

        point_url = "https://www.epicgames.com/account/personal?lang=en-US&productName=egs&sessionInvalidated=true"
        logger.info(f"Navigating to authorization URL: {point_url}")
        await page.goto(point_url, wait_until="networkidle", timeout=29000)
        logger.debug(f"Login with Email - {page.url}")
        await asyncio.sleep(3)

        agent = AgentV(page=page, agent_config=self.solver_config)

        logger.info("Typing email and password.")
        await page.type("#email", self.settings.EPIC_EMAIL, delay=30, timeout=29000)
        await asyncio.sleep(1)
        await page.type("#password", self.settings.EPIC_PASSWORD.get_secret_value(), delay=30, timeout=29000)
        await asyncio.sleep(1)

        try:
            logger.info("Clicking sign-in button.")
            await page.click("#sign-in", timeout=29000)
            await asyncio.sleep(5)
            logger.info("Waiting for hCaptcha challenge.")
            await agent.wait_for_challenge()
            logger.info("hCaptcha challenge likely handled.")
        except Exception as err:
            logger.warning(f"Failed to solve captcha or sign in - {err}")
            await page.reload()
            logger.info("Reloading page and retrying authorization.")
            return await self._authorize(page, retry_times=retry_times - 1)

        logger.info(f"Waiting for redirect to: {point_url}")
        await page.wait_for_url(point_url)
        logger.success("Authorization successful.")
        return True

    async def _logout(self, page: Page):
        """Logout from the current Epic Games account."""
        logger.info("Attempting to logout.")
        try:
            await page.goto("https://www.epicgames.com/account/personal", wait_until="networkidle")
            
            if "true" != await page.locator("//egs-navigation").get_attribute("isloggedin"):
                logger.debug("Already logged out.")
                return True

            logger.info("Clicking account menu button.")
            await page.click("//button[@id='account-menu-button']")
            
            logger.info("Clicking logout button.")
            await page.click("//a[contains(@href, '/logout')]")
            
            await page.wait_for_load_state("networkidle")
            logger.debug("Successfully logged out.")
            return True
        except Exception as err:
            logger.warning(f"Failed to logout - {err}")
            return False

    async def authorize(self, page: Page):
        logger.info(f"Checking authorization status on {URL_CLAIM}")
        await page.goto(URL_CLAIM, wait_until="domcontentloaded", timeout=29000)
        if "true" == await page.locator("//egs-navigation").get_attribute("isloggedin"):
            logger.info("Page reports user is logged in. Checking account email.")
            try:
                account_email = await page.locator("//button[@id='account-menu-button']").get_attribute("aria-label")
                if self.settings.EPIC_EMAIL.lower() in account_email.lower():
                    logger.success("Already logged in with the correct account.")
                    return True
                else:
                    logger.warning("Logged in with incorrect account, attempting logout.")
            except:
                logger.warning("Could not retrieve account email, assuming incorrect login and attempting logout.")
                pass
            await self._logout(page)
        
        logger.info("Proceeding with full authorization process.")
        await self._authorize(page)

    async def _purchase_free_game(self):
        logger.info("Attempting to purchase free game.")
        await self.page.goto(URL_CART, wait_until="domcontentloaded", timeout=29000)

        logger.debug("Move ALL paid games from the shopping cart out")
        await self._empty_cart(self.page)

        agent = AgentV(page=self.page, agent_config=self.solver_config)

        logger.info("Clicking Check Out button.")
        await self.page.click("//button//span[text()='Check Out']")

        logger.info("Attempting to agree to license if necessary.")
        await self._agree_license(self.page)

        try:
            logger.debug("Move to webPurchaseContainer iframe")
            wpc, payment_btn = await self._active_purchase_container(self.page)
            logger.debug("Click payment button")
            await self._uk_confirm_order(wpc)

            logger.info("Waiting for hCaptcha challenge during purchase.")
            await agent.wait_for_challenge()
            logger.info("hCaptcha challenge likely handled during purchase.")
        except Exception as err:
            logger.warning(f"Failed to solve captcha during purchase - {err}")
            await self.page.reload()
            logger.info("Reloading page and retrying purchase.")
            return await self._purchase_free_game()
        logger.info("Purchase process initiated.")

    @retry(retry=retry_if_exception_type(TimeoutError), stop=stop_after_attempt(2), reraise=True)
    async def collect_weekly_games(self, promotions: List[PromotionGame]) -> List[PromotionGame]:
        logger.info("Starting weekly games collection process.")
        urls = [p.url for p in promotions]
        has_pending_free_promotion, added_games = await self.add_promotion_to_cart(self.page, urls)

        if not has_pending_free_promotion:
            logger.success("✅ All week-free games are already in the library")
            return []

        logger.info("Proceeding with purchase for pending free promotions.")
        await self._purchase_free_game()

        try:
            await self.page.wait_for_url(URL_CART_SUCCESS, timeout=30000)
            logger.success("🎉 Successfully collected all weekly games")
            return added_games
        except TimeoutError:
            logger.warning("Failed to collect all weekly games - Timeout waiting for success page")
            return []
        except Exception as e:
            logger.warning(f"An error occurred during collection: {e}")
            return []

    async def collect_for_all_accounts(self):
        """Собрать игры для всех аккаунтов последовательно."""
        # TODO: Сделать чтение аккаунтов из файла accounts.json
        # Пока используем аккаунты из настроек, предполагая, что они загружены туда
        accounts = [(self.settings.EPIC_EMAIL, self.settings.EPIC_PASSWORD.get_secret_value())]
        
        # Если аккаунты не были загружены в настройки, попробуем прочитать accounts.json
        if not self.settings.EPIC_EMAIL or not self.settings.EPIC_PASSWORD.get_secret_value():
            try:
                with open('accounts.json', 'r') as f:
                    accounts_data = json.load(f)
                    accounts = [(acc['email'], acc['password']) for acc in accounts_data]
                logger.info(f"Загружено {len(accounts)} аккаунтов из accounts.json")
            except FileNotFoundError:
                logger.error("❌ Файл accounts.json не найден и аккаунты не настроены через переменные окружения.")
                return
            except json.JSONDecodeError:
                logger.error("❌ Ошибка декодирования JSON в accounts.json. Убедитесь, что формат правильный.")
                return
            except Exception as e:
                logger.error(f"❌ Ошибка при чтении accounts.json: {e}")
                return
                

        if not accounts:
            logger.error("❌ Нет аккаунтов для обработки после попытки загрузки из accounts.json.")
            return

        logger.info(f"Найдено {len(accounts)} аккаунтов для обработки")

        # --- Начало логики обработки аккаунтов ---
        for i, (email, password) in enumerate(accounts):
            logger.info(f"🔄 Обработка аккаунта {i+1}/{len(accounts)}: {email}")
            
            try:
                # Обновляем настройки для текущего аккаунта
                self.settings.EPIC_EMAIL = email
                self.settings.EPIC_PASSWORD = SecretStr(password)
                
                # Очищаем кэш браузера перед каждым аккаунтом
                await self.page.context.clear_cookies()
                logger.info("Кэш браузера очищен перед обработкой нового аккаунта.")
                
                # Авторизация
                await self.authorize(self.page)
                logger.info("Авторизация выполнена.")
                
                # Сбор игр для текущего аккаунта
                collected_games = await self.collect_weekly_games(self._promotions)
                
                if collected_games:
                    game_titles = [game.title for game in collected_games if hasattr(game, 'title')]
                    logger.success(f"✅ Успешно собраны игры для аккаунта {email}: {', '.join(game_titles)}")
                else:
                    logger.info(f"✅ Новых бесплатных игр не найдено или не удалось собрать для аккаунта {email}.")

            except Exception as e:
                logger.error(f"❌ Ошибка при обработке аккаунта {email}: {e}")
            
        # --- Конец логики обработки аккаунтов ---    

        logger.complete()


async def run_collector():
    # Эта функция теперь не отправляет уведомления в Telegram, только выполняет сбор
    from browserforge.fingerprints import Screen
    from camoufox.async_api import AsyncCamoufox
    from playwright.async_api import Page
    from hcaptcha_challenger.agent import AgentConfig

    # Вместо получения из аргументов командной строки, получаем из переменных окружения
    epic_email = os.environ.get("EPIC_EMAIL")
    epic_password = os.environ.get("EPIC_PASSWORD")
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    
    # TODO: Возможно, стоит использовать accounts.json как основной способ получения аккаунтов
    # и переменные окружения для GEMINI_API_KEY

    user_data_dir = Path("tmp/.cache/user_data") # Используем фиксированный путь или получаем из env

    try:
        # Создаем отдельную директорию для данных браузера
        user_data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Создана директория для данных браузера: {user_data_dir}")

        async with AsyncCamoufox(
            persistent_context=True,
            user_data_dir=str(user_data_dir.resolve()),
            screen=Screen(max_width=1920, max_height=1080),
            humanize=0.5,
        ) as browser:
            logger.info("Браузер успешно запущен")
            page = browser.pages[-1] if browser.pages else await browser.new_page()
            
            epic_settings = EpicSettings()
            # Загружаем EPIC_EMAIL и EPIC_PASSWORD через SettingsConfigDict(env_file=".env")
            # или они уже должны быть установлены как переменные окружения в workflow

            solver_config = AgentConfig(
                DISABLE_BEZIER_TRAJECTORY=True,
                CHALLENGE_CLASSIFIER_MODEL='gemini-2.5-flash-preview-05-20',
                IMAGE_CLASSIFIER_MODEL='gemini-2.5-flash-preview-05-20',
                SPATIAL_POINT_REASONER_MODEL='gemini-2.5-flash-preview-05-20',
                SPATIAL_PATH_REASONER_MODEL='gemini-2.5-flash-preview-05-20',
            )
            if gemini_api_key:
                solver_config.GEMINI_API_KEY = SecretStr(gemini_api_key)
            
            agent = EpicGames(page, epic_settings, solver_config)
            await agent.collect_for_all_accounts()
            logger.info("Сбор игр успешно завершен для всех аккаунтов.")

    except Exception as e:
        logger.error(f"Критическая ошибка при выполнении run_collector: {e}")
        # Здесь можно добавить отправку уведомления об общей ошибке workflow
        raise # Перебрасываем исключение, чтобы workflow завершился с ошибкой


def main():
    # Эта функция теперь просто вызывает run_collector с обработкой исключений
    try:
        asyncio.run(run_collector())
    except KeyboardInterrupt:
        logger.info("Программа остановлена пользователем")
    except Exception as e:
        logger.error(f"Необработанная ошибка в main: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
