"""项目系统：筹划立项、启动、艺人投放、结算收益。

评分规则（与策划案一致，不设上限）：

* **初始评分** = ``(玩家主属性 + 财商) / 200``，保留一位小数。
* **制作加成** = ``min(0.5, 投资额 / 10000)``。
* **艺人加分** = ``项目判定四项属性之和 / 4 / 100``，保留一位小数，单人上限由属性决定。
* **收益**：评分 < 3 亏损全部投资；3 ≤ 评分 < 5 返还一半；评分 ≥ 5 时
  ``收益 = 投资 × (1 + (评分 - 5) × 10%)``。

商业活动（外部项目）与今日秀场一致，每日可刷新 3 次，刷新后旧列表作废。
所有面向玩家的文案均不使用 emoji。
"""

from __future__ import annotations

import random

from .. import constants as C
from .. import render as R
from .. import utils as U
from ..models import Artist, MarketProject, Player, Project
from ..result import Result
from ..store import GameStore
from . import artist as artist_sys

# --------------------------------------------------------------------------
# 评分与收益计算
# --------------------------------------------------------------------------
def calc_base_score(player: Player, ptype: str) -> float:
    """计算自筹划项目的初始基础评分。"""
    info = C.PROJECT_TYPES.get(ptype)
    main_attr = info.main_attr if info else "decision"
    value = (player.attr(main_attr) + player.finance) / 200
    return U.round_score(max(C.MIN_SCORE, value))


def calc_production_bonus(invest: int) -> float:
    """制作规格加成：投资越大，制作水准越高（最多 +0.5 分）。"""
    bonus = max(0.0, min(C.PRODUCTION_BONUS_CAP, invest / C.PRODUCTION_BONUS_SCALE))
    return U.round_score(bonus)


def calc_artist_score(artist: Artist, ptype: str) -> float:
    """计算单个艺人给项目带来的评分增量。"""
    info = C.PROJECT_TYPES.get(ptype)
    keys = info.artist_attrs if info else ("acting",)
    total = sum(artist.get(key) for key in keys)
    score = total / max(1, len(keys)) / 100
    return U.round_score(max(0.0, score))


def calc_revenue(invest: int, score: float) -> int:
    """按最终评分计算项目收益（评分不设上限）。"""
    if score < C.SCORE_LOSS_ALL:
        return 0
    if score < C.SCORE_LOSS_HALF:
        return int(invest * 0.5)
    return int(invest * (1 + (score - C.SCORE_LOSS_HALF) * C.SCORE_PROFIT_STEP))


def score_breakdown(player: Player, ptype: str, invest: int, project: Project | None = None) -> str:
    """生成评分构成说明，便于玩家理解当前项目能拿多少分。"""
    base = calc_base_score(player, ptype)
    prod = calc_production_bonus(invest)
    artist_total = 0.0
    if project is not None:
        artist_total = sum(project.artist_scores.values())
    total = U.round_score(max(C.MIN_SCORE, base + prod + artist_total))
    return (
        f"基础 {U.fmt_score(base)} + 制作 {U.fmt_score(prod)}"
        f" + 艺人 {U.fmt_score(artist_total)} = **{U.fmt_score(total)}**"
    )


def _recompute_score(project: Project) -> None:
    """按当前总投资额与艺人加成重算项目评分。

    评分 = base_score（玩家主属性基础分）+ 制作加成（由总投资额决定）
           + Σ(参与艺人加分)。投资额变化（追加投资 / 他人加入投资）会自动抬升评分。
    """
    production = calc_production_bonus(project.invest)
    artist_total = sum(project.artist_scores.values())
    project.score = U.round_score(
        max(C.MIN_SCORE, project.base_score + production + artist_total)
    )


# --------------------------------------------------------------------------
# 项目命名（重名自动追加序号，形成系列项目）
# --------------------------------------------------------------------------
def unique_project_name(player: Player, name: str) -> str:
    """若项目名与已有项目重复，则在末尾追加序号，例如 河中仙2、河中仙3。"""
    name = (name or "").strip()[:14]
    existing = {p.name for p in player.projects}
    if name not in existing:
        return name
    index = 2
    while f"{name}{index}" in existing:
        index += 1
    return f"{name}{index}"


# --------------------------------------------------------------------------
# 商业活动（外部项目）
# --------------------------------------------------------------------------
def ensure_daily_market(store: GameStore, player: Player, ts: float | None = None) -> None:
    """跨天时重置商业活动次数与列表。"""
    ts = ts if ts is not None else U.now()
    today = U.today_str(ts)
    if player.market_date != today:
        player.market_date = today
        # 每日商业活动次数 = 基础次数 + 已达资产档位数
        player.market_left = store.market_refresh + player.asset_tier
        player.market = []
        store.mark_dirty()


def refresh_market(
    store: GameStore,
    player: Player,
    ts: float | None = None,
    *,
    consume: bool = True,
    force: bool = False,
) -> Result:
    """刷新商业活动列表（默认消耗一次机会）。

    刷新后旧列表立即作废，未投资的项目不再出现。
    """
    ts = ts if ts is not None else U.now()
    if force:
        player.market_date = U.today_str(ts)
        player.market_left = store.market_refresh + player.asset_tier
    ensure_daily_market(store, player, ts)
    if consume:
        if player.market_left <= 0:
            return Result.fail(
                "今日商业活动次数已用完（0 次）。\n"
                "可在商城购买「招商手册」恢复次数，或明天再来。"
            )
        player.market_left -= 1

    player.market = []
    types = list(C.PROJECT_TYPES.values())
    weights = [(t.name, t.weight) for t in types]
    used_names = {p.name for p in player.projects}
    for _ in range(C.MARKET_PROJECT_COUNT):
        type_name = U.weighted_choice(weights)
        info = C.PROJECT_TYPES[type_name]
        score = round(random.uniform(*C.MARKET_SCORE_RANGE), 1)
        invest = random.randint(info.invest[0], info.invest[1])
        duration = random.randint(*info.duration)
        raw_name = _random_project_name()
        name = raw_name
        index = 2
        while name in used_names:
            name = f"{raw_name}{index}"
            index += 1
        used_names.add(name)
        player.market.append(
            MarketProject(
                mid=U.new_id("m"),
                name=name,
                ptype=type_name,
                duration=duration,
                score=score,
                invest=invest,
            )
        )
    store.mark_dirty()
    return Result.success()


_PROJECT_NAME_PARTS_A = [
    "长安", "云海", "暗涌", "浮生", "山海", "夜航", "春风", "星河", "无声", "雾都",
    "千秋", "赤壁", "孤岛", "黎明", "镜中", "荒原", "旧梦", "青鸟", "白夜", "潮汐",
]
_PROJECT_NAME_PARTS_B = [
    "纪事", "列车", "往事", "来信", "回声", "之约", "谜案", "手记", "长歌", "旅人",
    "日记", "星图", "笔记", "序曲", "信号", "告白", "现场", "计划",
]


def _random_project_name() -> str:
    return f"{random.choice(_PROJECT_NAME_PARTS_A)}{random.choice(_PROJECT_NAME_PARTS_B)}"


# --------------------------------------------------------------------------
# 立项 / 启动
# --------------------------------------------------------------------------
def plan_project(
    store: GameStore,
    player: Player,
    invest: int,
    ptype: str,
    name: str,
    duration: int | None = None,
) -> Result:
    """自行筹划并立项。时长不再由玩家指定：立项后随机 1~8 小时。

    ``duration`` 参数保留仅为兼容旧调用方，实际总是忽略，改为随机生成。
    """
    if ptype not in C.PROJECT_TYPES:
        return Result.fail(
            "没有「" + str(ptype) + "」这种项目类型。可选：" + "、".join(C.PROJECT_TYPES)
        )
    name = (name or "").strip().strip("《》")
    if not name:
        return Result.fail("请给项目起个名字，例如「用1000w筹划综艺（项目名）」。")
    info = C.PROJECT_TYPES[ptype]
    if invest < C.MIN_INVEST:
        return Result.fail(f"投资额过低，{ptype}最少需要 {U.fmt_money(C.MIN_INVEST)}。")
    if not player.can_afford(invest):
        return Result.fail(
            f"资金不足：筹划《{name}》需要 {U.fmt_money(invest)}，"
            f"你当前有 {U.fmt_money(player.money)}。"
        )
    # 删除玩家指定时长：立项后随机 1~8 小时
    duration = random.randint(1, 8)

    final_name = unique_project_name(player, name)
    renamed = final_name != name

    player.pay(invest)
    base_score = calc_base_score(player, ptype)
    production = calc_production_bonus(invest)
    total = U.round_score(max(C.MIN_SCORE, base_score + production))
    project = Project(
        pid=U.new_id("p"),
        name=final_name,
        ptype=ptype,
        invest=invest,
        base_score=base_score,
        score=total,
        duration=duration,
        owner=player.uid,
    )
    # 业主的初始投资计入其份额；评分统一由 base_score + 制作加成 + 艺人加成重算
    project.investor_funds[player.uid] = int(project.investor_funds.get(player.uid, 0)) + invest
    _recompute_score(project)
    player.projects.append(project)
    player.push_log(f"立项《{final_name}》（{ptype}，投资 {U.fmt_money(invest)}）")
    store.mark_dirty()
    card = R.render_project_created(
        project, breakdown=score_breakdown(player, ptype, invest, project)
    )
    if renamed:
        card.markdown = (
            f"检测到已有同名项目，本次立项自动命名为《{final_name}》。\n\n" + card.markdown
        )
    return Result.success(card=card)


def start_project(store: GameStore, player: Player, name: str = "") -> Result:
    """启动一个待启动的项目。"""
    project: Project | None = None
    if name:
        project = player.find_project(name)
        if project is None:
            return Result.fail(f"没有找到名为《{name}》的项目。")
    else:
        pending = [p for p in player.projects if p.is_pending]
        if not pending:
            return Result.fail("当前没有待启动的项目。可发送「用1000w筹划综艺（项目名）」立项。")
        project = pending[-1]
    if project.status == C.PROJECT_RUNNING:
        return Result.fail(
            f"《{project.name}》已经在进行中了，预计 {U.fmt_clock(project.end_at)} 结束。"
        )
    if project.status != C.PROJECT_PENDING:
        return Result.fail(f"《{project.name}》当前状态为 {project.status}，无法启动。")

    ts = U.now()
    project.status = C.PROJECT_RUNNING
    project.start_at = ts
    project.end_at = ts + project.duration * 3600
    player.push_log(f"《{project.name}》开机")
    store.mark_dirty()
    return Result.success(card=R.render_project_started(project))


# --------------------------------------------------------------------------
# 艺人投放
# --------------------------------------------------------------------------
def join_project(store: GameStore, player: Player, names: list[str], project_name: str) -> Result:
    """让一位或多位艺人参加项目。"""
    project_name = (project_name or "").strip().strip("《》")
    project = player.find_project(project_name)
    if project is None:
        return Result.fail(f"没有找到名为《{project_name}》的项目，可发送「项目面板」查看。")
    if project.status == C.PROJECT_DONE:
        return Result.fail(f"《{project.name}》已经结束拍摄了。")
    if project.status == C.PROJECT_PENDING:
        return Result.fail(f"《{project.name}》还没有开机，请先发送「{project.name}项目开始」。")

    ts = U.now()
    added: list[tuple[Artist, int, float]] = []
    skipped: list[str] = []
    if len(project.artists) >= C.MAX_ARTISTS_PER_PROJECT:
        return Result.fail(
            f"《{project.name}》的参演艺人已达上限（{C.MAX_ARTISTS_PER_PROJECT} 位）。"
        )
    for name in names:
        if len(project.artists) >= C.MAX_ARTISTS_PER_PROJECT:
            skipped.append(f"{name}（已达参演上限 {C.MAX_ARTISTS_PER_PROJECT} 人）")
            continue
        artist = player.find_artist(name)
        if artist is None:
            skipped.append(f"{name}（不在名册中）")
            continue
        if artist.aid in project.artists:
            skipped.append(f"{artist.name}（已在该项目中）")
            continue
        artist_sys.regen_stamina(artist, ts)
        if artist.is_busy(ts):
            skipped.append(f"{artist.name}（{artist.status_line(ts)}）")
            continue
        if artist.stamina < C.PROJECT_STAMINA_MIN:
            skipped.append(f"{artist.name}（体力不足 {artist.stamina}/100）")
            continue
        salary = artist.salary
        if not player.can_afford(salary):
            skipped.append(f"{artist.name}（资金不足以支付薪资 {U.fmt_money(salary)}）")
            continue

        player.pay(salary)
        artist.salary_paid += salary
        gain = calc_artist_score(artist, project.ptype)
        project.artists.append(artist.aid)
        project.artist_scores[artist.aid] = gain
        project.score = U.round_score(max(C.MIN_SCORE, project.score + gain))
        project.salary_paid += salary
        artist.status = C.STATUS_PROJECT
        artist.project_id = project.pid
        artist.status_text = f"{R.project_activity_verb(project.ptype)}《{project.name}》中"
        artist.busy_until = project.end_at
        artist.stamina_at = ts
        artist.project_stamina_at = ts
        added.append((artist, salary, gain))

    if not added:
        detail = "；".join(skipped[:6]) if skipped else "没有可投放的艺人"
        return Result.fail(f"没有艺人加入《{project.name}》。\n原因：{detail}")

    store.mark_dirty()
    lines = [
        "# 艺人已加入项目",
        f"**《{project.name}》**（{project.ptype}）",
        "",
    ]
    salary_text = "、".join(f"{a.name} {U.fmt_money(s)}" for a, s, _ in added)
    total_salary = sum(s for _, s, _ in added)
    lines.append(f"**加入艺人**：{'、'.join(a.name for a, _, _ in added)}")
    lines.append(f"**发放薪资**：共 {U.fmt_money(total_salary)}（{salary_text}）")
    for artist, _, _gain in added:
        lines.append(f"- {artist.name}：评分加成 {R.artist_score_calc(artist, project.ptype)}")
    lines.append("")
    lines.append(
        f"**当前评分**：{U.fmt_score(project.score)}"
        f"（{score_breakdown(player, project.ptype, project.invest, project)}）"
    )
    lines.append(f"**参演人数**：{len(project.artists)}/{C.MAX_ARTISTS_PER_PROJECT}")
    lines.append(f"**预计结束**：{U.fmt_clock(project.end_at)}")
    if skipped:
        lines.append("")
        lines.append("未加入：" + "；".join(skipped[:6]))
    return Result.success(card=R.Card(markdown="\n".join(lines)))


# --------------------------------------------------------------------------
# 结算
# --------------------------------------------------------------------------
def settle_project(store: GameStore, player: Player, project: Project, ts: float | None = None) -> str:
    """结算一个已到期的项目，返回通知文本。"""
    ts = ts if ts is not None else U.now()
    if project.settled:
        return ""
    project.settled = True
    project.status = C.PROJECT_DONE
    project.finished_at = ts

    # 玩家「聚光灯」增益：项目最终评分 +0.5
    bonus = player.buff_value("project", ts)
    if bonus:
        project.score = U.round_score(max(C.MIN_SCORE, project.score + bonus / 10))

    revenue = calc_revenue(project.invest, project.score)
    project.revenue = revenue
    # 收益按各方投入资金的比例分配
    total_invest = max(1, project.invest)
    distribution: dict[str, int] = {}
    for uid, funds in project.investor_funds.items():
        funds = int(funds or 0)
        distribution[str(uid)] = int(revenue * funds // total_invest) if funds else 0
    owner_share = distribution.get(player.uid, 0)
    if owner_share:
        player.earn(owner_share)
    for uid, share in distribution.items():
        if uid == player.uid or not share:
            continue
        other = store.get(uid)
        if other is not None:
            other.earn(share)

    lines = [
        f"# 《{project.name}》已结束",
        f"**类型**：{project.ptype}　**时长**：{project.duration} 小时",
        f"**最终评分**：{U.fmt_score(project.score)}",
        f"**总投资**：{U.fmt_money(project.invest)}　**回收**：{U.fmt_money(revenue)}　　　"
        f"**净收益**：{U.fmt_signed(revenue - project.invest)}",
    ]
    if len(distribution) > 1 or len(project.investor_funds) > 1:
        parts = []
        for uid, share in distribution.items():
            funds = int(project.investor_funds.get(uid, 0))
            if uid == player.uid:
                who = player.name
            else:
                other = store.get(uid)
                who = other.name if other is not None else "某位玩家"
            parts.append(f"{who}（投入 {U.fmt_money(funds)}）分得 {U.fmt_money(share)}")
        lines.append("**收益分配**：" + "；".join(parts))
    if project.salary_paid:
        lines.append(f"**艺人薪资支出**：{U.fmt_money(project.salary_paid)}")
    if project.score < C.SCORE_LOSS_ALL:
        lines.append("> 口碑崩盘，投资血本无归。")
    elif project.score < C.SCORE_LOSS_HALF:
        lines.append("> 反响平平，勉强收回一半投资。")
    else:
        lines.append("> 项目盈利，继续保持。")

    growth_lines: list[str] = []
    for aid in list(project.artists):
        artist = next((a for a in player.artists if a.aid == aid), None)
        if artist is None:
            continue
        artist_sys.tick_project_stamina(artist, ts)  # 补扣最后不足 1 小时的体力
        growth_lines.append(
            artist_sys.apply_project_growth(store, player, artist, project, project.score, ts)
        )
    if growth_lines:
        lines.append("")
        lines.append("**艺人成长**")
        lines.extend(f"- {text}" for text in growth_lines)

    lines.append("")
    lines.append(
        f"**现金**：{U.fmt_money(player.cash)}　**总资产**：{U.fmt_money(player.total_asset)}"
    )
    player.push_log(
        f"《{project.name}》结算：评分 {U.fmt_score(project.score)}，"
        f"净收益 {U.fmt_signed(revenue - project.invest)}"
    )
    store.mark_dirty()
    return "\n".join(lines)


def cancel_project(store: GameStore, player: Player, name: str) -> Result:
    """取消待启动项目并退回投资。"""
    project = player.find_project(name)
    if project is None:
        return Result.fail(f"没有找到名为《{name}》的项目。")
    if project.status == C.PROJECT_RUNNING:
        return Result.fail(f"《{project.name}》已开机，无法取消。")
    if project.status != C.PROJECT_PENDING:
        return Result.fail(f"《{project.name}》当前状态无法取消。")
    project.status = C.PROJECT_CANCELLED
    project.settled = True
    player.earn(project.invest)
    store.mark_dirty()
    return Result.success(
        text=f"已取消《{project.name}》，退回投资 {U.fmt_money(project.invest)}。"
    )


# --------------------------------------------------------------------------
# 外部投资
# --------------------------------------------------------------------------
def invest_market(store: GameStore, player: Player, index: int) -> Result:
    """投资商业活动中的外部项目。"""
    ensure_daily_market(store, player)
    if not player.market:
        return Result.fail("当前没有可选的外部项目，请先发送「商业活动」刷新列表。")
    if index < 1 or index > len(player.market):
        return Result.fail(f"本次商业活动中没有第 {index} 号项目，可发送「商业活动」查看。")
    item = player.market[index - 1]
    if item.taken:
        return Result.fail(f"《{item.name}》已经投资过了，换一个吧。")
    if not player.can_afford(item.invest):
        return Result.fail(
            f"资金不足：投资《{item.name}》需要 {U.fmt_money(item.invest)}，"
            f"你当前有 {U.fmt_money(player.money)}。"
        )
    player.pay(item.invest)
    item.taken = True
    ts = U.now()
    final_name = unique_project_name(player, item.name)
    project = Project(
        pid=U.new_id("p"),
        name=final_name,
        ptype=item.ptype,
        invest=item.invest,
        base_score=item.score,
        score=item.score,
        duration=item.duration,
        status=C.PROJECT_RUNNING,
        start_at=ts,
        end_at=ts + item.duration * 3600,
        external=True,
        owner=player.uid,
    )
    project.investor_funds[player.uid] = int(project.investor_funds.get(player.uid, 0)) + project.invest
    player.projects.append(project)
    player.push_log(f"投资外部项目《{final_name}》{U.fmt_money(item.invest)}")
    store.mark_dirty()
    lines = [
        "# 投资成功",
        f"**项目**：《{project.name}》",
        f"**类型**：{project.ptype}　**投资**：{U.fmt_money(project.invest)}",
        f"**预期评分**：{U.fmt_score(project.score)}",
        f"**预计结束**：{U.fmt_clock(project.end_at)}（{project.duration} 小时后）",
    ]
    if final_name != item.name:
        lines.insert(2, f"检测到已有同名项目，本次自动命名为《{final_name}》。")
    lines.extend(["", "到期后会自动结算并通知你。"])
    return Result.success(card=R.Card(markdown="\n".join(lines)))


# --------------------------------------------------------------------------
# 追加投资 / 开放项目投资
# --------------------------------------------------------------------------
def find_any_project(store: GameStore, name: str) -> tuple[Player, Project] | None:
    """在所有玩家的项目中按名称查找项目（用于其他玩家追加投资开放项目）。"""
    name = (name or "").strip().strip("《》")
    if not name:
        return None
    for owner in store.all_players():
        project = owner.find_project(name)
        if project is not None and project.status != C.PROJECT_DONE:
            return owner, project
    return None


def _production_delta(old_invest: int, new_invest: int) -> float:
    """计算追加投资带来的制作加成增长（评分增量）。"""
    return calc_production_bonus(new_invest) - calc_production_bonus(old_invest)


def find_project_for_invest(store: GameStore, player: Player, name: str) -> tuple[Player, Project | None, bool]:
    """定位用于追加投资的项目。

    ``is_owner`` 表示该玩家是否为项目业主；业主可以对任何未结束的项目追加，
    其他玩家只能追加自己已开放投资的项目。
    返回 (业主, 项目, 是否业主)；找不到项目时返回 (None, None, False)。
    """
    own = player.find_project(name)
    if own is not None and own.status != C.PROJECT_DONE:
        return player, own, True
    found = find_any_project(store, name)
    if found is not None:
        return found[0], found[1], False
    return None, None, False


def add_investment(store: GameStore, player: Player, project_name: str, amount: int) -> Result:
    """追加项目投资，提高投资额与项目评分。

    * 业主可直接追加自己名下未结束的项目；
    * 其他玩家仅当项目已开放投资时才能追加，追加同样提高评分；
    * 收益在项目结算时按各方投入的资金比例分配。
    """
    project_name = (project_name or "").strip().strip("《》")
    if not project_name:
        return Result.fail("请指定项目名，例如「追加投资《长夜列车》500」。")
    amount = int(amount)
    if amount < C.MIN_INVEST:
        return Result.fail(
            f"追加投资金额过低，最少 {U.fmt_money(C.MIN_INVEST)}，例如「追加投资《项目名》500」。"
        )
    if not player.can_afford(amount):
        return Result.fail(
            f"资金不足：追加需要 {U.fmt_money(amount)}，你当前有 {U.fmt_money(player.money)}。"
        )

    owner, project, is_owner = find_project_for_invest(store, player, project_name)
    if project is None:
        return Result.fail(f"没有找到可追加投资的项目《{project_name}》。")
    if not is_owner and not project.open_invest:
        return Result.fail(
            f"《{project.name}》尚未开放投资，只有业主可以追加；或等业主开放后再投资。"
        )
    if project.status != C.PROJECT_PENDING and project.status != C.PROJECT_RUNNING:
        return Result.fail(f"《{project.name}》状态为 {project.status}，无法追加投资。")

    old_invest = project.invest
    new_invest = old_invest + amount
    player.pay(amount)
    project.invest = new_invest
    project.investor_funds[player.uid] = int(project.investor_funds.get(player.uid, 0)) + amount
    score_add = _production_delta(old_invest, new_invest)
    project.score = U.round_score(max(C.MIN_SCORE, project.score + score_add))
    store.mark_dirty()
    if is_owner:
        owner.push_log(f"为《{project.name}》追加投资 {U.fmt_money(amount)}")
    else:
        owner.push_log(f"{player.name} 为《{project.name}》追加投资 {U.fmt_money(amount)}")

    share = int(project.investor_funds.get(player.uid, 0))
    total = project.invest
    percent = share / total * 100 if total else 0
    lines = [
        "# 追加投资成功",
        f"**项目**：《{project.name}》（{project.ptype}）",
        f"**追加**：{U.fmt_money(amount)}　**总投资**：{U.fmt_money(project.invest)}",
        f"**评分**：{U.fmt_score(project.score)}（制作加成 +{U.fmt_score(score_add)}）",
        f"**你的投入**：{U.fmt_money(share)}（占总资金 {percent:.1f}%）",
        "",
        "项目结算收益将按投入资金比例分配。",
    ]
    if project.status == C.PROJECT_PENDING:
        lines.append(f"发送「{project.name}项目开始」可启动项目。")
    return Result.success(card=R.Card(markdown="\n".join(lines)))


def open_project_investment(store: GameStore, player: Player, name: str) -> Result:
    """开放名下项目，让其他玩家可以追加投资（仅业主可操作）。"""
    project = player.find_project(name)
    if project is None:
        return Result.fail(f"名册中没有名为《{name}》的项目，可发送「项目面板」查看。")
    if project.status == C.PROJECT_DONE:
        return Result.fail(f"《{project.name}》已结束，无法开放投资。")
    project.open_invest = True
    store.mark_dirty()
    return Result.success(
        card=R.Card(
            markdown=(
                f"# 已开放投资\n"
                f"**《{project.name}》** 已开放投资，其他玩家可发送"
                f"「追加投资《{project.name}》（金额）」参与，投资同样提升评分，"
                f"结算时收益按各方投入资金比例分配。"
            )
        )
    )


def close_project_investment(store: GameStore, player: Player, name: str) -> Result:
    """关闭名下项目的投资通道（仅业主可操作）。"""
    project = player.find_project(name)
    if project is None:
        return Result.fail(f"名册中没有名为《{name}》的项目。")
    if not project.open_invest:
        return Result.fail(f"《{project.name}》当前并未开放投资。")
    project.open_invest = False
    store.mark_dirty()
    return Result.success(
        text=f"已关闭《{project.name}》的投资通道，其他玩家不能再追加投资。"
    )
