"""消息投递策略。

QQ 官方机器人对**主动消息**（不带 msg_id 的推送）有条数与频率限制，
超出配额时平台会直接拒绝。因此后台任务产出的结果需要一个可靠的兜底通道：

* ``push_first``（默认）：先尝试主动推送；被拒绝时把内容写入玩家的
  ``pending_notices`` 离线队列，玩家下次发言时随回复一并送达；
* ``always_queue``：完全不主动推送，全部走离线队列（最省配额）；
* ``push_only``：只主动推送，失败即丢弃。

本模块刻意不依赖 AstrBot，便于单独测试与复用。
"""

from __future__ import annotations

from typing import Any, Protocol

#: 可用的投递模式
NOTIFY_MODES = ("push_first", "always_queue", "push_only")

#: 默认投递模式
DEFAULT_NOTIFY_MODE = "push_first"


class _Sender(Protocol):
    """只要求一个 ``push`` 方法，便于测试替身。"""

    async def push(self, umo: str, card: Any) -> bool: ...


class _Receiver(Protocol):
    """接收方只需要提供 umo 与离线队列。"""

    umo: str

    def push_notice(self, text: str) -> None: ...

    def notice_count(self) -> int: ...


def normalize_mode(mode: str | None) -> str:
    """把配置值规范化为合法模式。"""
    return mode if mode in NOTIFY_MODES else DEFAULT_NOTIFY_MODE


async def deliver_one(
    sender: _Sender,
    player: _Receiver,
    card: Any,
    *,
    mode: str = DEFAULT_NOTIFY_MODE,
    logger: Any = None,
) -> bool:
    """投递一条消息，返回是否进入了离线队列。

    Args:
        sender: 具备 ``push(umo, card)`` 的发送器。
        player: 玩家对象（需要 ``umo`` 与 ``push_notice``）。
        card: 待发送的卡片。
        mode: 投递模式。
        logger: 可选日志对象。

    Returns:
        内容被写入离线队列时返回 True。
    """
    mode = normalize_mode(mode)
    text = ""
    try:
        text = card.markdown or card.plain or ""
    except AttributeError:
        text = str(card)

    if mode == "always_queue" or not getattr(player, "umo", ""):
        player.push_notice(text)
        return True

    ok = False
    try:
        ok = bool(await sender.push(player.umo, card))
    except Exception as exc:  # 平台拒绝、网络异常等都视为未送达
        if logger is not None:
            logger.warning(f"[staridol] 主动推送失败（{player.umo}）：{exc}")

    if ok:
        return False

    if mode == "push_only":
        if logger is not None:
            logger.info("[staridol] 主动推送失败且配置为 push_only，本条消息已丢弃。")
        return False

    player.push_notice(text)
    if logger is not None:
        try:
            count = player.notice_count()
        except Exception:
            count = 0
        logger.info(f"[staridol] 主动推送未送达，已转入离线队列（待补发 {count} 条）。")
    return True


async def deliver_many(
    sender: _Sender,
    pending: list[tuple[Any, Any]],
    *,
    mode: str = DEFAULT_NOTIFY_MODE,
    logger: Any = None,
) -> bool:
    """批量投递 ``(player, card)``，返回是否有内容进入离线队列。"""
    queued = False
    for player, card in pending:
        if await deliver_one(sender, player, card, mode=mode, logger=logger):
            queued = True
    return queued
