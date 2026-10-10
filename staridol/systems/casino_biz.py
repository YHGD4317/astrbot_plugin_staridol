"""投资赌场系统：玩家开设、供 NPC 游玩的经营赌场。

与玩家自己下注的玩法赌场（``casino.py``）不同，这里经营的是"别人来玩的赌场"。

规则概述：
  * 每个玩家可开设一间以自己名字命名的赌场，并自行设置筹码汇率（一枚筹码 = …w）。
  * 玩家通过「投资赌场（数额）」注入赌资（``invest``）。
  * NPC 会周期性地进入赌场游玩：荷官赢了（NPC 输了）会让 ``invest`` 与
    ``reserved``（可领取净利润）增加；荷官输了（NPC 赢了）钱会被 NPC 拿走，
    ``invest`` 与可领取净利润随之减少。
  * 「领取分红」把 ``invest - injected``（净利润）提现，之后资金池回到注入底金。
  * 「查询赌场」展示经营面板：注资、今日收益、玩法/人流。

所有面向玩家的文案均不使用 emoji。
"""

from __future__ import annotations

import random

from .. import constants as C
from .. import render as R
from .. import utils as U
from ..models import CasinoBiz, Player
from ..result import Result
from ..store import GameStore

#: NPC 游玩的玩法（比玩家对局玩法更丰富，含多种人气项目）。
#: 键 -> 展示名。展示顺序即面板顺序。
NPC_GAMES: dict[str, str] = {
    "blackjack": "21点",
    "baccarat": "百家乐",
    "poker": "德州扑克",
    "dragon_tiger": "龙虎斗",
    "dice": "猜大小",
    "roulette": "轮盘",
    "slot": "老虎机",
    "mahjong": "麻将",
    "shisanshui": "十三水",
    "duel": "骰子大战",
}

#: 荷官胜率基线（赌场吃小赔大的"庄家优势"，明显高于一半才能稳定盈利）。
HOUSE_EDGE = 0.62

#: 每批 NPC 游玩所间隔的时长范围（秒），控制游玩节奏，避免资金暴涨。
VISIT_INTERVAL_RANGE = (600.0, 1800.0)

#: 每批 NPC 到来后的游玩局数范围。
BATCH_ROUNDS_RANGE = (3, 8)

#: NPC 姓名池（避免与玩家混淆，只取单名风格）。
_NPC_NAMES = [
    "赵老板", "钱富贵", "孙少", "李四", "周三", "王胖子", "陈爷", "黄赌徒",
    "马公子", "老潘", "阿坤", "大壮", "刘总", "小杜", "顾爷", "阿豪",
]

#: 筹码汇率的允许范围（w），防止调出离谱数值。
CHIP_VALUE_RANGE = (1, 100000)


# --------------------------------------------------------------------------
# 基础
# --------------------------------------------------------------------------
def ensure(player: Player) -> CasinoBiz:
    """确保玩家已有经营赌场对象（首次投资时创建）。"""
    if player.casino_biz is None:
        player.casino_biz = CasinoBiz()
    return player.casino_biz


def casino_name(player: Player) -> str:
    """赌场名称以集团（公司）冠名，例如「星海集团赌场」。

    优先使用集团名 ``player.company_name``（如「星海集团」），未登记集团时
    退而使用玩家昵称。
    """
    base = (player.company_name or player.custom_name or player.name or "无名").strip()
    if base.endswith("赌场"):
        return base
    return f"{base}赌场"


def _roll_daily(biz: CasinoBiz, ts: float | None = None) -> bool:
    """跨天时把今日收益归零，返回是否发生了跨天。"""
    ts = ts if ts is not None else U.now()
    today = U.today_str(ts)
    if biz.today_date != today:
        biz.today_date = today
        biz.today_income = 0
        return True
    return False


def _bet_amount(biz: CasinoBiz) -> int:
    """根据当前资金池与筹码汇率估算一局的赌注（w）。

    赌注约为资金池的 0.2%~0.8%，同时以筹码汇率作下限，保证小额资金池也能开局。
    """
    base = max(biz.chip_value, 1)
    scaled = int(biz.invest * random.uniform(0.002, 0.008))
    return max(1, max(base, scaled))


def _npc_round(biz: CasinoBiz, name: str) -> str:
    """模拟一次 NPC 游玩，返回一句事件描述。"""
    game_name = random.choice(list(NPC_GAMES.values()))
    bet = _bet_amount(biz)
    if random.random() < HOUSE_EDGE:
        # 荷官赢（NPC 输）：资金池与净利润增加
        bet = max(1, bet)
        biz.invest += bet
        biz.today_income += bet
        biz.total_income += bet
        return f"{name}在{game_name}押了 {U.fmt_money(bet)}，输光了筹码。"
    # 荷官输（NPC 赢）：钱被 NPC 拿走
    deduct = min(bet, int(biz.invest))
    biz.invest = max(0, biz.invest - deduct)
    biz.today_income -= deduct
    biz.total_income -= deduct
    return f"{name}在{game_name}赢走 {U.fmt_money(deduct)}。"


def npc_tick(store: GameStore, player: Player, ts: float | None = None) -> list[str]:
    """让 NPC 进入玩家的赌场游玩一批，返回事件描述。

    仅在时间间隔达标、且赌场已有注资（资金池）时才发生；资金池为 0 时
    荷官无钱可赔，NPC 游玩暂停（玩家需「投资赌场」补充）。
    """
    ts = ts if ts is not None else U.now()
    biz = ensure(player)
    if biz.invest <= 0:
        return []
    _roll_daily(biz, ts)
    if ts - biz.last_visit_at < random.uniform(*VISIT_INTERVAL_RANGE):
        return []
    biz.last_visit_at = ts
    rounds = random.randint(*BATCH_ROUNDS_RANGE)
    events: list[str] = []
    visitors: list[str] = []
    for _ in range(rounds):
        if biz.invest <= 0:
            break
        name = random.choice(_NPC_NAMES)
        events.append(_npc_round(biz, name))
        players_game = random.choice(list(NPC_GAMES.values()))
        biz.traffic[players_game] = biz.traffic.get(players_game, 0) + 1
        biz.npc_count += 1
        if name not in visitors and len(visitors) < 5:
            visitors.append(name)
    biz.recent_visitors = visitors
    store.mark_dirty()
    return events


# --------------------------------------------------------------------------
# 指令处理
# --------------------------------------------------------------------------
def panel(store: GameStore, player: Player) -> Result:
    """查询赌场经营面板。"""
    biz = ensure(player)
    _roll_daily(biz)
    if biz.invest <= 0 and biz.injected <= 0:
        lines = [
            f"# {casino_name(player)}",
            "你还没有给赌场注资。",
            "",
            "NPC 会随时来你的赌场玩耍，但荷官需要足够的注资才能赔得起。",
            f"发送「投资赌场（数额）」注入赌资开始经营，例如：投资赌场1000。",
            f"当前筹码汇率：1 筹码 = {C.CHIP_VALUE}w，可发送「调整赌场汇率（数额）」修改。",
            "",
            "> 说明：荷官赢了（NPC 输）会增加注资，NPC 赢了则会拿走一部分钱。",
            "> 「领取分红」可以把净利提现，例如查询后发送「领取分红」。",
        ]
        return Result.success(card=R.Card(markdown="\n".join(lines)))
    return Result.success(card=R.render_casino_biz(player))


def invest(store: GameStore, player: Player, amount: int) -> Result:
    """投资赌场：注入赌资。"""
    amount = int(amount or 0)
    if amount <= 0:
        return Result.fail("投资金额需要大于 0，例如「投资赌场1000」。")
    if not player.can_afford(amount):
        return Result.fail(
            f"资金不足：需要 {U.fmt_money(amount)}，你当前有 {U.fmt_money(player.money)}。"
        )
    player.pay(amount)
    biz = ensure(player)
    biz.invest += amount
    biz.injected += amount
    store.mark_dirty()
    player.push_log(f"投资赌场 {U.fmt_money(amount)}")
    return Result.success(
        text=(
            f"已给「{casino_name(player)}」注入 {U.fmt_money(amount)} 赌资\n"
            f"**注资**：{U.fmt_money(biz.invest)}　**可领分红**：{U.fmt_money(biz.claimable)}\n"
            f"发送「查询赌场」查看经营面板；NPC 会陆续来游玩。"
        )
    )


def set_chip_value(store: GameStore, player: Player, value: int) -> Result:
    """调整赌场筹码汇率（一枚筹码的价值，w）。"""
    low, high = CHIP_VALUE_RANGE
    value = int(value or 0)
    if value < low or value > high:
        return Result.fail(f"筹码汇率需要在 {low}~{high}w 之间（建议 100w = 1 亿/100）。")
    biz = ensure(player)
    biz.chip_value = value
    store.mark_dirty()
    return Result.success(
        text=(
            f"已调整「{casino_name(player)}」的筹码汇率：1 筹码 = {U.fmt_money(value)}。\n"
            "NPC 下注会按新汇率计算。"
        )
    )


def collect_dividend(store: GameStore, player: Player) -> int:
    """领取赌场分红：把净利（invest - injected）提现，返回领取金额。

    提现后资金池回到注入底金，避免无限制取空。
    """
    biz = ensure(player)
    claim = biz.claimable
    if claim <= 0:
        return 0
    player.earn(claim)
    biz.invest -= claim
    store.mark_dirty()
    return claim
