"""今日事务系统。

玩家每天会收到若干条"今日事务"，每条事务提供三个可用属性方向，
点击按钮后输入框会被填入 ``检定xx`` 指令，发送即可完成判定并领取奖励。

归属校验：只有"自己发送过今日行程（拥有待检定事务）"的玩家才能触发判定，
其他人点击别人卡片里的按钮不会得到任何回复。
"""

from __future__ import annotations

import random

from .. import constants as C
from .. import render as R
from .. import utils as U
from ..models import Player, Quest
from ..result import Result
from ..store import GameStore


# --------------------------------------------------------------------------
# 事务生成
# --------------------------------------------------------------------------
def generate_quests(count: int) -> list[Quest]:
    """按模板池生成 count 条事务（模板不足时循环使用，选项顺序随机）。"""
    count = max(1, int(count))
    templates = list(C.QUEST_TEMPLATES)
    random.shuffle(templates)
    quests: list[Quest] = []
    for index in range(count):
        template = templates[index % len(templates)]
        options = [{"label": label, "attr": attr} for label, attr in template["options"]]
        random.shuffle(options)
        quests.append(
            Quest(
                qid=U.new_id("q"),
                index=index + 1,
                title=str(template["title"]),
                desc=str(template["desc"]),
                options=options,
            )
        )
    return quests


def reset_quests(store: GameStore, player: Player, ts: float | None = None, force: bool = False) -> bool:
    """每日重置事务，返回是否发生了重置。"""
    ts = ts if ts is not None else U.now()
    today = U.today_str(ts)
    if not force and player.last_quest_date == today:
        return False
    player.last_quest_date = today
    tier = store.tier_info(player)
    player.quests = generate_quests(tier.quest_count)
    player.active_quest = ""
    store.mark_dirty()
    return True


def add_quests(store: GameStore, player: Player, count: int = 1) -> int:
    """追加事务（道具「事务刷新卡」使用）。"""
    exist = len(player.quests)
    new_quests = generate_quests(count)
    for offset, quest in enumerate(new_quests):
        quest.index = exist + offset + 1
    player.quests.extend(new_quests)
    store.mark_dirty()
    return len(new_quests)


# --------------------------------------------------------------------------
# 展示
# --------------------------------------------------------------------------
def show_quest(store: GameStore, player: Player) -> Result:
    """「今日行程」：展示下一条待处理事务卡片。"""
    if not player.quests:
        reset_quests(store, player, force=True)
    quest = player.pending_quest()
    if quest is None:
        return Result.success(card=R.render_quest_summary(player))
    player.active_quest = quest.qid
    store.mark_dirty()
    done = sum(1 for q in player.quests if q.done)
    return Result.success(card=R.render_quest_card(quest, player, done, len(player.quests)))


def summary(player: Player) -> Result:
    """事务进度总览。"""
    return Result.success(card=R.render_quest_summary(player))


# --------------------------------------------------------------------------
# 检定
# --------------------------------------------------------------------------
def do_check(store: GameStore, player: Player, attr_cn: str) -> Result:
    """执行一次今日事务判定。"""
    attr_cn = (attr_cn or "").strip()
    if attr_cn not in C.PLAYER_ATTRS:
        return Result.quiet()

    quest = player.pending_quest()
    # 没有待处理事务，或当前没有展示中的事务卡片 → 静默（并非本人操作自己的卡片）
    if quest is None or player.active_quest != quest.qid:
        return Result.quiet()

    option = next((o for o in quest.options if o.get("attr") == attr_cn), None)
    if option is None:
        available = "、".join(f"检定{o.get('attr')}" for o in quest.options)
        return Result.fail(
            f"「{quest.title}」当前没有使用**{attr_cn}**处理的方案。\n可选：{available}"
        )

    attr_key = C.PLAYER_ATTRS[attr_cn]
    bonus = player.buff_value("check")
    result = U.roll_check(attr_cn, player.attr(attr_key), bonus)
    tier = store.tier_info(player)

    extra: list[str] = []
    base_reward = C.QUEST_BASE_REWARD * tier.reward_multiplier
    delta = 0

    if result.crit_success:
        # 大成功：收益 x3，并额外获得 1 点属性
        reward = int(base_reward * 3 * random.uniform(1.0, 1.2))
        player.earn(reward)
        delta = reward
        gain = player.add_attr(attr_key, C.CHECK_CRIT_ATTR_GAIN)
        reward_text = f"**大成功**！事务处理得滴水不漏，获得 {U.fmt_money(reward)}（收益 x3）。"
        if gain:
            extra.append(f"**{attr_cn} +{gain}**（当前 {player.attr(attr_key)}）")
    elif result.success:
        reward = int(base_reward * random.uniform(0.8, 1.2))
        player.earn(reward)
        delta = reward
        reward_text = f"事务处理顺利，获得 {U.fmt_money(reward)}。"
    elif result.crit_fail:
        loss = int(max(30, player.total_asset * C.QUEST_FAIL_LOSS_RATE))
        loss = min(loss, max(0, player.money))
        real_loss = player.force_pay(loss)
        delta = -real_loss
        gain = player.add_attr(attr_key, C.CHECK_CRIT_ATTR_GAIN)
        reward_text = f"**大失败**！判断失误造成损失 {U.fmt_money(real_loss)}。"
        extra.append("这一单不仅没赚到钱，还赔进去了。")
        if gain:
            extra.append(f"但挫折也让你成长了：**{attr_cn} +{gain}**（当前 {player.attr(attr_key)}）")
    else:
        reward = int(base_reward * C.QUEST_MISS_REWARD_RATE)
        player.earn(reward)
        delta = reward
        reward_text = f"未能达成预期，仅获得安慰性收益 {U.fmt_money(reward)}。"

    quest.done = True
    quest.result_text = f"{result.level_cn}｜骰点 {result.roll}｜{U.fmt_signed(delta)}"
    player.active_quest = ""
    store.mark_dirty()

    card = R.render_check_result(result, player, reward_text, extra)

    # 自动接续下一条事务
    nxt = player.pending_quest()
    if nxt is not None:
        player.active_quest = nxt.qid
        store.mark_dirty()
        follow = R.render_quest_card(
            nxt, player, sum(1 for q in player.quests if q.done), len(player.quests)
        )
        card.markdown += (
            f"\n\n---\n\n# 今日事务 {nxt.index}/{len(player.quests)}\n"
            f"**{nxt.title}**\n\n{nxt.desc}\n\n点击下方按钮继续检定。"
        )
        card.buttons = follow.buttons
        card.plain = ""
    else:
        card.markdown += "\n\n---\n\n今日事务已全部完成，明天 0 点刷新。"
        card.plain = ""

    return Result.success(card=card)
