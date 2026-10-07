"""股市系统。

构成：
  * 市场上固定存在 10 家独立公司（全局共享，可被多位玩家同时投资）；
  * 每家独立公司每日随机决定是否"招标中"，招标中的公司会放出当日额度；
  * 玩家自己创建的公司也可以开放投资，由创建者自行决定单价与放出份额。

规则要点：
  * 单次投资最多买入 10% 股份，且不得超过当日放出额度与剩余可售份额；
  * 当一家公司 100% 股份都被玩家持有时，在有人卖出之前不再开放投资；
  * 买入价 = 份额 x 单价；卖出按 85% 折价由市场即时回购；
  * 独立公司的买入资金流向市场，玩家公司的买入资金流向公司创建者。
"""

from __future__ import annotations

import random

from . import constants as C
from . import render as R
from . import utils as U
from .models import Company, MarketCompany, Player
from .result import Result
from .store import GameStore

# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------
def player_companies(store: GameStore) -> list[tuple[Player, Company]]:
    """返回所有玩家创建的公司及其归属玩家。"""
    result: list[tuple[Player, Company]] = []
    for player in store.all_players():
        for company in player.companies:
            result.append((player, company))
    return result


def find_player_company(store: GameStore, name: str) -> tuple[Player, Company] | None:
    """按名称查找玩家公司。"""
    name = (name or "").strip()
    if not name:
        return None
    for owner, company in player_companies(store):
        if company.name == name:
            return owner, company
    for owner, company in player_companies(store):
        if name in company.name:
            return owner, company
    return None


def holdings_of(store: GameStore, uid: str) -> list[tuple[str, int, int]]:
    """查询某玩家持有的全部股份：(公司名, 份额, 当前单价)。"""
    uid = str(uid)
    result: list[tuple[str, int, int]] = []
    for company in store.market_companies.values():
        shares = int(company.holders.get(uid, 0))
        if shares > 0:
            result.append((company.name, shares, company.unit_price))
    for _owner, company in player_companies(store):
        shares = int(company.holders.get(uid, 0))
        if shares > 0:
            result.append((company.name, shares, max(1, company.unit_price)))
    return result


def holding_value(store: GameStore, uid: str) -> int:
    """按当前单价估算持股总市值（w）。"""
    return sum(shares * price for _name, shares, price in holdings_of(store, uid))


def holder_name(store: GameStore, uid: str) -> str:
    """把持股人的账号 ID 转换为可读的昵称（不对外暴露编号）。"""
    player = store.get(str(uid))
    if player is not None and player.name:
        return player.name
    return "某位玩家"


# --------------------------------------------------------------------------
# 每日刷新
# --------------------------------------------------------------------------
def refresh_daily(store: GameStore, ts: float | None = None, force: bool = False) -> bool:
    """每日招标刷新：随机开放公司、重置当日额度、股价波动。"""
    ts = ts if ts is not None else U.now()
    today = U.today_str(ts)
    if not force and store.stock_date == today:
        return False
    store.stock_date = today
    store.ensure_market_companies()

    companies = list(store.market_companies.values())
    for company in companies:
        company.prev_price = company.unit_price
        drift = 1 + random.uniform(-C.STOCK_FLUCTUATION, C.STOCK_FLUCTUATION)
        company.unit_price = max(5, int(round(company.unit_price * drift)))
        company.open = False
        company.quota = 0

    # 已经 100% 被玩家持有的公司不再开放
    available = [c for c in companies if c.remaining > 0]
    open_count = min(len(available), random.randint(C.MARKET_OPEN_MIN, C.MARKET_OPEN_MAX))
    for company in random.sample(available, open_count) if open_count else []:
        company.open = True
        company.quota = min(C.STOCK_DAILY_QUOTA, company.remaining)
    store.mark_dirty()
    return True


def settle_company_stocks(store: GameStore, player: Player) -> None:
    """玩家自己的公司：股价随资产变化（用于展示）。"""
    for company in player.companies:
        company.prev_price = company.stock_price
        drift = 1 + random.uniform(-C.STOCK_FLUCTUATION, C.STOCK_FLUCTUATION)
        company.stock_price = round(max(0.5, company.stock_price * drift), 2)


# --------------------------------------------------------------------------
# 买入 / 卖出
# --------------------------------------------------------------------------
def buy_shares(store: GameStore, player: Player, name: str, shares: float) -> Result:
    """投资买入股份。"""
    refresh_daily(store)
    name = (name or "").strip()
    if not name:
        return Result.fail("请指定要投资的公司，例如「投资长河影业5%」。")
    shares = int(shares)
    if shares <= 0:
        return Result.fail("请指定买入的份额，例如「投资长河影业5%」。")
    if shares > C.STOCK_MAX_PER_TRADE:
        return Result.fail(
            f"单次投资最多买入 {C.STOCK_MAX_PER_TRADE}% 的股份，请分次投入或减少份额。"
        )

    market = store.find_market_company(name)
    if market is not None:
        return _buy_market(store, player, market, shares)

    found = find_player_company(store, name)
    if found is not None:
        return _buy_player_company(store, player, found[0], found[1], shares)

    return Result.fail(f"股市中没有找到「{name}」，可发送「今日股市」查看当前行情。")


def _buy_market(store: GameStore, player: Player, company: MarketCompany, shares: int) -> Result:
    if not company.open:
        return Result.fail(f"{company.name} 当前没有开放投资，可留意它下次招标。")
    if company.remaining <= 0:
        return Result.fail(f"{company.name} 的股份已经被玩家全部持有，需等有人卖出后才会重新开放。")
    if company.quota <= 0:
        return Result.fail(f"{company.name} 今日放出的股份额度已经用完，明天再来看看。")
    if shares > company.quota:
        return Result.fail(
            f"{company.name} 今日剩余可投资额度为 {company.quota}%，无法一次买入 {shares}%。"
        )
    if shares > company.remaining:
        return Result.fail(f"{company.name} 目前只剩下 {company.remaining}% 的股份可被投资。")

    cost = shares * company.unit_price
    if not player.can_afford(cost):
        return Result.fail(
            f"资金不足：买入 {company.name} {shares}% 需要 {U.fmt_money(cost)}"
            f"（{U.fmt_money(company.unit_price)}/1%），你当前有 {U.fmt_money(player.money)}。"
        )
    player.pay(cost)
    company.holders[player.uid] = int(company.holders.get(player.uid, 0)) + shares
    company.quota -= shares
    store.mark_dirty()

    mine = company.holders[player.uid]
    lines = [
        "# 投资成功",
        f"**公司**：{company.name}（{company.ctype}）",
        f"**买入**：{shares}%　**单价**：{U.fmt_money(company.unit_price)}/1%",
        f"**花费**：{U.fmt_money(cost)}",
        "",
        f"**我的持股**：{mine}%　**当前市值**：{U.fmt_money(mine * company.unit_price)}",
        f"**今日剩余额度**：{company.quota}%　**未售出股份**：{company.remaining}%",
        f"**现金**：{U.fmt_money(player.cash)}",
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)))


def _buy_player_company(
    store: GameStore,
    player: Player,
    owner: Player,
    company: Company,
    shares: int,
) -> Result:
    if owner.uid == player.uid:
        return Result.fail(f"{company.name} 是你自己的公司，无需投资自己的股份。")
    if not company.open_invest:
        return Result.fail(f"{company.name} 目前没有开放投资。")
    if company.shares_free <= 0:
        return Result.fail(f"{company.name} 放出的 {company.shares_offered}% 股份已经售完。")
    if shares > company.shares_free:
        return Result.fail(f"{company.name} 目前只剩下 {company.shares_free}% 的股份可投资。")

    unit_price = max(1, company.unit_price)
    cost = shares * unit_price
    if not player.can_afford(cost):
        return Result.fail(
            f"资金不足：买入 {company.name} {shares}% 需要 {U.fmt_money(cost)}"
            f"（{U.fmt_money(unit_price)}/1%），你当前有 {U.fmt_money(player.money)}。"
        )
    player.pay(cost)
    owner.earn(cost)  # 投资款归公司创建者所有
    company.holders[player.uid] = int(company.holders.get(player.uid, 0)) + shares
    store.mark_dirty()

    mine = company.holders[player.uid]
    lines = [
        "# 投资成功",
        f"**公司**：{company.name}（{owner.name} 创建）",
        f"**买入**：{shares}%　**单价**：{U.fmt_money(unit_price)}/1%",
        f"**花费**：{U.fmt_money(cost)}（已支付给 {owner.name}）",
        "",
        f"**我的持股**：{mine}%　**当前市值**：{U.fmt_money(mine * unit_price)}",
        f"**该公司剩余可投资份额**：{company.shares_free}%",
        f"**现金**：{U.fmt_money(player.cash)}",
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)))


def sell_shares(store: GameStore, player: Player, name: str, shares: float) -> Result:
    """卖出持有的股份（市场按 85% 折价即时回购）。"""
    name = (name or "").strip()
    shares = int(shares)
    if not name or shares <= 0:
        return Result.fail("请指定要卖出的公司与份额，例如「卖出长河影业5%」。")

    market = store.find_market_company(name)
    if market is not None:
        return _sell_market(store, player, market, shares)

    found = find_player_company(store, name)
    if found is not None:
        return _sell_player_company(store, player, found[1], shares)

    return Result.fail(f"股市中没有找到「{name}」，可发送「持股」查看自己的持仓。")


def _sell_market(store: GameStore, player: Player, company: MarketCompany, shares: int) -> Result:
    mine = int(company.holders.get(player.uid, 0))
    if mine <= 0:
        return Result.fail(f"你没有持有 {company.name} 的股份。")
    if shares > mine:
        return Result.fail(f"你只持有 {company.name} {mine}% 的股份，无法卖出 {shares}%。")
    income = int(shares * company.unit_price * C.STOCK_SELL_RATE)
    company.holders[player.uid] = mine - shares
    if company.holders[player.uid] <= 0:
        company.holders.pop(player.uid, None)
    player.earn(income)
    store.mark_dirty()
    lines = [
        "# 卖出成功",
        f"**公司**：{company.name}",
        f"**卖出**：{shares}%　**成交价**：{U.fmt_money(company.unit_price)}/1%（市场折价 15%）",
        f"**到账**：{U.fmt_money(income)}",
        "",
        f"**剩余持股**：{max(0, mine - shares)}%　**现金**：{U.fmt_money(player.cash)}",
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)))


def _sell_player_company(
    store: GameStore,
    player: Player,
    company: Company,
    shares: int,
) -> Result:
    mine = int(company.holders.get(player.uid, 0))
    if mine <= 0:
        return Result.fail(f"你没有持有 {company.name} 的股份。")
    if shares > mine:
        return Result.fail(f"你只持有 {company.name} {mine}% 的股份，无法卖出 {shares}%。")
    unit_price = max(1, company.unit_price)
    income = int(shares * unit_price * C.STOCK_SELL_RATE)
    company.holders[player.uid] = mine - shares
    if company.holders[player.uid] <= 0:
        company.holders.pop(player.uid, None)
    player.earn(income)
    store.mark_dirty()
    return Result.success(
        text=(
            f"卖出成功\n"
            f"公司：{company.name}\n"
            f"卖出：{shares}%　到账：{U.fmt_money(income)}（市场折价 15%）\n"
            f"剩余持股：{max(0, mine - shares)}%"
        )
    )


# --------------------------------------------------------------------------
# 玩家公司开放投资
# --------------------------------------------------------------------------
def open_investment(
    store: GameStore,
    player: Player,
    name: str,
    unit_price: int,
    shares_offered: int,
) -> Result:
    """开放自家公司接受投资（仅创建者可操作）。"""
    found = find_player_company(store, name)
    if found is None:
        return Result.fail(f"你名下没有找到「{name}」，可发送「集团面板」查看公司列表。")
    owner, company = found
    if owner.uid != player.uid:
        return Result.fail(f"{company.name} 由 {owner.name} 创建，只有创建者可以调整开放设置。")
    low, high = C.STOCK_PLAYER_PRICE_RANGE
    if not (low <= unit_price <= high):
        return Result.fail(f"单价需要在 {U.fmt_money(low)} ~ {U.fmt_money(high)} 之间（每 1% 股份）。")
    if not (1 <= shares_offered <= 99):
        return Result.fail("放出份额需要在 1% ~ 99% 之间。")
    if shares_offered < company.shares_taken:
        return Result.fail(
            f"已经售出 {company.shares_taken}% 的股份，放出份额不能低于这个数。"
        )
    company.open_invest = True
    company.unit_price = unit_price
    company.shares_offered = shares_offered
    store.mark_dirty()
    lines = [
        "# 已开放投资",
        f"**公司**：{company.name}",
        f"**单价**：{U.fmt_money(unit_price)} / 1% 股份",
        f"**放出份额**：{shares_offered}%（已售出 {company.shares_taken}%）",
        "",
        f"全部售出可获得 {U.fmt_money(unit_price * (shares_offered - company.shares_taken))} 投资款。",
        "其他玩家可发送「投资" + company.name + "1%」买入。",
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)))


def close_investment(store: GameStore, player: Player, name: str) -> Result:
    """关闭自家公司的投资通道。"""
    found = find_player_company(store, name)
    if found is None:
        return Result.fail(f"你名下没有找到「{name}」。")
    owner, company = found
    if owner.uid != player.uid:
        return Result.fail(f"{company.name} 由 {owner.name} 创建，只有创建者可以调整开放设置。")
    if not company.open_invest:
        return Result.fail(f"{company.name} 当前并未开放投资。")
    company.open_invest = False
    store.mark_dirty()
    return Result.success(
        text=(
            f"已关闭 {company.name} 的投资通道。\n"
            f"已售出的 {company.shares_taken}% 股份仍归投资人所有，他们可以随时卖出。"
        )
    )


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
def render_market_panel(store: GameStore, player: Player) -> R.Card:
    """股市行情面板。"""
    refresh_daily(store)
    lines = ["# 今日股市", ""]
    lines.append("独立公司（招标中的公司可投资，其余暂不接受投资）")
    lines.append("")
    for company in store.market_companies.values():
        state = company.state_text
        state_text = f"　[{state}]" if state else ""
        lines.append(f"**{company.name}**（{company.ctype}）{state_text}")
        detail = (
            f"　单价 {U.fmt_money(company.unit_price)}/1%　{company.change_text}"
            f"　已发行 {company.issued}%"
        )
        if state:
            detail += f"　今日额度 {company.quota}%"
        lines.append(detail)
        mine = int(company.holders.get(player.uid, 0))
        if mine:
            lines.append(
                f"　我的持股 {mine}%　市值 {U.fmt_money(mine * company.unit_price)}"
            )
    lines.append("")

    others = [
        (owner, company)
        for owner, company in player_companies(store)
        if company.open_invest and owner.uid != player.uid
    ]
    if others:
        lines.append("其他玩家的公司（开放投资中）")
        lines.append("")
        for owner, company in others:
            lines.append(
                f"**{company.name}**（{owner.name} 创建）　[招标中]"
                f"　单价 {U.fmt_money(max(1, company.unit_price))}/1%"
                f"　剩余 {company.shares_free}%"
            )
            mine = int(company.holders.get(player.uid, 0))
            if mine:
                lines.append(f"　我的持股 {mine}%")
        lines.append("")

    own_open = [c for c in player.companies if c.open_invest]
    if own_open:
        lines.append("我的公司（开放投资中）")
        lines.append("")
        for company in own_open:
            lines.append(
                f"**{company.name}**　单价 {U.fmt_money(max(1, company.unit_price))}/1%"
                f"　放出 {company.shares_offered}%　已售出 {company.shares_taken}%"
            )
            if company.holders:
                detail = "　".join(
                    f"{holder_name(store, holder)} 持有 {shares}%"
                    for holder, shares in company.holders.items()
                )
                lines.append(f"　{detail}")
        lines.append("")

    lines.append("发送「投资公司名5%」买入、「卖出公司名5%」卖出、「持股」查看持仓。")
    return R.Card(markdown="\n".join(lines))


def render_holdings(store: GameStore, player: Player) -> R.Card:
    """我的持股面板。"""
    holdings = holdings_of(store, player.uid)
    lines = ["# 我的持股", ""]
    if not holdings:
        lines.append("当前没有持有任何股份。")
        lines.append("")
        lines.append("可在「今日股市」中寻找招标中的公司进行投资。")
        return R.Card(markdown="\n".join(lines))
    total = 0
    for name, shares, price in holdings:
        value = shares * price
        total += value
        lines.append(
            f"**{name}**　持股 {shares}%　单价 {U.fmt_money(price)}/1%"
            f"　市值 {U.fmt_money(value)}"
        )
    lines.append("")
    lines.append(f"**持股总市值**：{U.fmt_money(total)}")
    lines.append("发送「卖出公司名份额%」可变现（市场按 85% 折价回购）。")
    return R.Card(markdown="\n".join(lines))
