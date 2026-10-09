"""赌场系统：21点、猜大小、骰子大战、轮盘、老虎机。

一枚筹码 = C.CHIP_VALUE（默认 100w）。下注以 1 筹码起步，资金单位与全游戏一致（w）。
下注金额在开局时即从玩家资金中扣除，结算时按结果返还 / 加彩。所有文案不使用 emoji。

会话保存在 ``player.casino``，未下完的赌局可随时发送「离开赌场」结束（已下注不退）。
"""

from __future__ import annotations

import random

from .. import constants as C
from .. import render as R
from .. import utils as U
from ..models import Player
from ..result import Result
from ..store import GameStore

#: 单局最高下注筹码数，防止赔付失衡
MAX_BET_CHIPS = 500

#: 玩法内部名 -> 中文名
GAME_CN: dict[str, str] = {
    "blackjack": "21点",
    "dice": "猜大小",
    "duel": "骰子大战",
    "roulette": "轮盘",
    "slot": "老虎机",
}

#: 玩法中文名 / 一句话规则 / 快捷指令（按展示顺序排列）
GAME_DESC: dict[str, tuple[str, str, str]] = {
    "blackjack": ("21点", "尽量让点数接近 21 且不超过，与庄家比大小。", "开始21点"),
    "dice": ("猜大小", "押一枚骰子的大小/单双/具体点数。", "开始猜大小"),
    "duel": ("骰子大战", "与庄家各掷两枚骰子，点数高者胜。", "开始骰子大战"),
    "roulette": ("轮盘", "押红/黑/单双/具体点，命中大额赔付。", "开始轮盘"),
    "slot": ("老虎机", "拉杆转出三枚符号，对子与三连赢筹码。", "开始老虎机"),
}

#: 玩法中文别名 -> 内部名
_GAME_ALIAS: dict[str, str] = {
    "21点": "blackjack",
    "黑杰克": "blackjack",
    "猜大小": "dice",
    "骰子大战": "duel",
    "骰子对决": "duel",
    "轮盘": "roulette",
    "轮盘赌": "roulette",
    "老虎机": "slot",
    "拉霸机": "slot",
}

_SUITS = ["♠", "♥", "♦", "♣"]
_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
_SLOT_SYMBOLS = ["A", "K", "Q", "J", "7", "◆", "★"]
_SLOT_PAY: dict[str, int] = {"A": 20, "K": 15, "Q": 12, "J": 10, "7": 8, "◆": 6, "★": 5}
_RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def chip_value() -> int:
    """单枚筹码的价值（w）。"""
    return int(getattr(C, "CHIP_VALUE", 100))


def chips_to_w(chips: int) -> int:
    return int(chips) * chip_value()


def resolve_game(cn: str) -> str | None:
    return _GAME_ALIAS.get(cn)


# --------------------------------------------------------------------------
# 通用
# --------------------------------------------------------------------------
def _balance_line(player: Player) -> str:
    return f"**现金**：{U.fmt_money(player.cash)}　**存款**：{U.fmt_money(player.deposit)}"


def menu(player: Player) -> Result:
    lines = [
        "# 赌场",
        f"**一枚筹码 = {chip_value()}w**，下注以 **1 筹码** 起步（即一次最低 {U.fmt_money(chips_to_w(1))}），"
        f"可用资金 {U.fmt_money(player.money)}。",
        "",
    ]
    for name, desc, _cmd in GAME_DESC.values():
        lines.append(f"**{name}**：{desc}")
    lines += [
        "",
        "选择下方玩法，点击按钮自动填入开始指令；可在指令后追加下注筹码数，例如「开始21点5筹码」。",
        "> 未下完的赌局可随时发送「离开赌场」结束（已下注不退）。",
        "> 发送「离开赌场」结束当前赌局；发送「赌场」随时查看玩法。",
    ]
    buttons = [[R.button(name, cmd)] for name, _desc, cmd in GAME_DESC.values()]
    return Result.success(card=R.Card(markdown="\n".join(lines)).with_buttons(buttons))


def start(store: GameStore, player: Player, game_key: str, chips: int) -> Result:
    """开启一局：扣除下注筹码，进行开局结算或进入等待操作阶段。"""
    if game_key not in GAME_DESC:
        return Result.fail("没有这个玩法。发送「赌场」查看可选玩法。")
    chips = max(1, int(chips or 1))
    if chips > MAX_BET_CHIPS:
        return Result.fail(f"单局下注最多 {MAX_BET_CHIPS} 筹码，当前输入 {chips} 筹码。")
    if player.casino and player.casino.get("game") in ("blackjack", "dice", "roulette"):
        pending = GAME_CN.get(player.casino.get("game"), "")
        return Result.fail(
            f"你还有一局未下完的「{pending}」，请先发送「离开赌场」结束它再开新局。"
        )
    amount = chips_to_w(chips)
    if not player.can_afford(amount):
        return Result.fail(
            f"下注 {chips} 筹码（{U.fmt_money(amount)}）超出当前资金 {U.fmt_money(player.money)}。"
        )
    player.pay(amount)
    store.mark_dirty()
    if game_key == "blackjack":
        return _bj_start(store, player, chips, amount)
    if game_key == "duel":
        return _duel_start(store, player, chips, amount)
    if game_key == "slot":
        return _slot_start(store, player, chips, amount)
    player.casino = {"game": game_key, "chips": chips, "amount": amount}
    if game_key == "dice":
        return _dice_prompt(player, chips, amount)
    return _roulette_prompt(player, chips, amount)


def verb(store: GameStore, player: Player, verb_str: str) -> Result:
    """处理 21点 的 <要牌/停牌/加倍/投降> 等操作与老虎机再拉。"""
    game = player.casino.get("game") if player.casino else ""
    if game == "blackjack":
        if verb_str in ("要牌", "加牌", "再来一张"):
            return _bj_hit(store, player)
        if verb_str in ("停牌", "够了", "不要了"):
            return _bj_stand(store, player)
        if verb_str == "加倍":
            return _bj_double(store, player)
        if verb_str in ("投降", "弃牌"):
            return _bj_surrender(store, player)
    if verb_str in ("拉杆", "再拉一次"):
        return Result.fail("老虎机每局单独结算，发送「开始老虎机（筹码数）」来拉杆下注。")
    return Result.fail("当前玩法不支持该操作，请按卡片提示进行。")


def guess(store: GameStore, player: Player, choice: str) -> Result:
    """处理猜大小 / 轮盘的具体押注选项。"""
    game = player.casino.get("game") if player.casino else ""
    if game == "dice":
        return _dice_guess(store, player, choice)
    if game == "roulette":
        return _roulette_guess(store, player, choice)
    return Result.fail("当前玩法不需要押这种选择，请按卡片提示进行。")


# --------------------------------------------------------------------------
# 21点
# --------------------------------------------------------------------------
def _draw() -> dict[str, str]:
    suit = random.choice(_SUITS)
    rank = random.choice(_RANKS)
    return {"suit": suit, "rank": rank, "text": f"{suit}{rank}"}


def _hand_value(cards) -> int:
    value = 0
    aces = 0
    for card in cards:
        r = card["rank"]
        if r == "A":
            aces += 1
            value += 11
        elif r in ("J", "Q", "K"):
            value += 10
        else:
            value += int(r)
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value


def _dealer_finish(dealer_hand) -> list:
    while _hand_value(dealer_hand) < 17:
        dealer_hand.append(_draw())
    return dealer_hand


def _bj_outcome(amount: int, pv: int, dv: int) -> tuple[int, int, str]:
    """返回 (净返还net, 盈亏profit, 结算语)。bet 已预扣，net 为需补回金额。"""
    if pv > 21:
        return 0, -amount, f"你爆牌（点数 {pv}），输掉 {U.fmt_money(amount)}。"
    if dv > 21:
        return 2 * amount, amount, f"庄家爆牌（点数 {dv}）！你赢 {U.fmt_money(amount)}。"
    if pv > dv:
        return 2 * amount, amount, f"你的 {pv} 大于庄家 {dv}，赢 {U.fmt_money(amount)}。"
    if pv == dv:
        return amount, 0, f"双方都是 {pv}，平局退回下注。"
    return 0, -amount, f"你的 {pv} 小于庄家 {dv}，输掉 {U.fmt_money(amount)}。"


def _bj_start(store: GameStore, player: Player, chips: int, amount: int) -> Result:
    player_hand = [_draw(), _draw()]
    dealer_hand = [_draw(), _draw()]
    session = {
        "game": "blackjack",
        "chips": chips,
        "amount": amount,
        "player_hand": player_hand,
        "dealer_hand": dealer_hand,
        "doubled": False,
    }
    pv = _hand_value(player_hand)
    dv = _hand_value(dealer_hand)
    if pv == 21:
        if dv == 21:
            player.earn(amount)
            msg = "双方都是天然 21 点，平局退回下注。"
            profit = 0
        else:
            payoff = int(amount * 1.5)
            player.earn(amount + payoff)
            msg = f"你起手就是 21 点（黑杰克）！按 1.5 倍赔付 {U.fmt_money(payoff)}。"
            profit = payoff
        return _bj_final(store, player, session, player_hand, dealer_hand, msg, profit)
    player.casino = session
    return _bj_prompt(store, player, session)


def _bj_prompt(store: GameStore, player: Player, session: dict) -> Result:
    hand = session["player_hand"]
    dhand = session["dealer_hand"]
    pv = _hand_value(hand)
    dv = _hand_value(dhand)
    lines = [
        "# 21点",
        f"**已下注**：{session['chips']} 筹码（{U.fmt_money(session['amount'])}）",
        "",
        f"**你的牌**：{' '.join(c['text'] for c in hand)}　**点数 {pv}**",
        f"**庄家的牌**：{' '.join(c['text'] for c in dhand)}　**点数 {dv}**",
        "",
        "选择下一步：",
        "- 要牌：再抽一张，点数超过 21 即爆牌；",
        "- 停牌：与庄家比点数结束本局；",
        "- 加倍：仅首两张时可，加注一倍并只抽一张；",
        "- 投降：仅首两张时可，退回一半下注。",
    ]
    buttons = [[R.button("要牌", "要牌"), R.button("停牌", "停牌")]]
    if len(hand) == 2 and not session["doubled"]:
        buttons.append([R.button("加倍", "加倍"), R.button("投降", "投降")])
    return Result.success(card=R.Card(markdown="\n".join(lines)).with_buttons(buttons))


def _bj_hit(store: GameStore, player: Player) -> Result:
    session = player.casino
    hand = session["player_hand"]
    hand.append(_draw())
    pv = _hand_value(hand)
    if pv > 21:
        return _bj_final(
            store, player, session, hand, session["dealer_hand"],
            f"你爆牌（点数 {pv}），输掉 {U.fmt_money(session['amount'])}。", -session["amount"],
        )
    return _bj_prompt(store, player, session)


def _bj_stand(store: GameStore, player: Player) -> Result:
    session = player.casino
    dealer_hand = _dealer_finish(session["dealer_hand"])
    pv = _hand_value(session["player_hand"])
    dv = _hand_value(dealer_hand)
    net, profit, msg = _bj_outcome(session["amount"], pv, dv)
    player.earn(net)
    return _bj_final(store, player, session, session["player_hand"], dealer_hand, msg, profit)


def _bj_double(store: GameStore, player: Player) -> Result:
    session = player.casino
    if len(session["player_hand"]) != 2 or session["doubled"]:
        return Result.fail("加倍只能在刚发完首两张牌时使用。")
    amount = session["amount"]
    if not player.can_afford(amount):
        return Result.fail(f"资金不足：加倍需要再补 {U.fmt_money(amount)}。")
    player.pay(amount)
    session["amount"] = amount * 2
    session["chips"] = session["chips"] * 2
    session["doubled"] = True
    hand = session["player_hand"]
    hand.append(_draw())
    pv = _hand_value(hand)
    store.mark_dirty()
    if pv > 21:
        return _bj_final(
            store, player, session, hand, session["dealer_hand"],
            f"加倍后爆牌（点数 {pv}），输掉 {U.fmt_money(session['amount'])}。", -session["amount"],
        )
    dealer_hand = _dealer_finish(session["dealer_hand"])
    dv = _hand_value(dealer_hand)
    net, profit, msg = _bj_outcome(session["amount"], pv, dv)
    player.earn(net)
    return _bj_final(store, player, session, hand, dealer_hand, msg, profit)


def _bj_surrender(store: GameStore, player: Player) -> Result:
    session = player.casino
    if len(session["player_hand"]) != 2:
        return Result.fail("弃牌投降只能在首两张牌时使用。")
    back = session["amount"] // 2
    player.earn(back)
    return _bj_final(
        store, player, session, session["player_hand"], session["dealer_hand"],
        f"你选择投降，退回一半下注 {U.fmt_money(back)}。", back - session["amount"],
    )


def _bj_final(store: GameStore, player: Player, session: dict, player_hand, dealer_hand, headline: str, profit: int) -> Result:
    player.casino.clear()
    store.mark_dirty()
    lines = [
        "# 21点 · 结算",
        f"**你的牌**：{' '.join(c['text'] for c in player_hand)}　点数 {_hand_value(player_hand)}",
        f"**庄家的牌**：{' '.join(c['text'] for c in dealer_hand)}　点数 {_hand_value(dealer_hand)}",
        "",
        f"**{U.fmt_signed(profit)}**　{headline}",
        "",
        _balance_line(player),
        "",
        "发送「赌场」继续下一局。",
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)))


# --------------------------------------------------------------------------
# 猜大小（单枚骰子）
# --------------------------------------------------------------------------
def _dice_prompt(player: Player, chips: int, amount: int) -> Result:
    lines = [
        "# 猜大小",
        f"**已下注**：{chips} 筹码（{U.fmt_money(amount)}）",
        "",
        "荷官即将掷出一枚骰子（1~6），选择你的押法：",
        "- 大（4/5/6）、小（1/2/3）、单、双：命中赢 1:1；",
        "- 押具体点数（1~6）：命中赢 5:1。",
    ]
    buttons = [
        [R.button("押大", "押大"), R.button("押小", "押小"), R.button("押单", "押单"), R.button("押双", "押双")],
        [R.button("押1", "押1"), R.button("押2", "押2"), R.button("押3", "押3"), R.button("押4", "押4"), R.button("押5", "押5")],
        [R.button("押6", "押6")],
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)).with_buttons(buttons))


def _dice_guess(store: GameStore, player: Player, choice: str) -> Result:
    session = player.casino
    amount = session["amount"]
    roll = random.randint(1, 6)
    if choice in ("大", "小", "单", "双"):
        hit = False
        if choice == "大" and roll in (4, 5, 6):
            hit = True
        elif choice == "小" and roll in (1, 2, 3):
            hit = True
        elif choice == "单" and roll % 2 == 1:
            hit = True
        elif choice == "双" and roll % 2 == 0:
            hit = True
        mult = 2 if hit else 0
        head = f"掷出 {roll}，{choice} " + ("命中！" if hit else "未命中。")
    else:
        target = int(choice)
        if target < 1 or target > 6:
            return Result.fail("猜大小押点数需要是 1~6。")
        hit = roll == target
        mult = 6 if hit else 0
        head = f"掷出 {roll}，押 {target} " + ("命中！5 倍赔付！" if hit else "未命中。")
    net = amount * mult
    profit = net - amount
    player.earn(net)
    player.casino.clear()
    store.mark_dirty()
    lines = [
        "# 猜大小 · 结算",
        f"**掷出**：{roll}　**押法**：{choice}",
        f"**{U.fmt_signed(profit)}**　{head}",
        "",
        _balance_line(player),
        "",
        "发送「赌场」继续下一局。",
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)))


# --------------------------------------------------------------------------
# 骰子大战（与庄家各掷两枚骰子比点）
# --------------------------------------------------------------------------
def _duel_start(store: GameStore, player: Player, chips: int, amount: int) -> Result:
    pr = sum(random.randint(1, 6) for _ in range(2))
    dr = sum(random.randint(1, 6) for _ in range(2))
    if pr > dr:
        net, profit, head = 2 * amount, amount, f"你掷出 {pr}，庄家掷出 {dr}，你赢了！"
    elif pr == dr:
        net, profit, head = amount, 0, f"双方都掷出 {pr}，平局退回下注。"
    else:
        net, profit, head = 0, -amount, f"你掷出 {pr}，庄家掷出 {dr}，你输了。"
    player.earn(net)
    store.mark_dirty()
    lines = [
        "# 骰子大战 · 结算",
        f"**你**：{pr}（两枚骰子）　**庄家**：{dr}",
        f"**{U.fmt_signed(profit)}**　{head}",
        "",
        _balance_line(player),
        "",
        "发送「开始骰子大战（筹码数）」再战一局。",
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)))


# --------------------------------------------------------------------------
# 轮盘（0~36）
# --------------------------------------------------------------------------
def _roulette_prompt(player: Player, chips: int, amount: int) -> Result:
    lines = [
        "# 轮盘",
        f"**已下注**：{chips} 筹码（{U.fmt_money(amount)}）",
        "",
        "选择一个押法：",
        "- 红 / 黑 / 单 / 双：命中赢 1:1；",
        "- 押具体数字（0~36）：命中赢 35:1。",
        "- 0 是绿色，只能押具体数字，不能押红黑单双。",
        "",
        "发送如「押红」「押17」来完成本局。",
    ]
    buttons = [
        [R.button("押红", "押红"), R.button("押黑", "押黑"), R.button("押单", "押单"), R.button("押双", "押双")],
        [R.button("押0", "押0"), R.button("押7", "押7"), R.button("押17", "押17"), R.button("押21", "押21")],
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)).with_buttons(buttons))


def _roulette_guess(store: GameStore, player: Player, choice: str) -> Result:
    session = player.casino
    amount = session["amount"]
    outcome = random.randint(0, 36)
    if choice in ("红", "黑", "单", "双"):
        if choice == "红":
            hit = outcome in _RED
        elif choice == "黑":
            hit = outcome != 0 and outcome not in _RED
        elif choice == "单":
            hit = outcome != 0 and outcome % 2 == 1
        else:
            hit = outcome != 0 and outcome % 2 == 0
        color = "红" if outcome in _RED else ("黑" if outcome != 0 else "绿0")
        mult = 2 if hit else 0
        head = f"开出 {outcome}（{color}），{choice} " + ("命中！" if hit else "未命中。")
    else:
        target = int(choice)
        if target < 0 or target > 36:
            return Result.fail("轮盘押点需要在 0~36 之间。")
        hit = outcome == target
        mult = 36 if hit else 0
        head = f"开出 {outcome}，押 {target} " + ("命中单点！35 倍赔付！" if hit else "未命中。")
    net = amount * mult
    profit = net - amount
    player.earn(net)
    player.casino.clear()
    store.mark_dirty()
    lines = [
        "# 轮盘 · 结算",
        f"**开出**：{outcome}　**押法**：{choice}",
        f"**{U.fmt_signed(profit)}**　{head}",
        "",
        _balance_line(player),
        "",
        "发送「赌场」继续下一局。",
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)))


# --------------------------------------------------------------------------
# 老虎机
# --------------------------------------------------------------------------
def _slot_start(store: GameStore, player: Player, chips: int, amount: int) -> Result:
    reels = [random.choice(_SLOT_SYMBOLS) for _ in range(3)]
    if reels[0] == reels[1] == reels[2]:
        mult = _SLOT_PAY[reels[0]]
        head = f"三连 {reels[0]}！{mult} 倍赔付！"
    elif len(set(reels)) == 2:
        mult = 2
        head = "出现对子，2 倍赔付。"
    else:
        mult = 0
        head = "没有连成，未中奖。"
    net = amount * mult
    profit = net - amount
    player.earn(net)
    store.mark_dirty()
    lines = [
        "# 老虎机 · 结算",
        f"**转出**：{' '.join(reels)}",
        f"**{U.fmt_signed(profit)}**　{head}",
        "",
        _balance_line(player),
        "",
        "发送「开始老虎机（筹码数）」再来一局。",
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)))
