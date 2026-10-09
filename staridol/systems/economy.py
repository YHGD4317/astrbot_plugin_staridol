"""经济系统：银行、商城道具、下属公司与分红。

所有面向玩家的文案均不使用 emoji。
"""

from __future__ import annotations

import random

from .. import constants as C
from .. import render as R
from .. import utils as U
from ..models import Company, Player
from ..result import Result
from ..store import GameStore
from . import artist as artist_sys
from . import quest as quest_sys

# --------------------------------------------------------------------------
# 银行
# --------------------------------------------------------------------------
def credit_tier(level: int) -> C.CreditTier:
    for tier in C.CREDIT_TIERS:
        if tier.level == level:
            return tier
    return C.CREDIT_TIERS[0]


def next_credit_tier(level: int) -> C.CreditTier | None:
    for tier in C.CREDIT_TIERS:
        if tier.level == level + 1:
            return tier
    return None


def credit_score(player: Player) -> int:
    """信用评估值：存款全额 + 总资产的一半。"""
    return int(player.deposit + max(0, player.total_asset) * 0.5)


def deposit_money(store: GameStore, player: Player, amount: int) -> Result:
    amount = int(amount)
    if amount <= 0:
        return Result.fail("存款金额需要大于 0，例如「存款1000」。")
    if player.cash < amount:
        return Result.fail(
            f"现金不足：当前现金 {U.fmt_money(player.cash)}，无法存入 {U.fmt_money(amount)}。"
        )
    player.cash -= amount
    player.deposit += amount
    store.mark_dirty()
    return Result.success(
        text=(
            f"已存入 {U.fmt_money(amount)}\n"
            f"**现金**：{U.fmt_money(player.cash)}　**存款**：{U.fmt_money(player.deposit)}"
        )
    )


def withdraw_money(store: GameStore, player: Player, amount: int) -> Result:
    amount = int(amount)
    if amount <= 0:
        return Result.fail("取款金额需要大于 0，例如「取款1000」。")
    if player.deposit < amount:
        return Result.fail(
            f"存款不足：当前存款 {U.fmt_money(player.deposit)}，无法取出 {U.fmt_money(amount)}。"
        )
    player.deposit -= amount
    player.cash += amount
    store.mark_dirty()
    return Result.success(
        text=(
            f"已取出 {U.fmt_money(amount)}\n"
            f"**现金**：{U.fmt_money(player.cash)}　**存款**：{U.fmt_money(player.deposit)}"
        )
    )


def claim_interest(store: GameStore, player: Player, ts: float | None = None) -> Result:
    ts = ts if ts is not None else U.now()
    today = U.today_str(ts)
    if player.last_interest_date == today:
        return Result.fail("今天的利息已经领取过了，明天再来吧。")
    if player.deposit <= 0:
        return Result.fail("当前没有存款，先「存款金额」再来领取利息。")
    info = credit_tier(player.credit_level)
    interest = max(1, int(player.deposit * info.deposit_rate))
    player.deposit += interest
    player.last_interest_date = today
    store.mark_dirty()
    return Result.success(
        text=(
            f"领取利息 {U.fmt_money(interest)}"
            f"（{info.level} 级 · 日息 {info.deposit_rate * 100:.2f}%）\n"
            f"**存款**：{U.fmt_money(player.deposit)}"
        )
    )


def upgrade_credit(store: GameStore, player: Player) -> Result:
    tier = next_credit_tier(player.credit_level)
    if tier is None:
        return Result.fail("你的信用等级已经达到最高（7 级 · 财团伙伴）。")
    score = credit_score(player)
    if score < tier.threshold:
        need = tier.threshold - score
        return Result.fail(
            f"信用评估不足：当前 {U.fmt_money(score)}，升级到 {tier.level} 级 {tier.name} "
            f"还需要 {U.fmt_money(need)}（存款全额 + 总资产一半）。"
        )
    player.credit_level = tier.level
    store.mark_dirty()
    return Result.success(
        text=(
            f"信用等级提升：**{tier.level} 级 · {tier.name}**\n"
            f"存款日息 {tier.deposit_rate * 100:.2f}%　贷款日息 {tier.loan_rate * 100:.2f}%　"
            f"贷款额度 {U.fmt_money(tier.loan_limit)}"
        )
    )


def take_loan(store: GameStore, player: Player, amount: int) -> Result:
    amount = int(amount)
    if amount <= 0:
        return Result.fail("贷款金额需要大于 0，例如「贷款500」。")
    info = credit_tier(player.credit_level)
    remain = max(0, info.loan_limit - player.loan)
    if remain <= 0:
        return Result.fail(
            f"贷款额度已用完（{info.level} 级上限 {U.fmt_money(info.loan_limit)}）。"
            "可发送「升级信用」提升额度。"
        )
    if amount > remain:
        return Result.fail(f"超出贷款额度，当前最多可贷 {U.fmt_money(remain)}。")
    player.loan += amount
    player.cash += amount
    store.mark_dirty()
    return Result.success(
        text=(
            f"贷款到账 {U.fmt_money(amount)}（日息 {info.loan_rate * 100:.2f}%）\n"
            f"**贷款总额**：{U.fmt_money(player.loan)}　**现金**：{U.fmt_money(player.cash)}"
        )
    )


def repay_loan(store: GameStore, player: Player, amount: int) -> Result:
    amount = int(amount)
    if amount <= 0:
        return Result.fail("还款金额需要大于 0，例如「还款500」。")
    if player.loan <= 0:
        return Result.fail("当前没有贷款需要偿还。")
    real = min(amount, player.loan)
    if player.money < real:
        return Result.fail(
            f"资金不足：需要 {U.fmt_money(real)}，你当前有 {U.fmt_money(player.money)}。"
        )
    player.pay(real)
    player.loan -= real
    store.mark_dirty()
    return Result.success(
        text=(
            f"已还款 {U.fmt_money(real)}\n"
            f"**剩余贷款**：{U.fmt_money(player.loan)}　**现金**：{U.fmt_money(player.cash)}"
        )
    )


def settle_loan_interest(store: GameStore, player: Player) -> int:
    """每日结算贷款利息，返回扣除金额。"""
    if player.loan <= 0:
        return 0
    info = credit_tier(player.credit_level)
    interest = int(player.loan * info.loan_rate)
    if interest <= 0:
        return 0
    paid = player.force_pay(interest)
    unpaid = interest - paid
    if unpaid > 0:
        player.loan += unpaid  # 未还清部分计入本金
    store.mark_dirty()
    return paid


# --------------------------------------------------------------------------
# 商城
# --------------------------------------------------------------------------
def refresh_shop(store: GameStore, player: Player, ts: float | None = None, force: bool = False) -> bool:
    """每日刷新商城（价格浮动、重新上架）。"""
    ts = ts if ts is not None else U.now()
    today = U.today_str(ts)
    if not force and player.shop_date == today:
        return False
    player.shop_date = today
    weights = [(name, tpl.weight) for name, tpl in C.ITEMS.items()]
    picked: list[str] = []
    guard = 0
    while len(picked) < min(C.SHOP_ITEM_COUNT, len(C.ITEMS)) and guard < 200:
        guard += 1
        name = U.weighted_choice(weights)
        if name not in picked:
            picked.append(name)
    stock: dict[str, dict] = {}
    for name in picked:
        tpl = C.ITEMS[name]
        factor = 1 + random.uniform(-C.SHOP_PRICE_FLUCTUATION, C.SHOP_PRICE_FLUCTUATION)
        price = max(1, int(round(tpl.price * factor)))
        stock[name] = {"price": price, "base": tpl.price}
    player.shop_stock = stock
    store.mark_dirty()
    return True


def shop_price(player: Player, name: str) -> int:
    """读取商城当前售价（不在架时返回基准价）。"""
    info = player.shop_stock.get(name)
    if info:
        return int(info.get("price", C.ITEMS[name].price))
    return C.ITEMS[name].price


def roll_discount(player: Player) -> tuple[int, U.CheckResult]:
    """购买前的口才检定：通过则按二次检定决定折扣（1~9 折）。"""
    check = U.roll_check("口才", player.eloquence, player.buff_value("check"))
    if not check.success:
        return 10, check
    second = random.randint(1, 100)
    for cap, discount in C.DISCOUNT_TIERS:
        if second <= cap:
            return discount, check
    return 10, check


def buy_item(store: GameStore, player: Player, name: str, count: int = 1) -> Result:
    """购买道具（含折扣检定）。"""
    name = (name or "").strip()
    tpl = C.ITEMS.get(name)
    if tpl is None:
        return Result.fail(f"商城里没有「{name}」这件道具，可发送「打开商城」查看。")
    count = max(1, min(99, int(count)))
    refresh_shop(store, player)
    if name not in player.shop_stock:
        return Result.fail(f"今日商城没有上架「{name}」，明天再来看看。")

    unit = shop_price(player, name)
    discount, check = roll_discount(player)
    total = max(1, unit * count * discount // 10)
    if not player.can_afford(total):
        return Result.fail(
            f"资金不足：{name}×{count} 需要 {U.fmt_money(total)}"
            f"（原价 {U.fmt_money(unit * count)}，{discount} 折），"
            f"你当前有 {U.fmt_money(player.money)}。"
        )
    player.pay(total)
    player.items[name] = player.items.get(name, 0) + count
    store.mark_dirty()

    lines = [
        "# 购买成功",
        f"**{name}** × {count}",
        f"**原价**：{U.fmt_money(unit * count)}　**成交价**：{U.fmt_money(total)}",
    ]
    if discount < 10:
        lines.append(
            f"口才检定通过（骰点 {check.roll}/{check.upper}），二次检定获得 **{discount} 折** 优惠。"
        )
    else:
        lines.append(f"口才检定未通过（骰点 {check.roll}/{check.upper}），按原价成交。")
    lines.append("")
    lines.append(f"**现金**：{U.fmt_money(player.cash)}　可发送「使用{name}」立即使用。")
    return Result.success(card=R.Card(markdown="\n".join(lines)))


def open_box(store: GameStore, player: Player) -> tuple[str, str]:
    """开盲盒，返回 (摘要, 详细说明)。"""
    if random.random() < C.BOX_MONEY_CHANCE:
        money = random.randint(*C.BOX_MONEY_RANGE)
        player.earn(money)
        store.mark_dirty()
        return f"现金 {U.fmt_money(money)}", f"盲盒里是一叠现金：{U.fmt_money(money)}"
    name = U.weighted_choice(C.BOX_LOOT_TABLE)
    player.items[name] = player.items.get(name, 0) + 1
    store.mark_dirty()
    return f"{name}×1", f"盲盒开出了 **{name}**"


def use_item(store: GameStore, player: Player, name: str, target: str = "", count: int = 1) -> Result:
    """使用背包中的道具（支持批量使用，效果直接叠加）。

    ``count`` 为使用数量，例如「使用2个幸运符」。数量以背包实际持有为上限，
    同种增益/属性/恢复效果按数量线性叠加。
    """
    name = (name or "").strip()
    tpl = C.ITEMS.get(name)
    if tpl is None:
        return Result.fail(f"没有「{name}」这件道具。")
    count = max(1, int(count))
    have = int(player.items.get(name, 0))
    if have <= 0:
        return Result.fail(f"背包里没有「{name}」了，可发送「打开商城」购买。")
    count = min(count, have)

    ts = U.now()

    # ---- 盲盒 ----
    if tpl.kind == "box":
        player.items[name] -= count
        lines = [f"# {name}", f"**批量开启**：×{count}", ""]
        for _ in range(count):
            summary, detail = open_box(store, player)
            lines.append(f"- {detail}（获得：{summary}）")
        lines.append("")
        lines.append(f"**现金**：{U.fmt_money(player.cash)}")
        store.mark_dirty()
        return Result.success(card=R.Card(markdown="\n".join(lines)))

    # ---- 属性道具 ----
    if tpl.kind == "attr":
        if tpl.target == "artist":
            artist = player.find_artist(target) if target else None
            if artist is None:
                return Result.fail(f"「{name}」需要指定艺人，例如「使用{name}给（艺人名）」。")
            before = artist.get(tpl.attr)
            for _ in range(count):
                artist.add(tpl.attr, tpl.value)
            player.items[name] -= count
            attr_cn = C.ARTIST_ATTR_CN.get(tpl.attr, "属性")
            acted = artist.get(tpl.attr) - before
            store.mark_dirty()
            return Result.success(
                text=(
                    f"{artist.name} 的 **{attr_cn} +{acted}**（使用 {count} 个，当前 {artist.get(tpl.attr)}）\n"
                    f"剩余「{name}」×{player.items.get(name, 0)}"
                )
            )
        before = player.attr(tpl.attr)
        for _ in range(count):
            player.add_attr(tpl.attr, tpl.value)
        player.items[name] -= count
        attr_cn = C.PLAYER_ATTR_CN.get(tpl.attr, "属性")
        acted = player.attr(tpl.attr) - before
        store.mark_dirty()
        return Result.success(
            text=(
                f"你的 **{attr_cn} +{acted}**（使用 {count} 个，当前 {player.attr(tpl.attr)}）\n"
                f"剩余「{name}」×{player.items.get(name, 0)}"
            )
        )

    # ---- 恢复道具 ----
    if tpl.kind == "restore":
        if tpl.restore == "stamina":
            artist = player.find_artist(target) if target else None
            if artist is None:
                return Result.fail(f"「{name}」需要指定艺人，例如「使用{name}给（艺人名）」。")
            artist_sys.regen_stamina(artist, ts)
            before = artist.stamina
            artist.stamina = min(100, artist.stamina + tpl.value * count)
            player.items[name] -= count
            store.mark_dirty()
            return Result.success(
                text=(
                    f"{artist.name} 体力 {before} → **{artist.stamina}/100**（使用 {count} 个）\n"
                    f"剩余「{name}」×{player.items.get(name, 0)}"
                )
            )
        if tpl.restore == "show":
            player.show_left += tpl.value * count
            player.items[name] -= count
            store.mark_dirty()
            return Result.success(
                text=(
                    f"今日秀场次数 +{tpl.value * count}（使用 {count} 个，当前 {player.show_left} 次）\n"
                    f"剩余「{name}」×{player.items.get(name, 0)}"
                )
            )
        if tpl.restore == "market":
            player.market_left += tpl.value * count
            player.items[name] -= count
            store.mark_dirty()
            return Result.success(
                text=(
                    f"今日商业活动次数 +{tpl.value * count}（使用 {count} 个，当前 {player.market_left} 次）\n"
                    f"剩余「{name}」×{player.items.get(name, 0)}"
                )
            )
        if tpl.restore == "quest":
            added = quest_sys.add_quests(store, player, tpl.value * count)
            player.items[name] -= count
            store.mark_dirty()
            return Result.success(
                text=(
                    f"已补发 {added} 条今日事务（使用 {count} 个），发送「今日行程」继续处理。\n"
                    f"剩余「{name}」×{player.items.get(name, 0)}"
                )
            )
        return Result.fail(f"「{name}」暂时无法使用。")

    # ---- 临时增益（效果叠加：同种增益多次使用，数值按次数相乘、时长顺延）----
    if tpl.kind == "temp":
        consume = count
        expire = ts + max(60, tpl.duration)
        total_count = consume
        if tpl.buff in player.buffs:
            old_expire, old_count = player._buff_entry(player.buffs[tpl.buff])
            if old_expire > ts:
                expire = max(expire, old_expire)
                total_count = old_count + consume
        player.buffs[tpl.buff] = {"expire": expire, "count": total_count}
        player.items[name] -= consume
        store.mark_dirty()
        return Result.success(
            text=(
                f"已使用「{name}」×{consume}：{tpl.desc}\n"
                f"增益效果已叠加（共 {total_count} 次），**有效期至**：{U.fmt_clock(expire)}\n"
                f"剩余「{name}」×{player.items.get(name, 0)}"
            )
        )

    return Result.fail(f"「{name}」暂时无法使用。")


# --------------------------------------------------------------------------
# 公司与分红
# --------------------------------------------------------------------------
def create_company(store: GameStore, player: Player, ctype: str) -> Result:
    """创建下属公司。"""
    info = C.COMPANY_TYPES.get(ctype)
    if info is None:
        return Result.fail("可创建的公司：" + "、".join(C.COMPANY_TYPES))
    base_name = player.company_name.replace("集团", "") or "星海"
    name = f"{base_name}{ctype}"
    if player.find_company(name):
        return Result.fail(f"你已经有一家「{name}」了。")
    if info.cost and not player.can_afford(info.cost):
        return Result.fail(
            f"资金不足：创建{ctype}需要 {U.fmt_money(info.cost)}，"
            f"你当前有 {U.fmt_money(player.money)}。"
        )
    if info.cost:
        player.pay(info.cost)
    company = Company(
        cid=U.new_id("c"),
        name=name,
        ctype=ctype,
        asset=info.cost,
        stock_price=round(random.uniform(8, 15), 2),
        owner=player.uid,
    )
    company.prev_price = company.stock_price
    player.companies.append(company)
    player.push_log(f"创建{name}")
    store.mark_dirty()
    lines = [
        "# 公司创建成功",
        f"**{name}**（{ctype}）",
        f"**投入**：{U.fmt_money(info.cost)}",
        f"**预计日收益**：{U.fmt_money(info.income[0])} ~ {U.fmt_money(info.income[1])}",
        "",
        "每日会自动产生收益，发送「收取分红」可以把利润提现。",
        f"如需接受其他玩家投资，可发送「开放投资{name}，单价100w，放出20%」。",
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)))


def collect_dividend(store: GameStore, player: Player) -> Result:
    """提取公司累积利润。"""
    total = 0
    details: list[str] = []
    for company in player.companies:
        info = C.COMPANY_TYPES.get(company.ctype)
        base = info.cost if info else 0
        profit = max(0, company.asset - base)
        if profit <= 0:
            continue
        company.asset = base
        total += profit
        details.append(f"{company.name} {U.fmt_money(profit)}")
    if total <= 0:
        return Result.fail("当前没有可提取的分红，等公司产生收益后再来吧。")
    player.earn(total)
    store.mark_dirty()
    return Result.success(
        text=(
            f"已提取分红 **{U.fmt_money(total)}**\n"
            + "\n".join(f"- {d}" for d in details)
            + f"\n**现金**：{U.fmt_money(player.cash)}"
        )
    )


def settle_companies(store: GameStore, player: Player, ts: float | None = None) -> int:
    """每日公司收益与股价波动，返回当日总收益。"""
    ts = ts if ts is not None else U.now()
    today = U.today_str(ts)
    if player.last_company_date == today:
        return 0
    player.last_company_date = today
    total = 0
    for company in player.companies:
        info = C.COMPANY_TYPES.get(company.ctype)
        if info is None:
            continue
        income = random.randint(*info.income)
        company.asset += income
        company.total_income += income
        total += income
        company.prev_price = company.stock_price
        drift = 1 + random.uniform(-C.STOCK_FLUCTUATION, C.STOCK_FLUCTUATION)
        company.stock_price = round(max(0.5, company.stock_price * drift), 2)
    if total:
        store.mark_dirty()
    return total


def total_dividend_pending(player: Player) -> int:
    """统计待提取的分红总额。"""
    total = 0
    for company in player.companies:
        info = C.COMPANY_TYPES.get(company.ctype)
        base = info.cost if info else 0
        total += max(0, company.asset - base)
    return total
