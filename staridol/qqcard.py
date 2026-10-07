"""QQ 官方机器人富文本卡片发送封装。

AstrBot 的 ``MessageChain`` 只描述文本 / 图片等通用组件，没有按钮概念，
而 QQ 官方机器人平台需要在发送消息时额外携带 ``keyboard`` 字段才能渲染按钮。

本模块在**不修改 AstrBot 本体**的前提下，直接复用 QQ 官方适配器持有的
``botpy`` 客户端发送 "Markdown + 按钮" 消息；一旦失败或不满足条件，
会自动回退到 AstrBot 标准消息链，保证任何平台都能正常游玩。
"""

from __future__ import annotations

import random
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain

from .render import Card

#: 支持 Markdown 的平台适配器名
MD_PLATFORM_NAMES = {"qq_official", "qq_official_webhook", "qqofficial"}

#: 支持按钮的平台适配器名（仅 QQ 官方）
CARD_PLATFORM_NAMES = {"qq_official", "qq_official_webhook", "qqofficial"}

#: 按钮样式：0 灰色线框，1 蓝色线框
BUTTON_STYLE_DEFAULT = 1

#: 单行最多按钮数
MAX_BUTTONS_PER_ROW = 5


def _build_keyboard(buttons: list[list[tuple[str, str]]]) -> dict[str, Any] | None:
    """把 (label, data) 二维结构转换成 QQ 官方的 keyboard 字段。"""
    rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(buttons[:5]):
        if not row:
            continue
        items = []
        for col_index, (label, data) in enumerate(row[:MAX_BUTTONS_PER_ROW]):
            items.append(
                {
                    "id": f"b{row_index}_{col_index}",
                    "render_data": {
                        "label": label,
                        "visited_label": label,
                        "style": BUTTON_STYLE_DEFAULT,
                    },
                    "action": {
                        "type": 2,  # 2 = 指令按钮：点击后把 data 填入输入框
                        "permission": {"type": 2},  # 所有人可点击
                        "data": data,
                        "unsupport_tips": "请升级 QQ 至最新版本后使用按钮",
                    },
                }
            )
        if items:
            rows.append({"buttons": items})
    if not rows:
        return None
    return {"content": {"rows": rows}}


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    """剔除 None 值：QQ 官方接口不接受显式的 null 字段。"""
    return {k: v for k, v in payload.items() if v is not None}


class CardSender:
    """统一的卡片 / 文本消息发送器。"""

    def __init__(self, context: Any, *, use_card: bool = True) -> None:
        self.context = context
        self.use_card = bool(use_card)
        self._seq: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 平台能力判断
    # ------------------------------------------------------------------
    @staticmethod
    def platform_name(event: AstrMessageEvent) -> str:
        try:
            return str(event.get_platform_name() or "")
        except Exception:
            return ""

    @classmethod
    def supports_markdown(cls, event: AstrMessageEvent) -> bool:
        return cls.platform_name(event) in MD_PLATFORM_NAMES

    def supports_card(self, event: AstrMessageEvent) -> bool:
        return self.use_card and self.platform_name(event) in CARD_PLATFORM_NAMES

    def _platform_inst(self, platform_id: str) -> Any:
        try:
            return self.context.get_platform_inst(platform_id)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 被动发送
    # ------------------------------------------------------------------
    async def send(self, event: AstrMessageEvent, card: Card) -> None:
        """回复当前消息。优先使用卡片，失败或平台不支持时自动降级。"""
        md_supported = self.supports_markdown(event)
        text = card.text_for(md_supported)

        if self.supports_card(event) and card.has_buttons():
            if await self._send_card(event, card):
                return

        result = event.chain_result([Plain(text)])
        if md_supported:
            result.use_markdown(True)
        await event.send(result)

    async def _send_card(self, event: AstrMessageEvent, card: Card) -> bool:
        """尝试用 botpy 直发带按钮的卡片。"""
        try:
            platform = self._platform_inst(event.get_platform_id())
            client = self._client_of(platform)
            if client is None:
                return False

            message_obj = event.message_obj
            session_id = str(getattr(message_obj, "session_id", "") or "")
            group_id = str(getattr(message_obj, "group_id", "") or "")
            scene = self._scene_of(platform, session_id, bool(group_id))
            markdown = {"content": card.markdown}
            keyboard = _build_keyboard(card.buttons)
            msg_id = str(getattr(message_obj, "message_id", "") or "")

            if scene == "group" and group_id:
                payload: dict[str, Any] = {
                    "group_openid": group_id,
                    "msg_type": 2,
                    "markdown": markdown,
                    "msg_seq": self._next_seq(msg_id or group_id),
                }
                if msg_id:
                    payload["msg_id"] = msg_id
                if keyboard:
                    payload["keyboard"] = keyboard
                ret = await client.api.post_group_message(**_clean(payload))
                return ret is not None
            if scene == "friend":
                openid = str(event.get_sender_id() or "")
                if not openid:
                    return False
                payload = {
                    "openid": openid,
                    "msg_type": 2,
                    "markdown": markdown,
                    "msg_seq": self._next_seq(msg_id or openid),
                }
                if msg_id:
                    payload["msg_id"] = msg_id
                if keyboard:
                    payload["keyboard"] = keyboard
                ret = await client.api.post_c2c_message(**_clean(payload))
                return ret is not None
            # 频道（channel）场景不支持指令按钮，交给标准链路处理
            return False
        except Exception as exc:
            logger.warning(f"[staridol] 卡片发送失败，已回退为普通消息：{exc}")
            return False

    # ------------------------------------------------------------------
    # 主动推送
    # ------------------------------------------------------------------
    async def push(self, umo: str, card: Card) -> bool:
        """向指定会话主动推送消息（定时任务使用）。"""
        platform_id, session_id, is_group = self._parse_umo(umo)
        platform = self._platform_inst(platform_id) if platform_id else None
        md_supported = self._is_md_platform(platform)

        if self.use_card and card.has_buttons() and platform is not None:
            if await self._push_card(platform, umo, session_id, is_group, card):
                return True

        chain = MessageChain(chain=[Plain(card.text_for(md_supported))])
        if md_supported:
            chain.use_markdown(True)
        try:
            return bool(await self.context.send_message(umo, chain))
        except Exception as exc:
            logger.warning(f"[staridol] 主动推送失败（{umo}）：{exc}")
            return False

    async def _push_card(
        self,
        platform: Any,
        umo: str,
        session_id: str,
        is_group: bool,
        card: Card,
    ) -> bool:
        try:
            client = self._client_of(platform)
            if client is None or not session_id:
                return False
            keyboard = _build_keyboard(card.buttons)
            payload: dict[str, Any] = {
                "msg_type": 2,
                "markdown": {"content": card.markdown},
                "msg_seq": self._next_seq(f"push:{session_id}"),
            }
            if keyboard:
                payload["keyboard"] = keyboard
            if is_group:
                ret = await client.api.post_group_message(
                    **_clean({"group_openid": session_id, **payload})
                )
            else:
                ret = await client.api.post_c2c_message(
                    **_clean({"openid": session_id, **payload})
                )
            if ret is None:
                return False
            return True
        except Exception as exc:
            logger.warning(f"[staridol] 卡片主动推送失败，改用标准链路（{umo}）：{exc}")
            return False

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _client_of(platform: Any) -> Any:
        if platform is None:
            return None
        client = getattr(platform, "client", None)
        if client is None and hasattr(platform, "get_client"):
            try:
                client = platform.get_client()
            except Exception:
                client = None
        if client is None or not hasattr(client, "api"):
            return None
        return client

    @staticmethod
    def _scene_of(platform: Any, session_id: str, has_group: bool) -> str:
        scene = ""
        sessions = getattr(platform, "_session_scene", None)
        if isinstance(sessions, dict) and session_id:
            scene = str(sessions.get(session_id) or "")
        if scene:
            return scene
        return "group" if has_group else "friend"

    @staticmethod
    def _parse_umo(umo: str) -> tuple[str, str, bool]:
        """解析 unified_msg_origin：``platform_id:message_type:session_id``。"""
        parts = str(umo or "").split(":")
        if len(parts) < 3:
            return "", "", False
        platform_id = parts[0]
        message_type = parts[1]
        session_id = ":".join(parts[2:])
        is_group = message_type in ("GroupMessage", "GROUP_MESSAGE", "group")
        return platform_id, session_id, is_group

    def _is_md_platform(self, platform: Any) -> bool:
        if platform is None:
            return False
        try:
            return str(platform.meta().name) in MD_PLATFORM_NAMES
        except Exception:
            return False

    def _next_seq(self, key: str) -> int:
        """为同一条消息生成递增的 msg_seq（QQ 要求 msg_id + msg_seq 唯一）。"""
        current = self._seq.get(key, random.randint(1, 500)) + 1
        if current > 60000:
            current = 1
        self._seq[key] = current
        return current
