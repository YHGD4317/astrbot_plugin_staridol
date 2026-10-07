"""每日刷新：事务、秀场、商业活动、商城、股市、公司收益、贷款利息。"""

from __future__ import annotations

from .. import utils as U
from ..models import Player
from ..store import GameStore
from .. import stocks as stock_sys
from . import artist as artist_sys
from . import economy as eco_sys
from . import project as project_sys
from . import quest as quest_sys


def ensure_daily(store: GameStore, player: Player, ts: float | None = None) -> dict:
    """确保玩家数据已按自然日刷新（幂等）。

    返回本次刷新产生的摘要信息，供上层决定是否提示玩家。
    """
    ts = ts if ts is not None else U.now()
    today = U.today_str(ts)
    is_new_day = player.last_quest_date not in ("", today)
    show_before = player.show_left
    market_before = player.market_left

    quest_reset = quest_sys.reset_quests(store, player, ts)
    artist_sys.ensure_daily_show(store, player, ts)
    project_sys.ensure_daily_market(store, player, ts)
    eco_sys.refresh_shop(store, player, ts)
    stock_sys.refresh_daily(store, ts)
    company_income = eco_sys.settle_companies(store, player, ts)
    loan_interest = eco_sys.settle_loan_interest(store, player)
    expired = player.clear_expired_buffs(ts)
    if expired:
        store.mark_dirty()

    if is_new_day:
        player.push_log(f"新的一天开始，事务 {len(player.quests)} 条")

    tier = store.tier_info(player)
    return {
        "new_day": is_new_day,
        "quest_reset": quest_reset,
        "quest_count": len(player.quests),
        "company_income": company_income,
        "loan_interest": loan_interest,
        "show_reset": player.show_left != show_before,
        "market_reset": player.market_left != market_before,
        "tier": tier,
    }


def new_day_notice(info: dict, player: Player) -> str:
    """生成跨天提示文本（仅在确有变化时返回内容）。"""
    if not info.get("new_day"):
        return ""
    lines = ["**新的一天开始了**"]
    if info.get("quest_reset"):
        lines.append(f"今日事务已刷新，共 **{info['quest_count']}** 条，发送「今日行程」开始处理。")
    if info.get("company_income"):
        lines.append(f"下属公司昨日收益 {U.fmt_money(info['company_income'])}，可发送「收取分红」提取。")
    if info.get("loan_interest"):
        lines.append(f"银行贷款利息已扣除 {U.fmt_money(info['loan_interest'])}。")
    if info.get("show_reset"):
        lines.append(f"今日秀场次数已恢复（{player.show_left} 次）。")
    if info.get("market_reset"):
        lines.append(f"今日商业活动次数已恢复（{player.market_left} 次），发送「商业活动」查看项目。")
    lines.append("股市今日已重新招标，发送「股市」查看行情。")
    return "\n".join(lines)
