"""娱乐圈模拟器 —— AstrBot 插件入口。

一款以艺人养成 + 集团经营为核心的双线文字游戏，支持 QQ 官方机器人的
Markdown 卡片、按钮交互、自定义菜单与指令面板，数据持久化在
``data/plugin_data/astrbot_plugin_staridol``。
"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .staridol.menus import BotMenuManager
from .staridol.qqcard import CardSender
from .staridol.render import plain_card
from .staridol.result import Result
from .staridol.router import Router
from .staridol.scheduler import GameScheduler
from .staridol.store import GameStore


@register(
    "astrbot_plugin_staridol",
    "staridol",
    "娱乐圈模拟器：艺人养成 + 集团经营文字游戏",
    "1.1.0",
)
class StarIdolPlugin(Star):
    """插件主类。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config: Any = config or {}
        self.store: GameStore | None = None
        self.sender: CardSender | None = None
        self.router: Router | None = None
        self.scheduler: GameScheduler | None = None
        self.menu_manager: BotMenuManager | None = None
        self._flush_task: asyncio.Task | None = None
        self._menu_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------
    def _cfg(self, key: str, default: Any) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:
            value = default
        return default if value is None else value

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def initialize(self) -> None:
        """载入存档并启动后台任务。"""
        use_card = bool(self._cfg("use_card", True))
        self.store = GameStore(
            init_money=int(self._cfg("init_money", 1000)),
            init_points=int(self._cfg("init_points", 150)),
            show_refresh=int(self._cfg("show_refresh_times", 3)),
            show_artist_count=int(self._cfg("show_artist_count", 5)),
            market_refresh=int(self._cfg("market_refresh_times", 3)),
            base_quest=int(self._cfg("daily_quest_base", 5)),
            max_artists=int(self._cfg("max_artists", 30)),
            logger=self.logger,
        )
        await self.store.load()

        self.sender = CardSender(self.context, use_card=use_card)
        self.menu_manager = BotMenuManager(
            self.store,
            self.context,
            enabled=bool(self._cfg("sync_bot_menu", True)),
            logger=self.logger,
        )
        self.router = Router(
            self.store,
            self.sender,
            config=self.config,
            logger_=self.logger,
            menu_manager=self.menu_manager,
        )
        self.scheduler = GameScheduler(
            self.store,
            self.sender,
            interval=float(self._cfg("tick_interval", 20)),
            notify=bool(self._cfg("notify_on_finish", True)),
            notify_mode=str(self._cfg("notify_mode", "always_queue")),
            logger=self.logger,
        )
        self.scheduler.start()
        self._flush_task = asyncio.create_task(
            self.store.flush_loop(15.0), name="staridol-flush"
        )
        # 等待平台适配器连接完成后同步一次机器人菜单与指令面板
        if self.menu_manager.enabled:
            self._menu_task = asyncio.create_task(
                self.menu_manager.sync_later(10.0), name="staridol-menu-sync"
            )
        self.logger.info("[staridol] 娱乐圈模拟器已启动。")

    async def terminate(self) -> None:
        """停止后台任务并落盘。"""
        for task in (self._menu_task, self._flush_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if self.scheduler:
            await self.scheduler.stop()
        if self.store:
            await self.store.save(force=True)
        self.logger.info("[staridol] 娱乐圈模拟器已停止。")

    # ------------------------------------------------------------------
    # 消息入口
    # ------------------------------------------------------------------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，尝试匹配游戏指令。"""
        if self.router is None or self.sender is None:
            return
        try:
            result: Result | None = await self.router.handle(event)
        except Exception as exc:
            logger.error(f"[staridol] 处理消息时出错：{exc}", exc_info=True)
            return
        if result is None:
            return  # 不是游戏指令，放行给其他插件 / LLM

        # 无论如何都终止事件传播，避免游戏指令被 LLM 再次回复
        event.stop_event()
        if result.silent:
            return
        try:
            await self._send(event, result)
        except Exception as exc:
            logger.error(f"[staridol] 发送回复失败：{exc}", exc_info=True)

    async def _send(self, event: AstrMessageEvent, result: Result) -> None:
        assert self.sender is not None
        if result.card is not None:
            await self.sender.send(event, result.card)
        elif result.text:
            await self.sender.send(event, plain_card(result.text))
