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
    async def add_promotion_to_cart(page: Page, urls: List[str]) -> bool:
        logger.info(f"Attempting to add promotions to cart. URLs: {urls}")
        has_pending_free_promotion = False
        added_games = [] # Список игр, успешно добавленных в корзину

        # --> Add promotions to Cart
        for url in urls:
            logger.info(f"Navigating to promotion URL: {url}")
            await page.goto(url, wait_until="load", timeout=29000)
            logger.info(f"Arrived at: {page.url}")

            # <-- Handle pre-page
            # with suppress(TimeoutError):
            #     await page.click("//button//span[text()='Continue']", timeout=3000)

            # 检查游戏是否已在库
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

            # 检查是否为免费游戏
            purchase_btn = page.locator("//aside//button[@data-testid='purchase-cta-button']")
            purchase_status = await purchase_btn.text_content()
            if "Buy Now" in purchase_status or "Get" not in purchase_status:
                logger.debug(f"❌ Not available for purchase - {url=}")
                continue

            # 将免费游戏添加至购物车
            add_to_cart_btn = page.locator("//aside//button[@data-testid='add-to-cart-cta-button']")
            try:
                text = await add_to_cart_btn.text_content()
                if text == "View In Cart":
                    logger.debug(f"🙌 Already in the shopping cart - {url=}")
                    has_pending_free_promotion = True
                    # TODO: Получить название игры и добавить в added_games
                elif text == "Add To Cart":
                    logger.info(f"Clicking Add To Cart for {url}")
                    await add_to_cart_btn.click()
                    logger.debug(f"🙌 Add to the shopping cart - {url=}")
                    await expect(add_to_cart_btn).to_have_text("View In Cart")
                    has_pending_free_promotion = True
                    # TODO: Получить название игры и добавить в added_games

            except Exception as err:
                logger.warning(f"Failed to add promotion to cart - {err}")
                continue

        logger.info(f"Finished adding promotions to cart. has_pending_free_promotion: {has_pending_free_promotion}")
        # Возвращаем статус наличия бесплатных игр для оформления и список добавленных игр
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
            # Check all items in the shopping cart
            cards = await page.query_selector_all("//div[@data-testid='offer-card-layout-wrapper']")
            logger.info(f"Found {len(cards)} items in cart.")

            # Move paid games to the wishlist
            for card in cards:
                is_free = await card.query_selector("//span[text()='Free']")
                if not is_free:
                    has_paid_free = True
                    wishlist_btn = await card.query_selector(
                        "//button//span[text()='Move to wishlist']"
                    )
                    logger.info("Moving paid game to wishlist.")
                    await wishlist_btn.click()

            # Wait up to 60 seconds for the page to re-render.
            # Usually it takes 1~3s for the web page to be re-rendered
            # - Set threshold for overflow in case of poor Epic network
            # - It can also prevent extreme situations, such as: the user's shopping cart has nearly a hundred products
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

        # Очищаем все куки и кэш браузера
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

        # {{< SIGN IN PAGE >}}
        logger.info("Typing email and password.")
        await page.type("#email", self.settings.EPIC_EMAIL, delay=30, timeout=29000)
        await asyncio.sleep(1)
        await page.type("#password", self.settings.EPIC_PASSWORD.get_secret_value(), delay=30, timeout=29000)
        await asyncio.sleep(1)

        try:
            # Active hCaptcha checkbox
            logger.info("Clicking sign-in button.")
            await page.click("#sign-in", timeout=29000)
            await asyncio.sleep(5)
            # Active hCaptcha challenge
            logger.info("Waiting for hCaptcha challenge.")
            await agent.wait_for_challenge()
            logger.info("hCaptcha challenge likely handled.")
            # Wait for the page to redirect
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
            # Go to account page
            await page.goto("https://www.epicgames.com/account/personal", wait_until="networkidle")
            
            # Check if we're logged in
            if "true" != await page.locator("//egs-navigation").get_attribute("isloggedin"):
                logger.debug("Already logged out.")
                return True

            # Click on the account menu
            logger.info("Clicking account menu button.")
            await page.click("//button[@id='account-menu-button']")
            
            # Click logout button
            logger.info("Clicking logout button.")
            await page.click("//a[contains(@href, '/logout')]")
            
            # Wait for logout to complete
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
            # Check if we're already logged in with the correct account
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
            # If we're logged in with a different account, we need to logout first
            await self._logout(page)
        
        logger.info("Proceeding with full authorization process.")
        await self._authorize(page)

    async def _purchase_free_game(self):
        # == Cart Page == #
        await self.page.goto(URL_CART, wait_until="domcontentloaded", timeout=29000)

        logger.debug("Move ALL paid games from the shopping cart out")
        await self._empty_cart(self.page)

        # {{< Insert hCaptcha Challenger >}}
        agent = AgentV(page=self.page, agent_config=self.solver_config)

        # --> Check out cart
        await self.page.click("//button//span[text()='Check Out']")

        # <-- Handle Any LICENSE
        await self._agree_license(self.page)

        try:
            # --> Move to webPurchaseContainer iframe
            logger.debug("Move to webPurchaseContainer iframe")
            wpc, payment_btn = await self._active_purchase_container(self.page)
            logger.debug("Click payment button")
            # <-- Handle UK confirm-order
            await self._uk_confirm_order(wpc)

            # {{< Active >}}
            await agent.wait_for_challenge()
        except Exception as err:
            logger.warning(f"Failed to solve captcha - {err}")
            await self.page.reload()
            return await self._purchase_free_game()

    @retry(retry=retry_if_exception_type(TimeoutError), stop=stop_after_attempt(2), reraise=True)
    async def collect_weekly_games(self, promotions: List[PromotionGame]):
        # --> Make sure promotion is not in the library before executing
        urls = [p.url for p in promotions]
        has_pending_free_promotion, added_games = await self.add_promotion_to_cart(self.page, urls)
        if not has_pending_free_promotion:
            logger.success("✅ All week-free games are already in the library")
            return [] # Возвращаем пустой список, так как ничего не собирали

        await self._purchase_free_game()

        try:
            await self.page.wait_for_url(URL_CART_SUCCESS)
            logger.success("🎉 Successfully collected all weekly games")
            return added_games
        except TimeoutError:
            logger.warning("Failed to collect all weekly games")
            return [] # Возвращаем пустой список в случае ошибки оформления

    async def collect_for_all_accounts(self):
        """Собрать игры для всех аккаунтов последовательно."""
        accounts = [(self.settings.EPIC_EMAIL, self.settings.EPIC_PASSWORD)]
        if not accounts:
            logger.error("❌ Нет аккаунтов для обработки")
            # Отправить уведомление об отсутствии аккаунтов
            await self._send_telegram_notification("❌ Нет аккаунтов для обработки")
            return

        for i, (email, password) in enumerate(accounts):
            logger.info(f"🔄 Обработка аккаунта: {email}")
            account_status_message = f"🔄 Обработка аккаунта: {email}"
            collected_games_list = [] # Список собранных игр для этого аккаунта
            
            try:
                self.settings.EPIC_EMAIL = email
                self.settings.EPIC_PASSWORD = SecretStr(password)
                
                # Очищаем кэш браузера перед каждым аккаунтом
                await self.page.context.clear_cookies()
                logger.info("Кэш браузера очищен перед обработкой нового аккаунта.")
                
                # Собираем игры для текущего аккаунта
                collected_games_list = await self.collect_weekly_games(self._promotions)
                
                account_status_message = f"✅ Завершена обработка аккаунта: {email}"
                if collected_games_list:
                    game_titles = [game.title for game in collected_games_list if hasattr(game, 'title')]
                    account_status_message += f"\nСобраны игры: {', '.join(game_titles)}"
                else:
                    account_status_message += "\nНовых бесплатных игр не найдено или не удалось собрать."

            except Exception as e:
                account_status_message = f"❌ Ошибка при обработке аккаунта {email}: {str(e)}"
                logger.error(account_status_message)
            
            # Отправить уведомление после обработки каждого аккаунта
            await self._send_telegram_notification(account_status_message)
            
        logger.complete() # Сброс буфера логирования

    async def _send_telegram_notification(self, message: str):
        """Отправляет сообщение в Telegram."""
        telegram_token = os.environ.get('TELEGRAM_BOT_TOKEN')
        telegram_chat_id = os.environ.get('TELEGRAM_CHAT_ID')
        
        if not telegram_token or not telegram_chat_id:
            logger.warning("❌ Не настроены секреты TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID для отправки уведомлений.")
            return

        url = f'https://api.telegram.org/bot{telegram_token}/sendMessage'
        data = {
            'chat_id': telegram_chat_id,
            'text': message,
            'parse_mode': 'HTML' # Используем HTML для форматирования
        }
        
        try:
            response = requests.post(url, data=data)
            response.raise_for_status() # Проверить на ошибки HTTP
            logger.info("✅ Уведомление в Telegram успешно отправлено.")
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Ошибка при отправке уведомления в Telegram: {e}")
