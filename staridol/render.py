"""面板与卡片渲染。

每个渲染函数返回一个 :class:`Card`：
  * ``markdown`` —— 富文本版本（QQ 官方机器人会渲染成 Markdown 卡片）
  * ``plain``    —— 纯文本降级版本（其他平台使用，自动由 markdown 转换）
  * ``buttons``  —— 按钮定义（每行一个列表，元素为 ``(按钮文字, 点击后填入的指令)``）

全部文案不使用 emoji，只使用普通文本与排版符号。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import constants as C
from . import utils as U
from .models import Artist, Player, Project, Quest

#: 按钮文字的最大长度（QQ 官方限制较严格，统一裁剪）
MAX_BUTTON_LABEL = 16

#: 每日秀场默认次数（实际值由插件配置决定，这里仅用于展示）
SHOW_TOTAL = 3

#: 商业活动默认次数
MARKET_TOTAL = 3


@dataclass
class Card:
    """一条可发送的消息卡片。"""

    markdown: str = ""
    plain: str = ""
    buttons: list[list[tuple[str, str]]] = field(default_factory=list)

    def with_buttons(self, buttons: list[list[tuple[str, str]]]) -> Card:
        self.buttons = buttons
        return self

    def text_for(self, markdown_supported: bool) -> str:
        if markdown_supported:
            return self.markdown or self.plain
        return self.plain or md_to_plain(self.markdown)

    def has_buttons(self) -> bool:
        return any(row for row in self.buttons)


def plain_card(text: str) -> Card:
    """构造一个纯文本卡片。"""
    return Card(markdown=text, plain=text)


# --------------------------------------------------------------------------
# Markdown -> 纯文本
# --------------------------------------------------------------------------

_MD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^#{1,6}\s*", re.MULTILINE), ""),
    (re.compile(r"\*\*(.+?)\*\*", re.DOTALL), r"\1"),
    (re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", re.DOTALL), r"\1"),
    (re.compile(r"`(.+?)`", re.DOTALL), r"\1"),
    (re.compile(r"^>\s?", re.MULTILINE), ""),
    (re.compile(r"^---+$", re.MULTILINE), "--------"),
]


def md_to_plain(text: str) -> str:
    """把 Markdown 文本粗略转换为纯文本，供不支持 Markdown 的平台使用。"""
    result = text or ""
    for pattern, repl in _MD_PATTERNS:
        result = pattern.sub(repl, result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def clip_label(text: str, limit: int = MAX_BUTTON_LABEL) -> str:
    """裁剪按钮文字。"""
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def button(label: str, data: str) -> tuple[str, str]:
    """构造一个按钮定义。"""
    return clip_label(label), data


# --------------------------------------------------------------------------
# 通用片段
# --------------------------------------------------------------------------


def artist_attr_block(artist: Artist, compact: bool = False) -> str:
    """艺人的八项属性，两行展示。"""
    parts = [f"{cn}{artist.get(key)}" for cn, key in C.ARTIST_ATTRS.items()]
    if compact:
        return " · ".join(parts)
    half = (len(parts) + 1) // 2
    return " · ".join(parts[:half]) + "\n" + " · ".join(parts[half:])


def artist_status_block(artist: Artist, ts: float | None = None) -> str:
    """艺人体力条与状态。"""
    ts = ts if ts is not None else U.now()
    stamina = max(0, min(100, artist.stamina))
    bar = U.progress_bar(stamina, 100)
    return f"{bar} {stamina}/100　{artist.status_line(ts)}"


def player_asset_block(player: Player, tier: C.TierInfo | None = None) -> str:
    """玩家资产概览。"""
    lines = [
        f"**现金**：{U.fmt_money(player.cash)}　**存款**：{U.fmt_money(player.deposit)}",
        f"**总资产**：{U.fmt_money(player.total_asset)}",
    ]
    if player.loan:
        lines.append(f"**贷款**：{U.fmt_money(player.loan)}（利息按日计）")
    if tier and tier.next_threshold:
        lines.append(
            f"**档位**：{tier.tier} 档（下一档 {U.fmt_money(tier.next_threshold)}，"
            f"每日事务 {tier.quest_count} 条）"
        )
    elif tier:
        lines.append(f"**档位**：{tier.tier} 档（已满档，每日事务 {tier.quest_count} 条）")
    return "\n".join(lines)


def credit_tier(level: int) -> C.CreditTier:
    for tier in C.CREDIT_TIERS:
        if tier.level == level:
            return tier
    return C.CREDIT_TIERS[0]


def format_notices(notices: list[dict]) -> str:
    """把离线通知（主动推送失败后暂存的内容）渲染成一段文本。"""
    if not notices:
        return ""
    lines = [f"# 离线消息（{len(notices)} 条）", ""]
    for index, item in enumerate(notices, 1):
        at = float(item.get("at") or 0)
        stamp = U.fmt_datetime(at) if at else ""
        lines.append(f"**{index}. {stamp}**")
        lines.append(str(item.get("text") or ""))
        lines.append("")
    return "\n".join(lines).rstrip()


def render_notices(notices: list[dict]) -> Card:
    """离线消息卡片。"""
    return Card(markdown=format_notices(notices))


def _credit_text(player: Player) -> str:
    info = credit_tier(player.credit_level)
    return (
        f"{info.level} 级 {info.name}（存款日息 {info.deposit_rate * 100:.2f}%，"
        f"贷款额度 {U.fmt_money(info.loan_limit)}）"
    )


# --------------------------------------------------------------------------
# 玩家 / 集团面板
# --------------------------------------------------------------------------


def render_player_panel(
    player: Player,
    tier: C.TierInfo | None = None,
    show_total: int = SHOW_TOTAL,
    market_total: int = MARKET_TOTAL,
    holding_value: int = 0,
    notice_count: int = 0,
) -> Card:
    """董事长个人面板。"""
    quest_done = sum(1 for q in player.quests if q.done)
    quest_total = len(player.quests)
    running = player.running_projects()
    lines = [
        f"# {player.company_name or '未登记集团'} · 董事长面板",
        f"**董事长**：{player.name}",
        "",
        f"**属性**：决策 {player.decision} · 财商 {player.finance} · 口才 {player.eloquence}",
        player_asset_block(player, tier),
    ]
    if holding_value:
        lines.append(f"**持股市值**：{U.fmt_money(holding_value)}（发送「持股」查看明细）")
    if notice_count:
        lines.append(f"**未读消息**：{notice_count} 条（发送「未读消息」查看）")
    lines.extend(
        [
            f"**信用**：{_credit_text(player)}",
            f"**员工**：{len(player.artists)} 人　**项目**：{len(running)} 个进行中",
        ]
    )
    buffs = player.buff_desc()
    if buffs:
        lines.append("**增益**：" + "；".join(buffs))
    if player.items:
        item_text = "、".join(f"{name}×{count}" for name, count in player.items.items() if count)
        lines.append(f"**背包**：{item_text}")
    lines.append("")
    lines.append(
        f"**今日事务**：{quest_done}/{quest_total} 已完成　"
        f"**秀场次数**：{player.show_left}/{show_total}　"
        f"**商业活动次数**：{player.market_left}/{market_total}"
    )
    return Card(markdown="\n".join(lines))


def render_group_panel(player: Player, tier: C.TierInfo | None = None) -> Card:
    """集团面板。"""
    lines = [
        f"# {player.company_name or '未登记集团'}",
        f"**董事长**：{player.name}",
        "",
        player_asset_block(player, tier),
        "",
    ]

    lines.append(f"**公司**（{len(player.companies)}）")
    if player.companies:
        for company in player.companies:
            state = company.state_text
            state_text = f"　[{state}]" if state else ""
            lines.append(
                f"- {company.name}　股价 {company.stock_price:.2f}"
                f"（{company.change_text}）　资产 {U.fmt_money(company.asset)}{state_text}"
            )
            if company.open_invest:
                lines.append(
                    f"　开放投资中：{U.fmt_money(max(1, company.unit_price))}/1%"
                    f"　放出 {company.shares_offered}%　已售出 {company.shares_taken}%"
                )
    else:
        lines.append("- 暂无")

    lines.append("")
    lines.append(f"**员工**（{len(player.artists)}）")
    if player.artists:
        for artist in player.artists[:10]:
            lines.append(
                f"- {artist.level} {artist.name}　体力 {artist.stamina}/100　{artist.status_line()}"
            )
        if len(player.artists) > 10:
            lines.append(f"- 其余 {len(player.artists) - 10} 人可使用「员工」查看")
    else:
        lines.append("- 暂无，发送「今日秀场」招募艺人")

    lines.append("")
    lines.append(f"**项目**（{len(player.active_projects())}）")
    active = player.active_projects()
    if active:
        for project in active[:5]:
            lines.append(
                f"- 《{project.name}》{project.ptype}　"
                f"评分 {U.fmt_score(project.score)}　{project.progress_line()}"
            )
    else:
        lines.append("- 暂无，可发送「用1000w筹划综艺（项目名）」立项")

    return Card(markdown="\n".join(lines))


def render_artist_panel(artist: Artist) -> Card:
    """单个艺人面板。"""
    ts = U.now()
    lines = [
        f"# {artist.name} · {artist.level}",
        artist_status_block(artist, ts),
        "",
        f"**粉丝**：{U.fmt_number(artist.fans)}　**人气**：{artist.fame}　**口碑**：{artist.reputation}",
        "**属性**：",
        artist_attr_block(artist),
        f"**平均**：{artist.avg_attr:.1f}　**估值**：{U.fmt_money(artist.value)}　"
        f"**参演**：{artist.total_projects} 次",
    ]
    if artist.status == C.STATUS_PROJECT and artist.project_id:
        lines.append(f"**当前项目**：{artist.status_text}")
    if artist.level_up_ready():
        lines.append(
            f"**可晋升**：{artist.level} → {artist.level_up_ready()}（发送「晋升{artist.name}」）"
        )
    else:
        lines.append(f"**晋升进度**：{artist.level_progress()}")
    return Card(markdown="\n".join(lines))


def render_staff_list(player: Player) -> Card:
    """员工列表。"""
    lines = [f"# {player.company_name or '集团'} · 员工名册（{len(player.artists)}）"]
    if not player.artists:
        lines.append("暂无艺人，发送「今日秀场」开始招募。")
    else:
        for index, artist in enumerate(player.artists, 1):
            lines.append(
                f"{index}. **{artist.name}**（{artist.level}）体力 {artist.stamina}/100　"
                f"均属性 {artist.avg_attr:.0f}　粉丝 {U.fmt_number(artist.fans)}"
            )
            lines.append(f"　　{artist.status_line()}")
    lines.append("")
    lines.append("发送「查询艺人名」查看详细面板")
    return Card(markdown="\n".join(lines))


# --------------------------------------------------------------------------
# 秀场
# --------------------------------------------------------------------------


def render_show_pool(player: Player, show_total: int = SHOW_TOTAL) -> Card:
    """今日秀场候选列表（每位艺人附带一键签约按钮）。"""
    lines = [
        f"# 今日秀场（剩余 {player.show_left}/{show_total} 次）",
        "以下艺人正在等待签约，属性越高越值得投资。",
        "",
    ]
    if not player.show_pool:
        lines.append("本次秀场已经结束，发送「今日秀场」刷新新一批艺人。")
    for index, artist in enumerate(player.show_pool, 1):
        lines.append(f"**{index}. {artist.name}**（素人）")
        lines.append(artist_attr_block(artist, compact=True))
        lines.append(
            f"　签约金 {U.fmt_money(artist.salary * C.SIGN_FEE_RATE)}　估值 {U.fmt_money(artist.value)}"
        )
    lines.append("")
    lines.append("发送「聘用艺人名」签约。再次发送「今日秀场」会刷新候选（未签约的会消失）。")
    buttons = [
        [button(f"聘用{artist.name}", f"聘用{artist.name}")]
        for artist in player.show_pool[:5]
    ]
    return Card(markdown="\n".join(lines)).with_buttons(buttons)


# --------------------------------------------------------------------------
# 今日事务
# --------------------------------------------------------------------------


def render_quest_card(quest: Quest, player: Player, done_count: int, total: int) -> Card:
    """今日事务卡片（带检定按钮）。"""
    lines = [
        f"# 今日事务 {quest.index}/{total}（已完成 {done_count}）",
        f"**{quest.title}**",
        "",
        quest.desc,
        "",
        "点击下方按钮，输入框会自动填入检定指令，发送即可完成判定。",
    ]
    buttons: list[list[tuple[str, str]]] = []
    for index, option in enumerate(quest.options, 1):
        attr_cn = option.get("attr", "决策")
        label = option.get("label", "")
        buttons.append([button(f"{index}.{label}·{attr_cn}", f"检定{attr_cn}")])
    return Card(markdown="\n".join(lines)).with_buttons(buttons)


def render_quest_summary(player: Player) -> Card:
    """今日事务进度概览。"""
    done = [q for q in player.quests if q.done]
    todo = player.pending_quest()
    lines = [
        f"# 今日事务进度（{len(done)}/{len(player.quests)}）",
        "",
    ]
    for quest in player.quests:
        flag = "完成" if quest.done else "待办"
        lines.append(f"[{flag}] {quest.index}. {quest.title}")
        if quest.done and quest.result_text:
            lines.append(f"　　{quest.result_text}")
    if todo:
        lines.append("")
        lines.append(f"下一条待处理：**{todo.title}**，发送「今日行程」查看卡片。")
    else:
        lines.append("")
        lines.append("今日事务已全部完成，明天 0 点刷新。")
    return Card(markdown="\n".join(lines))


def render_check_result(
    result: U.CheckResult,
    player: Player,
    reward_text: str,
    extra_lines: list[str] | None = None,
) -> Card:
    """检定结果卡片。"""
    lines = [
        f"# {result.attr_name}检定 · {result.level_cn}",
        f"**骰点**：{result.roll} / {result.upper}　**属性**：{result.attr_value}",
        "",
        reward_text,
    ]
    if extra_lines:
        lines.extend([""] + extra_lines)
    lines.append("")
    lines.append(f"**现金**：{U.fmt_money(player.cash)}　**总资产**：{U.fmt_money(player.total_asset)}")
    return Card(markdown="\n".join(lines))


# --------------------------------------------------------------------------
# 项目
# --------------------------------------------------------------------------


def render_project_panel(player: Player) -> Card:
    """项目总览。"""
    lines = ["# 项目面板"]
    active = player.active_projects()
    history = [p for p in player.projects if p.status == C.PROJECT_DONE][-5:]
    if not active:
        lines.append("暂无进行中的项目。")
    for project in active:
        lines.append(f"**《{project.name}》**（{project.ptype}）")
        lines.append(
            f"　投资 {U.fmt_money(project.invest)}　评分 {U.fmt_score(project.score)}"
            f"　时长 {project.duration} 小时"
        )
        lines.append(f"　{project.progress_line()}")
        if project.artists:
            lines.append(
                f"　参与艺人：{'、'.join(project.artists)}　已发薪资 {U.fmt_money(project.salary_paid)}"
            )
        else:
            lines.append("　尚未投放艺人（发送「艺人名参加项目名」）")
        lines.append("")
    if history:
        lines.append("**最近结算**")
        for project in history:
            lines.append(
                f"- 《{project.name}》评分 {U.fmt_score(project.score)}　"
                f"收益 {U.fmt_signed(project.revenue - project.invest)}"
            )
    return Card(markdown="\n".join(lines))


def render_project_created(project: Project, breakdown: str = "") -> Card:
    """立项成功卡片。"""
    lines = [
        "# 项目已立项",
        f"**项目**：《{project.name}》",
        f"**类型**：{project.ptype}",
        f"**投资**：{U.fmt_money(project.invest)}（已托管，结算后返还）",
        f"**初始评分**：{U.fmt_score(project.score)}",
        f"**预计时长**：{project.duration} 小时",
    ]
    if breakdown:
        lines.append(f"**评分构成**：{breakdown}")
    lines.extend(
        [
            "",
            "发送「项目开始」启动拍摄/录制，随后可投放艺人提升评分。",
            f"> 评分 <3 亏损全部投资；3~5 返还一半；≥5 时每高 0.1 分多 1% 收益。"
            f"单个项目最多 {C.MAX_ARTISTS_PER_PROJECT} 位艺人。",
        ]
    )
    return Card(markdown="\n".join(lines)).with_buttons([[button("项目开始", "项目开始")]])


def render_project_started(project: Project) -> Card:
    """项目启动卡片。"""
    lines = [
        "# 项目已启动",
        f"**《{project.name}》**（{project.ptype}）已开始拍摄/录制。",
        f"预计 **{U.fmt_clock(project.end_at)}** 结束，可投放艺人提升评分。",
        f"当前评分：**{U.fmt_score(project.score)}**",
        "",
        f"发送「艺人名参加{project.name}」投放艺人（最多 {C.MAX_ARTISTS_PER_PROJECT} 人）。",
    ]
    return Card(markdown="\n".join(lines))


def render_market(player: Player, market_total: int = MARKET_TOTAL) -> Card:
    """商业活动列表。"""
    lines = [
        f"# 商业活动（剩余 {player.market_left}/{market_total} 次）",
        "以下为本次刷出的外部项目，投资即锁定资金，到期自动结算。",
        "再次发送「商业活动」会刷新列表，本次未投资的项目将不再出现。",
        "",
    ]
    if not player.market:
        lines.append("本次列表已结束，发送「商业活动」刷新新一批项目。")
    for index, item in enumerate(player.market, 1):
        flag = "（已投资）" if item.taken else ""
        lines.append(f"**{index}.《{item.name}》**{flag}")
        lines.append(
            f"　类型 {item.ptype}　时长 {item.duration} 小时　"
            f"预期评分 {U.fmt_score(item.score)}　需投资 {U.fmt_money(item.invest)}"
        )
    lines.append("")
    lines.append("发送「投资1号项目」进行投资。")
    buttons = [
        [button(f"投资{i}号", f"投资{i}号项目")]
        for i, item in enumerate(player.market[:5], 1)
        if not item.taken
    ]
    return Card(markdown="\n".join(lines)).with_buttons(buttons)


# --------------------------------------------------------------------------
# 经济
# --------------------------------------------------------------------------


def render_bank(player: Player) -> Card:
    """银行账目。"""
    info = credit_tier(player.credit_level)
    lines = [
        "# 银行账户",
        f"**现金**：{U.fmt_money(player.cash)}",
        f"**存款**：{U.fmt_money(player.deposit)}",
        f"**贷款**：{U.fmt_money(player.loan)}",
        "",
        f"**信用等级**：{info.level} 级 · {info.name}",
        f"**存款日息**：{info.deposit_rate * 100:.2f}%",
        f"**贷款日息**：{info.loan_rate * 100:.2f}%",
        f"**贷款额度**：{U.fmt_money(info.loan_limit)}（已用 {U.fmt_money(player.loan)}）",
        "",
        "发送「存/取款金额」「贷款金额」「还款金额」「领取利息」「升级信用」进行操作。",
    ]
    if player.last_interest_date == U.today_str():
        lines.append("今日利息已领取。")
    buttons = [
        [button("领取利息", "领取利息")],
        [button("升级信用", "升级信用")],
    ]
    return Card(markdown="\n".join(lines)).with_buttons(buttons)


def render_shop(player: Player) -> Card:
    """商城界面。"""
    lines = [
        "# 今日商城",
        "价格每日 0 点刷新。购买时会进行一次口才检定，通过可享 1~9 折优惠。",
        "",
    ]
    rows: list[list[tuple[str, str]]] = []
    for index, (name, info) in enumerate(player.shop_stock.items(), 1):
        template = C.ITEMS.get(name)
        if template is None:
            continue
        price = int(info.get("price", template.price))
        lines.append(f"**{index}. {name}**　{U.fmt_money(price)}")
        if template.desc:
            lines.append(f"　{template.desc}")
        rows.append([button(f"购买{name}", f"购买{name}")])
    lines.append("")
    lines.append("发送「购买道具名」或「购买2个道具名」下单。")
    return Card(markdown="\n".join(lines)).with_buttons(rows[:5])
