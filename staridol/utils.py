"""通用工具函数：骰点、时间、数值格式化、随机姓名、ID 生成。"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import constants as C

# --------------------------------------------------------------------------
# 时间
# --------------------------------------------------------------------------


def now() -> float:
    """当前时间戳（秒）。"""
    return time.time()


def today_str(ts: float | None = None) -> str:
    """返回 YYYY-MM-DD 形式的日期字符串。"""
    return datetime.fromtimestamp(ts if ts is not None else now()).strftime("%Y-%m-%d")


def seconds_until_next_day(ts: float | None = None) -> float:
    """距离下一个自然日 0 点的秒数。"""
    current = datetime.fromtimestamp(ts if ts is not None else now())
    tomorrow = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1.0, (tomorrow - current).total_seconds())


def fmt_clock(ts: float) -> str:
    """时间戳 -> HH:MM。"""
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def fmt_datetime(ts: float) -> str:
    """时间戳 -> MM-DD HH:MM。"""
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def fmt_duration(seconds: float) -> str:
    """把秒数格式化成人类友好的时长描述。"""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分钟" if sec < 30 else f"{minutes}分{sec}秒"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}小时{minutes}分" if minutes else f"{hours}小时"
    days, hours = divmod(hours, 24)
    return f"{days}天{hours}小时" if hours else f"{days}天"


# --------------------------------------------------------------------------
# 数值格式化（游戏内金钱单位统一为 w = 万）
# --------------------------------------------------------------------------


def fmt_money(amount: float) -> str:
    """把以 w 为单位的金额格式化成易读字符串。"""
    value = int(round(amount))
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 10000:  # 1 亿 = 10000 w
        return f"{sign}{value / 10000:.2f}亿"
    return f"{sign}{value}w"


def fmt_signed(amount: float) -> str:
    """带正负号的金额显示。"""
    value = int(round(amount))
    if value > 0:
        return f"+{fmt_money(value)}"
    return fmt_money(value)


def fmt_number(value: float) -> str:
    """大数字紧凑显示（用于粉丝数）。"""
    value = int(value)
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f}亿"
    if value >= 10_000:
        return f"{value / 10_000:.1f}万"
    return str(value)


def fmt_traffic(value: float) -> str:
    """赌场人流显示：按 w（万）为单位紧凑展示，如 3.2w / 1.5亿人次。

    与 ``fmt_money`` 的 w/亿 约定一致，方便与金额对比。
    """
    value = int(value)
    if value >= 100_000_000:  # 1 亿人次
        return f"{value / 100_000_000:.2f}亿"
    if value >= 10_000:  # 1 万 = 1w
        return f"{value / 10_000:.1f}w"
    return f"{value}"


def fmt_score(score: float) -> str:
    """评分保留一位小数。"""
    return f"{round(score + 1e-9, 1):.1f}"


def round_score(score: float) -> float:
    """评分四舍五入到一位小数。"""
    return round(score + 1e-9, 1)


# --------------------------------------------------------------------------
# 随机
# --------------------------------------------------------------------------


def rand_name(used: set[str] | None = None, max_try: int = 60) -> str:
    """随机生成一个中文姓名，尽量避开 used 中出现过的名字。"""
    used = used or set()
    for _ in range(max_try):
        surname = random.choice(C.SURNAMES)
        given = random.choice(C.GIVEN_NAMES)
        name = f"{surname}{given}"
        if name not in used:
            return name
    # 极端情况下追加随机后缀保证唯一
    while True:
        name = f"{random.choice(C.SURNAMES)}{random.choice(C.GIVEN_NAMES)}{random.randint(2, 99)}"
        if name not in used:
            return name


def rand_attr(low: int = C.ARTIST_ATTR_MIN, high: int = C.ARTIST_ATTR_MAX) -> int:
    """随机生成一项艺人属性，偏向中间值，避免全员怪物或全员废柴。

    采用两次三角分布的均值，使 30~80 区间更常见。
    """
    a = random.randint(low, high)
    b = random.randint(low, high)
    value = int(round((a + b) / 2))
    return max(low, min(high, value))


def weighted_choice(pairs: list[tuple[str, int]]) -> str:
    """按权重随机选择一个键。"""
    if not pairs:
        raise ValueError("weighted_choice 需要非空列表")
    total = sum(max(0, w) for _, w in pairs)
    if total <= 0:
        return pairs[0][0]
    point = random.uniform(0, total)
    upto = 0.0
    for key, weight in pairs:
        upto += max(0, weight)
        if point <= upto:
            return key
    return pairs[-1][0]


def new_id(prefix: str = "") -> str:
    """生成短 ID。"""
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def clamp(value: float, low: float, high: float) -> float:
    """把数值限制在 [low, high] 区间。"""
    return max(low, min(high, value))


# --------------------------------------------------------------------------
# 判定系统
# --------------------------------------------------------------------------


@dataclass
class CheckResult:
    """一次属性检定的结果。"""

    attr_name: str  # 属性中文名
    attr_value: int  # 检定使用的属性值（含临时加成）
    base_value: int  # 不含加成的原始属性值
    roll: int  # 骰点
    upper: int  # 骰点上限
    success: bool  # 是否成功
    crit_success: bool  # 大成功
    crit_fail: bool  # 大失败
    margin: int  # 与成功线的差值（正数表示成功得越轻松）

    @property
    def level_cn(self) -> str:
        if self.crit_success:
            return "大成功"
        if self.crit_fail:
            return "大失败"
        return "成功" if self.success else "失败"

    def describe(self) -> str:
        """一行式的检定描述。"""
        return (
            f"【{self.attr_name}检定】骰点 {self.roll}/{self.upper} "
            f"（属性 {self.attr_value}）→ {self.level_cn}"
        )


def roll_check(attr_name: str, attr_value: int, bonus: int = 0) -> CheckResult:
    """执行一次属性检定。

    规则：
      * 属性 < 100：骰点 1-100；``<= 属性`` 成功，``<= 5`` 大成功，``>= 96`` 大失败。
      * 属性 >= 100：骰点 1-``属性 * 1.5``；``<= 属性 * 0.1`` 大成功，
        ``<= 属性`` 成功，``>= 属性 * 1.4`` 大失败（等价于 属性*1.5 - 属性*0.1）。
    """
    effective = max(1, int(attr_value) + int(bonus))
    if effective < C.CHECK_NORMAL_THRESHOLD:
        upper = 100
        roll_value = random.randint(1, upper)
        crit_success = roll_value <= C.CHECK_CRIT_SUCCESS
        crit_fail = roll_value >= C.CHECK_CRIT_FAIL
        success = roll_value <= effective
    else:
        upper = int(round(effective * C.CHECK_HIGH_ROLL_MULTIPLIER))
        roll_value = random.randint(1, upper)
        crit_line = max(1, int(effective * 0.1))
        fail_line = int(round(effective * (C.CHECK_HIGH_ROLL_MULTIPLIER - 0.1)))
        crit_success = roll_value <= crit_line
        crit_fail = roll_value >= fail_line
        success = roll_value <= effective

    # 大成功必然判定为成功；大失败必然判定为失败
    if crit_success:
        success = True
    if crit_fail:
        success = False

    return CheckResult(
        attr_name=attr_name,
        attr_value=effective,
        base_value=int(attr_value),
        roll=roll_value,
        upper=upper,
        success=success,
        crit_success=crit_success,
        crit_fail=crit_fail,
        margin=effective - roll_value,
    )
