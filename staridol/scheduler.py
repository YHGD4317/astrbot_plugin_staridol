"""后台定时任务：训练/休息完成、项目体力与结算、每日刷新与消息投递。

消息投递策略（``notify_mode``）：

* ``always_queue``（默认）：完全不主动推送，结果统一写入玩家的离线通知队列，
  等玩家发送任意游戏指令时随回复一并提醒未读变动（QQ 官方机器人通常没有主动消息权限）；
* ``push_first``：先尝试主动推送；被平台拒绝时转入离线队列；
* ``push_only``：只主动推送，失败即丢弃并记录日志。

所有面向玩家的文案均不使用 emoji。
"""

from __future__ import annotations

import asyncio
from typing import Any

from . import constants as C
from . import notify as notify_sys
from . import utils as U
from .models import Player
from .notify import NOTIFY_MODES, normalize_mode
from .qqcard import CardSender
from .render import Card
from .store import GameStore
from .systems import artist as artist_sys
from .systems import casino_biz as casino_biz_sys
from .systems import daily as daily_sys
from .systems import project as project_sys

__all__ = ["GameScheduler", "NOTIFY_MODES"]


class GameScheduler:
    """周期性扫描存档，处理到期事件并投递结果。"""

    def __init__(
        self,
        store: GameStore,
        sender: CardSender,
        *,
        interval: float = 20.0,
        notify: bool = True,
        notify_mode: str = notify_sys.DEFAULT_NOTIFY_MODE,
        logger: Any = None,
    ) -> None:
        self.store = store
        self.sender = sender
        self.interval = max(5.0, float(interval))
        self.notify = bool(notify)
        self.notify_mode = normalize_mode(notify_mode)
        self.logger = logger
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="staridol-scheduler")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 定时任务不允许因单次异常而中断
                if self.logger:
                    self.logger.error(f"[staridol] 定时任务执行出错：{exc}", exc_info=True)
            await asyncio.sleep(self.interval)

    # ------------------------------------------------------------------
    # 单次扫描
    # ------------------------------------------------------------------
    async def tick(self) -> None:
        ts = U.now()
        pending: list[tuple[Player, Card]] = []

        async with self.store.lock:
            for player in self.store.all_players():
                try:
                    await self._tick_player(player, ts, pending)
                except Exception as exc:
                    if self.logger:
                        self.logger.error(
                            f"[staridol] 处理玩家 {player.uid} 的定时任务失败：{exc}",
                            exc_info=True,
                        )
            await self.store.save()

        if not self.notify or not pending:
            return
        queued = await notify_sys.deliver_many(
            self.sender, pending, mode=self.notify_mode, logger=self.logger
        )
        if queued:
            async with self.store.lock:
                self.store.mark_dirty()
                await self.store.save()

    async def _tick_player(self, player: Player, ts: float, pending: list) -> None:
        daily_sys.ensure_daily(self.store, player, ts)

        # ---- 艺人状态 ----
        for artist in list(player.artists):
            artist_sys.regen_stamina(artist, ts)
            if artist.status == C.STATUS_PROJECT and not artist.busy_until:
                continue
            if artist.status == C.STATUS_PROJECT:
                # 项目进行中：按小时消耗体力
                spent = artist_sys.tick_project_stamina(artist, ts)
                if spent:
                    self.store.mark_dirty()
                    if artist.stamina <= 0:
                        pending.append(
                            (
                                player,
                                Card(
                                    markdown=(
                                        f"# 体力透支\n"
                                        f"**{artist.name}** {artist.status_text}期间已耗尽体力"
                                        f"（0/100）。\n\n"
                                        "拍摄仍会继续，但结束后记得安排休息。"
                                    )
                                ),
                            )
                        )
            if not artist.busy_until or artist.busy_until > ts:
                continue
            if artist.status == C.STATUS_TRAINING:
                text = artist_sys.finish_training(player, artist, ts)
                player.push_log(f"{artist.name} 训练完成")
                self.store.mark_dirty()
                pending.append((player, Card(markdown=text)))
            elif artist.status == C.STATUS_REST:
                text = artist_sys.finish_rest(player, artist, ts)
                self.store.mark_dirty()
                pending.append((player, Card(markdown=text)))
            elif artist.status == C.STATUS_PROJECT:
                project = next((p for p in player.projects if p.pid == artist.project_id), None)
                if project is None or project.settled:
                    # 项目已结算但艺人状态没复位时兜底恢复
                    artist.status = C.STATUS_IDLE
                    artist.status_text = ""
                    artist.project_id = ""
                    artist.busy_until = 0.0
                    self.store.mark_dirty()

        # ---- 项目结算 ----
        for project in list(player.projects):
            if project.status != C.PROJECT_RUNNING or project.settled:
                continue
            if project.end_at > ts:
                continue
            text = project_sys.settle_project(self.store, player, project, ts)
            if text:
                pending.append((player, Card(markdown=text)))

        # ---- 经营赌场：让 NPC 进入游玩（静默推进，不主动打扰玩家）----
        if player.casino_biz is not None:
            try:
                casino_biz_sys.npc_tick(self.store, player, ts)
            except Exception:
                pass
        self.store.mark_dirty()
