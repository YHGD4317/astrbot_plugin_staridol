"""业务系统的通用返回类型。"""

from __future__ import annotations

from dataclasses import dataclass

from .render import Card


@dataclass
class Result:
    """一次操作的结果。

    Attributes:
        ok: 操作是否成功。
        text: 简易文本（当 card 为空时使用）。
        card: 需要发送的卡片。
        silent: 为 True 时表示"静默处理"，不回复任何内容
            （例如非本人点击了他人事务卡片的按钮）。
    """

    ok: bool = True
    text: str = ""
    card: Card | None = None
    silent: bool = False

    @classmethod
    def success(cls, text: str = "", card: Card | None = None) -> Result:
        return cls(True, text, card)

    @classmethod
    def fail(cls, text: str, card: Card | None = None) -> Result:
        return cls(False, text, card)

    @classmethod
    def quiet(cls) -> Result:
        """静默：不回复。"""
        return cls(False, "", None, silent=True)
