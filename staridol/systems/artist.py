"""艺人养成系统：秀场招募、聘用/解聘、训练、休息、体力与等级晋升。

这是本插件的核心玩法，优先保证该模块的完整性与手感。
所有面向玩家的文案均不使用 emoji。
"""

from __future__ import annotations

import random

from .. import constants as C
from .. import render as R
from .. import utils as U
from ..models import Artist, Player, Project
from ..result import Result
from ..store import GameStore


# --------------------------------------------------------------------------
# 体力
# --------------------------------------------------------------------------
def regen_stamina(artist: Artist, ts: float | None = None) -> int:
    """按时间自然恢复体力，返回恢复量。"""
    ts = ts if ts is not None else U.now()
    if artist.stamina >= 100:
        artist.stamina_at = ts
        return 0
    last = artist.stamina_at or artist.hired_at or ts
    elapsed = ts - last
    if elapsed < 60:
        return 0
    gain = int(elapsed / 3600 * C.STAMINA_REGEN_PER_HOUR)
    if gain <= 0:
        return 0
    before = artist.stamina
    artist.stamina = min(100, artist.stamina + gain)
    artist.stamina_at = ts
    return artist.stamina - before


def apply_stamina_cost(artist: Artist, cost: int, ts: float | None = None) -> None:
    """扣除体力并刷新恢复计时起点。"""
    artist.stamina = max(0, artist.stamina - max(0, cost))
    if artist.stamina >= 100:
        artist.stamina_at = ts if ts is not None else U.now()


def tick_project_stamina(artist: Artist, ts: float | None = None) -> int:
    """参加项目期间按小时扣除体力，返回本次实际扣除量。

    每满 1 小时扣 ``PROJECT_STAMINA_PER_HOUR`` 点，不足 1 小时的部分会
    累计到下一次结算，体力最低扣到 0 为止（不会变成负数）。
    """
    ts = ts if ts is not None else U.now()
    if artist.status != C.STATUS_PROJECT:
        artist.project_stamina_at = ts
        return 0
    last = artist.project_stamina_at or ts
    if last > ts:
        artist.project_stamina_at = ts
        return 0
    hours = int((ts - last) / 3600)
    if hours <= 0:
        return 0
    cost = hours * C.PROJECT_STAMINA_PER_HOUR
    real = min(cost, artist.stamina)
    artist.stamina -= real
    artist.project_stamina_at += hours * 3600
    return real


def project_stamina_forecast(hours: int) -> int:
    """预估一个项目会消耗的体力。"""
    return max(0, int(hours) * C.PROJECT_STAMINA_PER_HOUR)


# --------------------------------------------------------------------------
# 训练
# --------------------------------------------------------------------------
def training_cost(artist: Artist, attr_key: str) -> int:
    """计算单次训练费用（w）。

    属性 50 以下 10w/点，50~70 为 30w，70~90 为 50w，90 以上 100w。
    """
    value = artist.get(attr_key)
    for cap, cost in C.TRAINING_COST_TIERS:
        if value < cap:
            return cost
    return C.TRAINING_COST_TIERS[-1][1]


def train(store: GameStore, player: Player, artist: Artist, attr_cn: str) -> Result:
    """安排艺人训练某一项属性。"""
    attr_key = C.ARTIST_ATTRS.get(attr_cn)
    if not attr_key:
        return Result.fail(f"没有「{attr_cn}」这项属性，可训练：{'、'.join(C.ARTIST_ATTRS)}")
    ts = U.now()
    regen_stamina(artist, ts)
    if artist.is_busy(ts):
        return Result.fail(f"{artist.name} 正在{artist.status_text or '忙碌'}，{artist.status_line(ts)}")
    if artist.stamina < C.TRAINING_STAMINA_COST:
        return Result.fail(
            f"{artist.name} 体力不足（{artist.stamina}/100），需要 {C.TRAINING_STAMINA_COST} 点。"
            f"可先发送「{artist.name}去休息」恢复体力。"
        )
    cost = training_cost(artist, attr_key)
    if not player.can_afford(cost):
        return Result.fail(
            f"资金不足：本次训练需要 {U.fmt_money(cost)}，"
            f"你当前有 {U.fmt_money(player.money)}。"
        )
    player.pay(cost)
    apply_stamina_cost(artist, C.TRAINING_STAMINA_COST, ts)
    artist.status = C.STATUS_TRAINING
    artist.training_attr = attr_key
    artist.training_from = artist.get(attr_key)
    artist.busy_until = ts + C.TRAINING_DURATION
    artist.status_text = f"练习{attr_cn}中"
    store.mark_dirty()

    lines = [
        "# 训练安排完成",
        f"**{artist.name}** 已前往训练室，开始练习**{attr_cn}**。",
        "",
        f"**花费**：{U.fmt_money(cost)}　**体力**：{artist.stamina}/100（-{C.TRAINING_STAMINA_COST}）",
        f"**预计**：{U.fmt_clock(artist.busy_until)} 完成（约 1 小时）",
        "",
        "训练结束后会主动通知你。",
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)))


def finish_training(player: Player, artist: Artist, ts: float | None = None) -> str:
    """结算一次训练，返回通知文本。"""
    ts = ts if ts is not None else U.now()
    attr_key = artist.training_attr or "eloquence"
    attr_cn = C.ARTIST_ATTR_CN.get(attr_key, "属性")
    before = artist.get(attr_key)
    # 10% 概率触发"灵感爆发"，一次涨 2 点
    gain = 2 if random.random() < 0.10 else 1
    real_gain = artist.add(attr_key, gain)
    artist.status = C.STATUS_IDLE
    artist.status_text = ""
    artist.training_attr = ""
    artist.busy_until = 0.0
    artist.stamina_at = ts

    level_text = ""
    new_level = artist.level_up_ready()
    if new_level:
        old_level = artist.level
        artist.level = new_level
        level_text = f"\n**等级晋升**：{old_level} → **{new_level}**（工资 {U.fmt_money(C.LEVEL_SALARY[new_level])}）"

    burst = "（灵感爆发）" if real_gain > 1 else ""
    return (
        f"# 训练完成\n"
        f"**{artist.name}** 已完成{attr_cn}训练{burst}\n\n"
        f"**{attr_cn}**：{before} → {artist.get(attr_key)}（+{real_gain}）\n"
        f"**体力**：{artist.stamina}/100"
        f"{level_text}"
    )


def rest(store: GameStore, player: Player, artist: Artist) -> Result:
    """安排艺人休息，恢复体力。"""
    ts = U.now()
    regen_stamina(artist, ts)
    if artist.is_busy(ts):
        return Result.fail(f"{artist.name} 正在{artist.status_text or '忙碌'}，暂时无法休息。")
    if artist.stamina >= 100:
        return Result.fail(f"{artist.name} 体力已满（100/100），无需休息。")
    artist.status = C.STATUS_REST
    artist.status_text = "休息中"
    artist.busy_until = ts + C.REST_DURATION
    store.mark_dirty()
    lines = [
        "# 休息安排完成",
        f"**{artist.name}** 已回休息室调整状态。",
        "",
        f"**预计**：{U.fmt_clock(artist.busy_until)} 恢复（体力 +{C.REST_STAMINA_GAIN}）",
        f"**当前体力**：{artist.stamina}/100",
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)))


def finish_rest(player: Player, artist: Artist, ts: float | None = None) -> str:
    """结算一次休息。"""
    ts = ts if ts is not None else U.now()
    before = artist.stamina
    artist.stamina = min(100, artist.stamina + C.REST_STAMINA_GAIN)
    artist.status = C.STATUS_IDLE
    artist.status_text = ""
    artist.busy_until = 0.0
    artist.stamina_at = ts
    return (
        f"# 休息结束\n"
        f"**{artist.name}** 已恢复精神。\n\n"
        f"**体力**：{before} → {artist.stamina}/100"
    )


# --------------------------------------------------------------------------
# 秀场
# --------------------------------------------------------------------------
def ensure_daily_show(store: GameStore, player: Player, ts: float | None = None) -> None:
    """跨天时重置秀场次数与候选。"""
    ts = ts if ts is not None else U.now()
    today = U.today_str(ts)
    if player.show_date != today:
        player.show_date = today
        player.show_left = store.show_refresh
        player.show_pool = []
        store.mark_dirty()


def refresh_show(store: GameStore, player: Player) -> Result:
    """刷新今日秀场（消耗一次机会）。"""
    ensure_daily_show(store, player)
    if player.show_left <= 0:
        return Result.fail(
            "今日秀场次数已用完（0 次）。\n"
            "可在商城购买「秀场邀请函」恢复次数，或明天再来。"
        )
    player.show_left -= 1
    used = player.used_names() | store.history_names
    pool: list[Artist] = []
    fresh: set[str] = set()
    for _ in range(max(1, store.show_artist_count)):
        artist = Artist.roll(used | fresh)
        # 保证同批不重名
        guard = 0
        while artist.name in fresh and guard < 30:
            artist.name = U.rand_name(used | fresh)
            guard += 1
        fresh.add(artist.name)
        pool.append(artist)
    store.record_names(fresh)
    player.show_pool = pool
    store.mark_dirty()
    return Result.success(card=R.render_show_pool(player, store.show_refresh))


def hire(store: GameStore, player: Player, name: str) -> Result:
    """聘用秀场中的艺人。"""
    name = (name or "").strip()
    if not name:
        return Result.fail("请使用「聘用艺人名」进行签约。")
    if player.find_artist(name):
        return Result.fail(f"{name} 已经在你的员工名册中了。")
    artist = next((a for a in player.show_pool if a.name == name), None)
    if artist is None:
        return Result.fail(
            f"当前秀场中没有名为「{name}」的艺人。\n"
            "发送「今日秀场」查看本批候选（刷新后上一批将无法签约）。"
        )
    if len(player.artists) >= store.max_artists:
        return Result.fail(f"员工人数已达上限（{store.max_artists} 人），请先解聘部分艺人。")
    fee = artist.salary * C.SIGN_FEE_RATE
    if not player.can_afford(fee):
        return Result.fail(
            f"资金不足：签约 {name} 需要 {U.fmt_money(fee)}，你当前有 {U.fmt_money(player.money)}。"
        )
    player.pay(fee)
    artist.hired_at = U.now()
    artist.stamina_at = U.now()
    artist.status = C.STATUS_IDLE
    artist.status_text = ""
    artist.busy_until = 0.0
    player.artists.append(artist)
    player.show_pool = [a for a in player.show_pool if a.aid != artist.aid]
    store.record_names({artist.name})
    store.mark_dirty()

    lines = [
        "# 签约成功",
        f"**{artist.name}** 已加入 {player.company_name or '集团'}。",
        "",
        f"**签约金**：{U.fmt_money(fee)}（{artist.level} 工资 {U.fmt_money(artist.salary)}）",
        "**属性**：",
        R.artist_attr_block(artist),
        f"**估值**：{U.fmt_money(artist.value)}",
        "",
        f"可发送「{artist.name}去训练舞蹈」开始培养，或「查询{artist.name}」查看面板。",
    ]
    return Result.success(card=R.Card(markdown="\n".join(lines)))


def fire(store: GameStore, player: Player, name: str) -> Result:
    """解聘艺人。"""
    artist = player.find_artist(name)
    if artist is None:
        return Result.fail(f"名册中没有找到「{name}」。")
    if artist.is_busy():
        return Result.fail(f"{artist.name} 正在{artist.status_text or '忙碌'}，无法解聘。")
    player.artists = [a for a in player.artists if a.aid != artist.aid]
    store.record_names({artist.name})
    store.mark_dirty()
    return Result.success(
        text=f"已解除与 {artist.name} 的合约，{artist.name} 离开了 {player.company_name}。"
    )


def promote(store: GameStore, player: Player, artist: Artist) -> str:
    """检查并执行等级晋升，返回提示文本（无晋升时返回空串）。"""
    new_level = artist.level_up_ready()
    if not new_level:
        return ""
    old_level = artist.level
    artist.level = new_level
    store.mark_dirty()
    return f"{artist.name} 晋升：{old_level} → {new_level}"


# --------------------------------------------------------------------------
# 项目成长
# --------------------------------------------------------------------------
def apply_project_growth(
    store: GameStore,
    player: Player,
    artist: Artist,
    project: Project,
    score: float,
    ts: float | None = None,
) -> str:
    """项目结束后结算艺人的成长（粉丝 / 人气 / 口碑）。"""
    ts = ts if ts is not None else U.now()
    scale = max(0.4, min(3.0, project.invest / 1000 + 0.4))
    fans_gain = int(C.ARTIST_GROWTH_RATE["fans"] * score * scale * random.uniform(0.8, 1.25))
    fame_gain = int(C.ARTIST_GROWTH_RATE["fame"] * score * scale * random.uniform(0.8, 1.2))
    rep_gain = int(C.ARTIST_GROWTH_RATE["reputation"] * score * random.uniform(0.6, 1.4))
    if score < C.SCORE_LOSS_ALL:
        # 扑街项目会掉口碑
        rep_gain = -abs(rep_gain or 1)
        fame_gain = max(0, fame_gain // 2)

    artist.fans += max(0, fans_gain)
    artist.fame = max(0, artist.fame + fame_gain)
    artist.reputation = max(0, artist.reputation + rep_gain)
    artist.total_projects += 1
    artist.status = C.STATUS_IDLE
    artist.status_text = ""
    artist.project_id = ""
    artist.busy_until = 0.0
    artist.stamina_at = ts
    artist.project_stamina_at = 0.0
    store.mark_dirty()

    line = (
        f"{artist.name}：粉丝 {fans_gain:+d}（当前 {U.fmt_number(artist.fans)}）　"
        f"人气 {fame_gain:+d}（当前 {artist.fame}）　"
        f"口碑 {rep_gain:+d}（当前 {artist.reputation}）"
    )
    promotion = promote(store, player, artist)
    if promotion:
        line += f"　{promotion}"
    return line
