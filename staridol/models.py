"""游戏数据模型。

所有模型都实现了 ``to_dict`` / ``from_dict``，方便以 JSON 方式持久化。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import constants as C
from . import utils as U


def _dict_to_int(source: dict[str, Any] | None) -> dict[str, int]:
    return {str(k): int(v) for k, v in (source or {}).items()}


# --------------------------------------------------------------------------
# 艺人
# --------------------------------------------------------------------------


@dataclass
class Artist:
    """艺人（角色）。"""

    aid: str
    name: str
    level: str = "素人"
    attrs: dict[str, int] = field(default_factory=dict)
    stamina: int = 100
    fans: int = 0
    fame: int = 0
    reputation: int = 0
    status: str = C.STATUS_IDLE
    busy_until: float = 0.0
    status_text: str = ""
    project_id: str = ""
    training_attr: str = ""
    training_from: int = 0
    hired_at: float = 0.0
    total_projects: int = 0
    salary_paid: int = 0
    stamina_at: float = 0.0  # 上次体力结算时间（用于自然恢复）
    project_stamina_at: float = 0.0  # 参加项目时上次按小时扣体力的时间

    # ---- 构造 ----
    @classmethod
    def roll(cls, used_names: set[str] | None = None) -> Artist:
        """随机生成一位素人艺人（今日秀场用）。"""
        return cls(
            aid=U.new_id("a"),
            name=U.rand_name(used_names),
            level="素人",
            attrs={key: U.rand_attr() for key in C.ARTIST_ATTRS.values()},
            stamina=100,
            fans=0,
            fame=0,
            reputation=0,
            hired_at=0.0,
        )

    # ---- 属性访问 ----
    def get(self, attr: str) -> int:
        return int(self.attrs.get(attr, 0))

    def add(self, attr: str, value: int) -> int:
        """增加属性，返回实际增加量。"""
        old = self.get(attr)
        new = max(C.ARTIST_ATTR_MIN, min(C.ARTIST_ATTR_HARD_CAP, old + value))
        self.attrs[attr] = new
        return new - old

    @property
    def salary(self) -> int:
        """当前等级的工资（w）。"""
        return C.LEVEL_SALARY.get(self.level, 5)

    @property
    def value(self) -> int:
        """市场估值（w），用于集团总资产统计。"""
        base = C.LEVEL_VALUE.get(self.level, 10)
        avg = sum(self.get(k) for k in C.ARTIST_ATTRS.values()) / max(1, len(C.ARTIST_ATTRS))
        return int(base * (0.6 + avg / 100))

    @property
    def avg_attr(self) -> float:
        return sum(self.get(k) for k in C.ARTIST_ATTRS.values()) / max(1, len(C.ARTIST_ATTRS))

    def is_busy(self, ts: float | None = None) -> bool:
        ts = ts if ts is not None else U.now()
        return self.status != C.STATUS_IDLE and self.busy_until > ts

    def status_line(self, ts: float | None = None) -> str:
        """一行式状态描述。"""
        ts = ts if ts is not None else U.now()
        if not self.is_busy(ts):
            return "待命中"
        left = U.fmt_duration(self.busy_until - ts)
        base = self.status_text or C.STATUS_CN.get(self.status, "忙碌中")
        return f"{base}（预计 {U.fmt_clock(self.busy_until)} 结束，剩余 {left}）"

    def level_up_ready(self) -> str | None:
        """检查是否可以晋升，返回可晋升到的等级。"""
        if self.level not in C.LEVEL_UP_REQUIREMENT:
            return None
        req = C.LEVEL_UP_REQUIREMENT[self.level]
        if self.fans < req["fans"] or self.fame < req["fame"]:
            return None
        matched = sum(1 for k in C.ARTIST_ATTRS.values() if self.get(k) >= req["attr"])
        if matched < req["attr_count"]:
            return None
        idx = C.LEVELS.index(self.level)
        return C.LEVELS[min(idx + 1, len(C.LEVELS) - 1)]

    def level_progress(self) -> str:
        """晋升进度描述。"""
        if self.level not in C.LEVEL_UP_REQUIREMENT:
            return "已达最高等级"
        req = C.LEVEL_UP_REQUIREMENT[self.level]
        matched = sum(1 for k in C.ARTIST_ATTRS.values() if self.get(k) >= req["attr"])
        return (
            f"粉丝 {U.fmt_number(self.fans)}/{U.fmt_number(req['fans'])}，"
            f"名声 {self.fame}/{req['fame']}，"
            f"{req['attr']}+ 属性 {matched}/{req['attr_count']}"
        )

    # ---- 序列化 ----
    def to_dict(self) -> dict[str, Any]:
        return {
            "aid": self.aid,
            "name": self.name,
            "level": self.level,
            "attrs": dict(self.attrs),
            "stamina": self.stamina,
            "fans": self.fans,
            "fame": self.fame,
            "reputation": self.reputation,
            "status": self.status,
            "busy_until": self.busy_until,
            "status_text": self.status_text,
            "project_id": self.project_id,
            "training_attr": self.training_attr,
            "training_from": self.training_from,
            "hired_at": self.hired_at,
            "total_projects": self.total_projects,
            "salary_paid": self.salary_paid,
            "stamina_at": self.stamina_at,
            "project_stamina_at": self.project_stamina_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Artist:
        artist = cls(
            aid=str(data.get("aid") or U.new_id("a")),
            name=str(data.get("name") or "无名"),
        )
        artist.level = str(data.get("level") or "素人")
        if artist.level not in C.LEVELS:
            artist.level = "素人"
        attrs = data.get("attrs") or {}
        artist.attrs = {
            key: int(attrs.get(key, U.rand_attr())) for key in C.ARTIST_ATTRS.values()
        }
        artist.stamina = int(data.get("stamina", 100))
        artist.fans = int(data.get("fans", 0))
        artist.fame = int(data.get("fame", 0))
        artist.reputation = int(data.get("reputation", 0))
        artist.status = str(data.get("status") or C.STATUS_IDLE)
        artist.busy_until = float(data.get("busy_until") or 0.0)
        artist.status_text = str(data.get("status_text") or "")
        artist.project_id = str(data.get("project_id") or "")
        artist.training_attr = str(data.get("training_attr") or "")
        artist.training_from = int(data.get("training_from") or 0)
        artist.hired_at = float(data.get("hired_at") or 0.0)
        artist.total_projects = int(data.get("total_projects") or 0)
        artist.salary_paid = int(data.get("salary_paid") or 0)
        artist.stamina_at = float(data.get("stamina_at") or 0.0)
        artist.project_stamina_at = float(data.get("project_stamina_at") or 0.0)
        return artist


# --------------------------------------------------------------------------
# 项目
# --------------------------------------------------------------------------


@dataclass
class Project:
    """影视 / 音乐 / 综艺等项目。"""

    pid: str
    name: str
    ptype: str
    invest: int
    base_score: float
    score: float
    duration: int  # 小时
    status: str = C.PROJECT_PENDING
    created_at: float = field(default_factory=U.now)
    start_at: float = 0.0
    end_at: float = 0.0
    finished_at: float = 0.0
    artists: list[str] = field(default_factory=list)
    artist_scores: dict[str, float] = field(default_factory=dict)
    salary_paid: int = 0
    revenue: int = 0
    external: bool = False
    owner: str = ""
    settled: bool = False

    @property
    def is_running(self) -> bool:
        return self.status == C.PROJECT_RUNNING

    @property
    def is_pending(self) -> bool:
        return self.status == C.PROJECT_PENDING

    @property
    def type_info(self) -> C.ProjectType | None:
        return C.PROJECT_TYPES.get(self.ptype)

    def time_left(self, ts: float | None = None) -> float:
        ts = ts if ts is not None else U.now()
        return max(0.0, self.end_at - ts)

    def progress_line(self, ts: float | None = None) -> str:
        ts = ts if ts is not None else U.now()
        if self.status == C.PROJECT_PENDING:
            return "等待「项目开始」指令"
        if self.status == C.PROJECT_RUNNING:
            total = max(1.0, self.end_at - self.start_at)
            done = U.clamp(ts - self.start_at, 0, total)
            bar = U.progress_bar(done, total)
            return f"{bar} 剩余 {U.fmt_duration(self.time_left(ts))}（{U.fmt_clock(self.end_at)} 结束）"
        if self.status == C.PROJECT_DONE:
            return f"已结束，最终评分 {U.fmt_score(self.score)}，收益 {U.fmt_signed(self.revenue - self.invest)}"
        return "已取消"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "name": self.name,
            "ptype": self.ptype,
            "invest": self.invest,
            "base_score": self.base_score,
            "score": self.score,
            "duration": self.duration,
            "status": self.status,
            "created_at": self.created_at,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "finished_at": self.finished_at,
            "artists": list(self.artists),
            "artist_scores": dict(self.artist_scores),
            "salary_paid": self.salary_paid,
            "revenue": self.revenue,
            "external": self.external,
            "owner": self.owner,
            "settled": self.settled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        project = cls(
            pid=str(data.get("pid") or U.new_id("p")),
            name=str(data.get("name") or "未命名项目"),
            ptype=str(data.get("ptype") or "综艺"),
            invest=int(data.get("invest") or 0),
            base_score=float(data.get("base_score") or 0.0),
            score=float(data.get("score") or 0.0),
            duration=int(data.get("duration") or 4),
        )
        project.status = str(data.get("status") or C.PROJECT_PENDING)
        project.created_at = float(data.get("created_at") or U.now())
        project.start_at = float(data.get("start_at") or 0.0)
        project.end_at = float(data.get("end_at") or 0.0)
        project.finished_at = float(data.get("finished_at") or 0.0)
        project.artists = [str(x) for x in (data.get("artists") or [])]
        project.artist_scores = {
            str(k): float(v) for k, v in (data.get("artist_scores") or {}).items()
        }
        project.salary_paid = int(data.get("salary_paid") or 0)
        project.revenue = int(data.get("revenue") or 0)
        project.external = bool(data.get("external"))
        project.owner = str(data.get("owner") or "")
        project.settled = bool(data.get("settled"))
        return project


@dataclass
class MarketProject:
    """商业活动中可投资的外部项目。"""

    mid: str
    name: str
    ptype: str
    duration: int
    score: float
    invest: int
    taken: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mid": self.mid,
            "name": self.name,
            "ptype": self.ptype,
            "duration": self.duration,
            "score": self.score,
            "invest": self.invest,
            "taken": self.taken,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MarketProject:
        return cls(
            mid=str(data.get("mid") or U.new_id("m")),
            name=str(data.get("name") or "外部项目"),
            ptype=str(data.get("ptype") or "电视剧"),
            duration=int(data.get("duration") or 4),
            score=float(data.get("score") or 1.0),
            invest=int(data.get("invest") or 100),
            taken=bool(data.get("taken")),
        )


# --------------------------------------------------------------------------
# 今日事务
# --------------------------------------------------------------------------


@dataclass
class Quest:
    """一条今日事务。"""

    qid: str
    index: int
    title: str
    desc: str
    options: list[dict[str, str]] = field(default_factory=list)
    done: bool = False
    result_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "qid": self.qid,
            "index": self.index,
            "title": self.title,
            "desc": self.desc,
            "options": [dict(o) for o in self.options],
            "done": self.done,
            "result_text": self.result_text,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Quest:
        quest = cls(
            qid=str(data.get("qid") or U.new_id("q")),
            index=int(data.get("index") or 1),
            title=str(data.get("title") or "今日事务"),
            desc=str(data.get("desc") or ""),
        )
        quest.options = [
            {"label": str(o.get("label", "")), "attr": str(o.get("attr", "决策"))}
            for o in (data.get("options") or [])
        ]
        quest.done = bool(data.get("done"))
        quest.result_text = str(data.get("result_text") or "")
        return quest


# --------------------------------------------------------------------------
# 公司
# --------------------------------------------------------------------------


@dataclass
class Company:
    """集团下属公司（同时也是可开放的股份标的）。"""

    cid: str
    name: str
    ctype: str
    stock_price: float = 10.0
    prev_price: float = 10.0
    asset: int = 0
    created_at: float = field(default_factory=U.now)
    total_income: int = 0
    owner: str = ""  # 创建者 uid
    open_invest: bool = False  # 是否开放投资
    unit_price: int = 0  # 每 1% 股份的单价（w）
    shares_offered: int = 0  # 放出份额（%）
    holders: dict[str, int] = field(default_factory=dict)  # uid -> 持有百分比

    @property
    def change_rate(self) -> float:
        if self.prev_price <= 0:
            return 0.0
        return (self.stock_price - self.prev_price) / self.prev_price

    @property
    def change_text(self) -> str:
        rate = self.change_rate * 100
        if abs(rate) < 0.01:
            return "—"
        arrow = "↑" if rate > 0 else "↓"
        return f"{arrow}{abs(rate):.2f}%"

    @property
    def shares_taken(self) -> int:
        """已被外部投资者持有的份额（%）。"""
        return sum(max(0, int(v)) for v in self.holders.values())

    @property
    def shares_free(self) -> int:
        """还可以被投资的份额（%）。"""
        return max(0, int(self.shares_offered) - self.shares_taken)

    @property
    def state_text(self) -> str:
        """展示用状态文本：招标中或无标注。"""
        if self.open_invest and self.shares_free > 0:
            return "招标中"
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cid": self.cid,
            "name": self.name,
            "ctype": self.ctype,
            "stock_price": self.stock_price,
            "prev_price": self.prev_price,
            "asset": self.asset,
            "created_at": self.created_at,
            "total_income": self.total_income,
            "owner": self.owner,
            "open_invest": self.open_invest,
            "unit_price": self.unit_price,
            "shares_offered": self.shares_offered,
            "holders": {str(k): int(v) for k, v in self.holders.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Company:
        company = cls(
            cid=str(data.get("cid") or U.new_id("c")),
            name=str(data.get("name") or "文娱公司"),
            ctype=str(data.get("ctype") or "文娱公司"),
        )
        company.stock_price = float(data.get("stock_price") or 10.0)
        company.prev_price = float(data.get("prev_price") or company.stock_price)
        company.asset = int(data.get("asset") or 0)
        company.created_at = float(data.get("created_at") or U.now())
        company.total_income = int(data.get("total_income") or 0)
        company.owner = str(data.get("owner") or "")
        company.open_invest = bool(data.get("open_invest"))
        company.unit_price = int(data.get("unit_price") or 0)
        company.shares_offered = int(data.get("shares_offered") or 0)
        company.holders = _dict_to_int(data.get("holders"))
        return company


@dataclass
class MarketCompany:
    """股市中的独立上市公司（所有玩家共享，可被多位玩家投资）。"""

    mid: str
    name: str
    ctype: str
    unit_price: int  # 每 1% 股份的单价（w）
    prev_price: int = 0
    open: bool = False  # 今日是否招标中
    quota: int = 0  # 今日剩余可投资份额（%）
    holders: dict[str, int] = field(default_factory=dict)  # uid -> 持有百分比
    listed_at: float = field(default_factory=U.now)

    @property
    def issued(self) -> int:
        """已被玩家持有的份额（%）。"""
        return sum(max(0, int(v)) for v in self.holders.values())

    @property
    def remaining(self) -> int:
        """仍未被任何玩家持有的份额（%）。"""
        return max(0, 100 - self.issued)

    @property
    def state_text(self) -> str:
        """招标中表示可投资，其余情况无标注。"""
        if self.open and self.quota > 0 and self.remaining > 0:
            return "招标中"
        return ""

    @property
    def investable(self) -> bool:
        return self.open and self.quota > 0 and self.remaining > 0

    @property
    def change_text(self) -> str:
        if not self.prev_price:
            return "—"
        rate = (self.unit_price - self.prev_price) / self.prev_price * 100
        if abs(rate) < 0.01:
            return "—"
        arrow = "↑" if rate > 0 else "↓"
        return f"{arrow}{abs(rate):.2f}%"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mid": self.mid,
            "name": self.name,
            "ctype": self.ctype,
            "unit_price": self.unit_price,
            "prev_price": self.prev_price,
            "open": self.open,
            "quota": self.quota,
            "holders": {str(k): int(v) for k, v in self.holders.items()},
            "listed_at": self.listed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MarketCompany:
        company = cls(
            mid=str(data.get("mid") or U.new_id("s")),
            name=str(data.get("name") or "独立公司"),
            ctype=str(data.get("ctype") or "文娱公司"),
            unit_price=int(data.get("unit_price") or 100),
        )
        company.prev_price = int(data.get("prev_price") or company.unit_price)
        company.open = bool(data.get("open"))
        company.quota = int(data.get("quota") or 0)
        company.holders = _dict_to_int(data.get("holders"))
        company.listed_at = float(data.get("listed_at") or U.now())
        return company


# --------------------------------------------------------------------------
# 玩家
# --------------------------------------------------------------------------


@dataclass
class Player:
    """玩家（集团总裁）。"""

    uid: str  # QQ 号
    name: str = "董事长"
    group_id: str = ""
    umo: str = ""  # 会话来源，用于主动推送
    company_name: str = ""
    decision: int = 0
    finance: int = 0
    eloquence: int = 0
    points_assigned: bool = False
    cash: int = 1000
    deposit: int = 0
    loan: int = 0
    credit_level: int = 1
    artists: list[Artist] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    quests: list[Quest] = field(default_factory=list)
    companies: list[Company] = field(default_factory=list)
    items: dict[str, int] = field(default_factory=dict)
    buffs: dict[str, float] = field(default_factory=dict)
    last_quest_date: str = ""
    quest_total: int = 0
    show_date: str = ""
    show_left: int = 3
    show_pool: list[Artist] = field(default_factory=list)
    market_date: str = ""
    market_left: int = 3
    market: list[MarketProject] = field(default_factory=list)
    shop_date: str = ""
    shop_stock: dict[str, dict[str, Any]] = field(default_factory=dict)
    asset_tier: int = 0
    last_interest_date: str = ""
    last_company_date: str = ""
    active_quest: str = ""  # 当前已展示、等待检定的今日事务 ID
    pending_notices: list[dict] = field(default_factory=list)  # 待补发的离线通知
    created_at: float = field(default_factory=U.now)
    updated_at: float = field(default_factory=U.now)
    log: list[str] = field(default_factory=list)

    # ---- 属性 ----
    def attr(self, key: str) -> int:
        return int(getattr(self, key, 0))

    def attr_cn(self, cn_name: str) -> int:
        """通过中文属性名取玩家属性值。"""
        key = C.PLAYER_ATTRS.get(cn_name)
        return self.attr(key) if key else 0

    def add_attr(self, key: str, value: int) -> int:
        old = self.attr(key)
        new = max(0, old + value)
        setattr(self, key, new)
        return new - old

    # ---- 资产 ----
    @property
    def total_asset(self) -> int:
        """集团总资产（w）。"""
        artists_value = sum(a.value for a in self.artists)
        project_value = sum(p.invest for p in self.projects if not p.settled and p.status != C.PROJECT_CANCELLED)
        company_value = sum(c.asset for c in self.companies)
        return int(self.cash + self.deposit + artists_value + project_value + company_value - self.loan)

    @property
    def money(self) -> int:
        return int(self.cash + self.deposit)

    def can_afford(self, amount: int) -> bool:
        return self.money >= amount

    def pay(self, amount: int) -> bool:
        """支付金额：优先现金，现金不足时动用存款。"""
        amount = int(amount)
        if amount <= 0:
            return True
        if self.money < amount:
            return False
        if self.cash >= amount:
            self.cash -= amount
        else:
            rest = amount - self.cash
            self.cash = 0
            self.deposit -= rest
        return True

    def earn(self, amount: int, to_deposit: bool = False) -> None:
        """获得金钱，默认进入现金。"""
        amount = int(amount)
        if to_deposit:
            self.deposit += amount
        else:
            self.cash += amount

    def force_pay(self, amount: int) -> int:
        """强制执行扣款（不会让余额变成负数），返回实际扣除的金额。"""
        amount = max(0, int(amount))
        real = min(amount, self.money)
        if real:
            self.pay(real)
        return real

    # ---- 查询 ----
    def find_artist(self, name: str) -> Artist | None:
        name = name.strip()
        for artist in self.artists:
            if artist.name == name:
                return artist
        return None

    def find_project(self, name: str) -> Project | None:
        name = name.strip()
        for project in self.projects:
            if project.name == name and project.status != C.PROJECT_CANCELLED:
                return project
        return None

    def find_company(self, name: str) -> Company | None:
        for company in self.companies:
            if company.name == name or company.ctype == name:
                return company
        return None

    def active_projects(self) -> list[Project]:
        return [p for p in self.projects if p.status in (C.PROJECT_PENDING, C.PROJECT_RUNNING)]

    def running_projects(self) -> list[Project]:
        return [p for p in self.projects if p.status == C.PROJECT_RUNNING]

    def pending_quest(self) -> Quest | None:
        """下一条未完成的今日事务。"""
        for quest in self.quests:
            if not quest.done:
                return quest
        return None

    def used_names(self) -> set[str]:
        """所有出现过的艺人姓名（含秀场池），用于避免重名。"""
        names = {a.name for a in self.artists}
        names.update(a.name for a in self.show_pool)
        return names

    # ---- 临时增益 ----
    def buff_value(self, buff: str, ts: float | None = None) -> int:
        """读取未过期的增益数值（已过期返回 0）。"""
        ts = ts if ts is not None else U.now()
        expire = float(self.buffs.get(buff, 0) or 0)
        if expire <= ts:
            return 0
        info = C.BUFFS.get(buff)
        return int(info["value"]) if info else 0

    def buff_desc(self, ts: float | None = None) -> list[str]:
        """列出当前生效的增益描述。"""
        ts = ts if ts is not None else U.now()
        result = []
        for key, expire in self.buffs.items():
            if float(expire or 0) <= ts:
                continue
            info = C.BUFFS.get(key)
            if not info:
                continue
            result.append(f"{info['name']}（{info['desc']}，剩余 {U.fmt_duration(float(expire) - ts)}）")
        return result

    def clear_expired_buffs(self, ts: float | None = None) -> list[str]:
        """清理过期增益，返回被清理的增益名。"""
        ts = ts if ts is not None else U.now()
        expired = [k for k, v in list(self.buffs.items()) if float(v or 0) <= ts]
        for key in expired:
            self.buffs.pop(key, None)
        return expired

    # ---- 序列化 ----
    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "name": self.name,
            "group_id": self.group_id,
            "umo": self.umo,
            "company_name": self.company_name,
            "decision": self.decision,
            "finance": self.finance,
            "eloquence": self.eloquence,
            "points_assigned": self.points_assigned,
            "cash": self.cash,
            "deposit": self.deposit,
            "loan": self.loan,
            "credit_level": self.credit_level,
            "artists": [a.to_dict() for a in self.artists],
            "projects": [p.to_dict() for p in self.projects],
            "quests": [q.to_dict() for q in self.quests],
            "companies": [c.to_dict() for c in self.companies],
            "items": dict(self.items),
            "buffs": dict(self.buffs),
            "last_quest_date": self.last_quest_date,
            "quest_total": self.quest_total,
            "show_date": self.show_date,
            "show_left": self.show_left,
            "show_pool": [a.to_dict() for a in self.show_pool],
            "market_date": self.market_date,
            "market_left": self.market_left,
            "market": [m.to_dict() for m in self.market],
            "shop_date": self.shop_date,
            "shop_stock": {k: dict(v) for k, v in self.shop_stock.items()},
            "asset_tier": self.asset_tier,
            "last_interest_date": self.last_interest_date,
            "last_company_date": self.last_company_date,
            "active_quest": self.active_quest,
            "pending_notices": [dict(n) for n in self.pending_notices[-C.MAX_PENDING_NOTICES :]],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "log": list(self.log[-30:]),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Player:
        player = cls(uid=str(data.get("uid") or ""), name=str(data.get("name") or "董事长"))
        player.group_id = str(data.get("group_id") or "")
        player.umo = str(data.get("umo") or "")
        player.company_name = str(data.get("company_name") or "")
        player.decision = int(data.get("decision") or 0)
        player.finance = int(data.get("finance") or 0)
        player.eloquence = int(data.get("eloquence") or 0)
        player.points_assigned = bool(data.get("points_assigned"))
        player.cash = int(data.get("cash") or 0)
        player.deposit = int(data.get("deposit") or 0)
        player.loan = int(data.get("loan") or 0)
        player.credit_level = int(data.get("credit_level") or 1)
        player.artists = [Artist.from_dict(x) for x in (data.get("artists") or [])]
        player.projects = [Project.from_dict(x) for x in (data.get("projects") or [])]
        player.quests = [Quest.from_dict(x) for x in (data.get("quests") or [])]
        player.companies = [Company.from_dict(x) for x in (data.get("companies") or [])]
        player.items = _dict_to_int(data.get("items"))
        player.buffs = {str(k): float(v or 0) for k, v in (data.get("buffs") or {}).items()}
        player.last_quest_date = str(data.get("last_quest_date") or "")
        player.quest_total = int(data.get("quest_total") or 0)
        player.show_date = str(data.get("show_date") or "")
        player.show_left = int(data.get("show_left", 3))
        player.show_pool = [Artist.from_dict(x) for x in (data.get("show_pool") or [])]
        player.market_date = str(data.get("market_date") or "")
        player.market_left = int(data.get("market_left", 3))
        player.market = [MarketProject.from_dict(x) for x in (data.get("market") or [])]
        player.shop_date = str(data.get("shop_date") or "")
        player.shop_stock = {
            str(k): dict(v) for k, v in (data.get("shop_stock") or {}).items()
        }
        player.asset_tier = int(data.get("asset_tier") or 0)
        player.last_interest_date = str(data.get("last_interest_date") or "")
        player.last_company_date = str(data.get("last_company_date") or "")
        player.active_quest = str(data.get("active_quest") or "")
        player.pending_notices = [
            {"text": str(n.get("text") or ""), "at": float(n.get("at") or 0)}
            for n in (data.get("pending_notices") or [])
            if isinstance(n, dict) and n.get("text")
        ]
        player.created_at = float(data.get("created_at") or U.now())
        player.updated_at = float(data.get("updated_at") or U.now())
        player.log = [str(x) for x in (data.get("log") or [])]
        return player

    def push_log(self, text: str) -> None:
        """写入一条简短日志（用于面板展示）。"""
        self.log.append(f"[{U.fmt_datetime(time.time())}] {text}")
        if len(self.log) > 60:
            self.log = self.log[-60:]

    # ---- 离线通知（主动推送失败时暂存，等玩家下次交互补发）----
    def push_notice(self, text: str, ts: float | None = None) -> None:
        """把一条通知放入待补发队列。"""
        text = (text or "").strip()
        if not text:
            return
        self.pending_notices.append({"text": text, "at": float(ts or U.now())})
        if len(self.pending_notices) > C.MAX_PENDING_NOTICES:
            self.pending_notices = self.pending_notices[-C.MAX_PENDING_NOTICES :]

    def take_notices(self, limit: int = C.NOTICE_BATCH) -> list[dict]:
        """取出至多 limit 条待补发通知（取出即出队）。"""
        if not self.pending_notices:
            return []
        limit = max(1, int(limit))
        taken = self.pending_notices[:limit]
        self.pending_notices = self.pending_notices[limit:]
        return taken

    def notice_count(self) -> int:
        return len(self.pending_notices)
