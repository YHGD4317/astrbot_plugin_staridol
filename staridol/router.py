"""指令解析与分发。

本插件以"自然语言式指令"为主（例如 ``xx去训练舞蹈``、``用1000w筹划综艺（项目名）``），
因此没有使用 AstrBot 的 ``@filter.command``，而是注册一个全局消息监听器，
用严格的正则逐条匹配；无法匹配的消息会原样放行，不影响其他插件与 LLM 对话。

全部文案不使用 emoji。
"""

from __future__ import annotations

import re
from typing import Any, Callable

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from . import constants as C
from . import render as R
from . import stocks as stock_sys
from . import utils as U
from .models import Company, Player
from .qqcard import CardSender
from .result import Result
from .store import GameStore
from .systems import artist as artist_sys
from .systems import daily as daily_sys
from .systems import economy as eco_sys
from .systems import project as project_sys
from .systems import quest as quest_sys

# --------------------------------------------------------------------------
# 文本规范化
# --------------------------------------------------------------------------
_FULLWIDTH = {
    ord(c): ord(c) - 0xFEE0
    for c in "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
}
_FULLWIDTH[ord("：")] = ord(":")
_FULLWIDTH[ord("，")] = ord(",")
_FULLWIDTH[ord("　")] = ord(" ")

#: 视为"占位符未替换"的标记
_PLACEHOLDER_MARKS = ("（", "(", "xx", "XX", "ｘ", "某集团", "集团名", "项目名", "艺人名", "金额", "公司名")


def normalize(text: str) -> str:
    """统一文本：去唤醒前缀、全角转半角、压缩空白。"""
    text = (text or "").strip()
    text = text.lstrip("/!！.。~～")
    text = text.translate(_FULLWIDTH)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_amount(raw: str) -> int:
    """解析金额：支持 ``1000``、``1000w``、``1000万``、``1.5亿``。"""
    if raw is None:
        return 0
    text = str(raw).strip().replace(",", "").replace("，", "")
    multi = 1
    if text.endswith(("亿", "e", "E")):
        multi = 10000
        text = text[:-1]
    elif text.endswith(("w", "W", "万")):
        multi = 1
        text = text[:-1]
    try:
        value = float(text)
    except ValueError:
        return 0
    return int(value * multi)


def parse_count(raw: str | None) -> int:
    """解析数量（个）。"""
    if not raw:
        return 1
    try:
        return max(1, int(float(raw)))
    except ValueError:
        return 1


def split_names(raw: str) -> list[str]:
    """按分隔符拆分艺人名。"""
    parts = re.split(r"[、,，\s]+", (raw or "").strip())
    return [p.strip("《》<>「」") for p in parts if p.strip("《》<>「」")]


def has_placeholder(text: str) -> bool:
    """判断文本中是否还留着菜单示例里的占位符。"""
    text = text or ""
    return any(mark in text for mark in _PLACEHOLDER_MARKS)


# --------------------------------------------------------------------------
# 正则片段
# --------------------------------------------------------------------------
TYPE_PATTERN = "|".join(sorted(C.PROJECT_TYPES.keys(), key=len, reverse=True))
ARTIST_ATTR_PATTERN = "|".join(C.ARTIST_ATTRS.keys())
PLAYER_ATTR_PATTERN = "|".join(C.PLAYER_ATTRS.keys())
ITEM_PATTERN = "|".join(sorted(C.ITEMS.keys(), key=len, reverse=True))
COMPANY_PATTERN = "|".join(sorted(C.COMPANY_TYPES.keys(), key=len, reverse=True))
NAME = r"[^\s,，、%]{1,12}"


class Router:
    """把聊天文本翻译成游戏动作。"""

    def __init__(
        self,
        store: GameStore,
        sender: CardSender,
        *,
        config: Any = None,
        logger_: Any = None,
        menu_manager: Any = None,
    ) -> None:
        self.store = store
        self.sender = sender
        self.config = config
        self.logger = logger_ or logger
        self.menu_manager = menu_manager
        self.rules: list[tuple[re.Pattern[str], str]] = self._build_rules()

    # ------------------------------------------------------------------
    # 规则表（顺序即优先级）
    # ------------------------------------------------------------------
    def _build_rules(self) -> list[tuple[re.Pattern[str], str]]:
        rules: list[tuple[re.Pattern[str], str]] = [
            # ---- 基础 ----
            (re.compile(r"^(?:指令菜单|菜单|帮助|玩法|help)$"), "menu"),
            (re.compile(r"^(?:改名|修改昵称|设置昵称)\s*$"), "rename_help"),
            (re.compile(r"^(?:改名|修改昵称|设置昵称)\s*(?P<name>[^\s,，、%]{1,16})$"), "rename"),
            (re.compile(r"^(?:登记|注册)\s*(?P<rest>.+)$"), "register"),
            (re.compile(r"^(?:信息|个人面板|系统面板|我的面板|我的信息|资产)$"), "player_panel"),
            (re.compile(r"^(?:集团面板|集团状态|集团信息)$"), "group_panel"),
            (re.compile(r"^(?:重开|重新开始|重置自己)$"), "reset_self"),
            (re.compile(r"^(?:未读消息|查看通知|消息记录|离线消息)$"), "notices"),
            (re.compile(r"^(?:同步机器人菜单|同步菜单|同步指令面板)$"), "sync_menu"),
            # ---- 艺人 ----
            (re.compile(r"^(?:今日秀场|今日选秀|秀场|选秀)$"), "show"),
            (re.compile(r"^(?:员工|员工列表|艺人列表|我的艺人)$"), "staff"),
            (re.compile(rf"^(?:查询|查)\s*(?P<name>{NAME})$"), "query_artist"),
            (re.compile(rf"^聘用\s*(?P<name>{NAME})$"), "hire"),
            (re.compile(rf"^(?:解聘|开除|解约)\s*(?P<name>{NAME})$"), "fire"),
            (re.compile(rf"^晋升\s*(?P<name>{NAME})$"), "promote"),
            (
                re.compile(rf"^(?P<name>{NAME}?)去(?:训练|练习|学习)(?P<attr>{ARTIST_ATTR_PATTERN})$"),
                "train",
            ),
            (re.compile(rf"^(?P<name>{NAME}?)去(?:休息|休假)$"), "rest"),
            # ---- 今日事务 ----
            (re.compile(r"^(?:今日行程|今日事务|事务|行程)$"), "quest"),
            (re.compile(r"^(?:事务进度|事务列表)$"), "quest_summary"),
            (re.compile(rf"^检定\s*(?P<attr>{PLAYER_ATTR_PATTERN})$"), "check"),
            # ---- 项目 ----
            (re.compile(r"^(?:商业活动|投资机会|项目市场)$"), "market"),
            (re.compile(r"^(?:项目面板|项目列表|我的项目|项目)$"), "project_panel"),
            (
                # 只写了投资额与类型、没写项目名：提示补全，避免误发示例
                re.compile(
                    rf"^用?\s*(?P<invest>[\d.]+)\s*[wW万]?\s*(?:筹划|立项|投资|启动)\s*"
                    rf"(?:{TYPE_PATTERN})\s*$"
                ),
                "plan_project_hint",
            ),
            (
                re.compile(
                    rf"^用?\s*(?P<invest>[\d.]+)\s*[wW万]?\s*(?:筹划|立项|投资|启动)\s*"
                    rf"(?:(?P<dur>\d+)\s*小时)?\s*(?P<type>{TYPE_PATTERN})\s*(?P<name>.+)$"
                ),
                "plan_project",
            ),
            (
                re.compile(
                    rf"^(?:筹划|立项)\s*(?P<type>{TYPE_PATTERN})\s*(?P<name>{NAME})"
                    rf"(?:\s*(?:投资|预算|花费)\s*(?P<invest>[\d.]+)\s*[wW万]?)?$"
                ),
                "plan_project",
            ),
            (
                re.compile(rf"^(?:(?P<name>{NAME}?)项目开始|项目开始|开始项目|开始拍摄|项目启动)$"),
                "start_project",
            ),
            (
                re.compile(rf"^(?P<artists>.{{1,40}}?)参加[《<]?(?P<project>[^》>]{{1,14}})[》>]?$"),
                "join_project",
            ),
            (re.compile(r"^投资\s*(?P<index>\d+)\s*号?(?:项目)?$"), "invest_market"),
            (re.compile(rf"^取消项目\s*(?P<name>{NAME})$"), "cancel_project"),
            # ---- 股市 ----
            (re.compile(r"^(?:持股|我的持股|持仓|股份)$"), "holdings"),
            (
                re.compile(rf"^(?:投资|买入)\s*(?P<name>{NAME})\s*(?P<shares>[\d.]+)\s*%$"),
                "buy_stock",
            ),
            (
                re.compile(rf"^卖出\s*(?P<name>{NAME})\s*(?P<shares>[\d.]+)\s*%$"),
                "sell_stock",
            ),
            (
                re.compile(
                    rf"^开放投资\s*(?P<name>{NAME})\s*[，,]?\s*单价\s*(?P<price>[\d.]+)\s*[wW万]?"
                    rf"\s*[，,]?\s*(?:放出|份额)\s*(?P<shares>\d+)\s*%?$"
                ),
                "open_invest",
            ),
            (re.compile(rf"^关闭投资\s*(?P<name>{NAME})$"), "close_invest"),
            # ---- 经济 ----
            (re.compile(r"^(?:银行账目|银行|银行面板)$"), "bank"),
            (re.compile(r"^(?:存款|存入|存)\s*(?P<amount>[\d.]+)\s*[wW万]?$"), "deposit"),
            (re.compile(r"^(?:取款|取出|取)\s*(?P<amount>[\d.]+)\s*[wW万]?$"), "withdraw"),
            (re.compile(r"^(?:领取利息|领利息|利息)$"), "interest"),
            (re.compile(r"^(?:升级信用|提升信用|信用升级)$"), "upgrade_credit"),
            (re.compile(r"^贷款\s*(?P<amount>[\d.]+)\s*[wW万]?$"), "loan"),
            (re.compile(r"^还款\s*(?P<amount>[\d.]+)\s*[wW万]?$"), "repay"),
            (re.compile(r"^(?:打开商城|商城|商店)$"), "shop"),
            (re.compile(r"^购买\s*(?P<count>\d+)?\s*个?\s*(?P<name>.+)$"), "buy"),
            (
                re.compile(rf"^使用\s*(?P<name>{ITEM_PATTERN})(?:\s*(?:给|对)\s*(?P<target>{NAME}))?$"),
                "use_item",
            ),
            (re.compile(rf"^创建\s*(?P<type>{COMPANY_PATTERN})$"), "create_company"),
            (re.compile(r"^(?:收取分红|领取分红|分红)$"), "dividend"),
            (re.compile(r"^(?:今日股市|股市|股价)$"), "stock"),
            # ---- 管理（需管理员）----
            (re.compile(r"^(?:备份列表|存档列表)$"), "backup_list"),
            (re.compile(r"^立即备份$"), "backup_now"),
            (re.compile(r"^恢复备份\s*(?P<name>\S+)$"), "backup_restore"),
            (re.compile(r"^重置游戏$"), "reset_game"),
        ]
        return rules

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    async def handle(self, event: AstrMessageEvent) -> Result | None:
        """尝试处理一条消息。返回 None 表示不是本插件的指令。"""
        text = normalize(event.message_str)
        if not text or len(text) > 80:
            return None

        handler_name = ""
        match: re.Match[str] | None = None
        for pattern, name in self.rules:
            m = pattern.match(text)
            if m:
                handler_name = name
                match = m
                break
        if match is None:
            return None

        handler: Callable[..., Any] | None = getattr(self, f"_cmd_{handler_name}", None)
        if handler is None:
            return None

        async with self.store.lock:
            player = self._ensure_player(event)
            if player is None:
                return Result.fail("无法识别你的账号，请稍后再试。")
            try:
                info = daily_sys.ensure_daily(self.store, player)
                result = await handler(player, event, match)
            except Exception as exc:
                self.logger.error(f"[staridol] 处理指令「{text}」失败：{exc}", exc_info=True)
                return Result.fail("指令处理时出现了一点问题，已经记录到日志了，请稍后重试或联系管理员。")
            if result is not None and not result.silent:
                # 跨天提示与离线消息（主动推送失败后暂存的结果）随本次回复一并送达
                prefix_parts: list[str] = []
                new_day = daily_sys.new_day_notice(info, player)
                if new_day:
                    prefix_parts.append(new_day)
                notices = player.take_notices()
                if notices:
                    prefix_parts.append(R.format_notices(notices))
                if prefix_parts:
                    prefix = "\n\n---\n\n".join(prefix_parts) + "\n\n---\n\n"
                    if result.card is not None:
                        result.card.markdown = prefix + result.card.markdown
                        result.card.plain = ""
                    else:
                        result.text = prefix + result.text
            self.store.mark_dirty()
            await self.store.save()
            return result

    # ------------------------------------------------------------------
    # 玩家
    # ------------------------------------------------------------------
    def _ensure_player(self, event: AstrMessageEvent) -> Player | None:
        uid = str(event.get_sender_id() or "").strip()
        if not uid:
            return None
        player = self.store.get(uid)
        name = event.get_sender_name() or "董事长"
        if player is None:
            # 首次出现：默认以 QQ 昵称作为展示名
            player = self.store.create_player(uid, name)
        elif not player.custom_name:
            # 未自定义昵称时，跟随 QQ 昵称更新；已自定义则保持不变
            player.name = name
        try:
            player.umo = event.unified_msg_origin
        except Exception:
            pass
        try:
            player.group_id = str(event.get_group_id() or "")
        except Exception:
            pass
        return player

    #: 点击登记按钮时填入输入框的示例集团名，玩家只需替换此名即可发送
    _REGISTER_SAMPLE_NAME = "星海集团"

    def _need_register(self, player: Player) -> Result:
        total = self.store.init_points
        per = total // 3
        rest = total - per * 2
        text = (
            "你还没有登记集团。\n"
            "发送「登记（集团名），决策50财商50口才50」即可创建集团"
            f"（初始 {C.DEFAULT_DAILY_QUEST} 项事务、初始资金 {U.fmt_money(1000)}），"
            "也可以直接点击下方按钮，把登记格式填入输入框，改好集团名即可发送。"
        )
        # 点击后把完整的登记样例填入输入框，玩家只需替换集团名
        sample = f"登记{self._REGISTER_SAMPLE_NAME}，决策{per}财商{per}口才{rest}"
        card = R.Card(markdown=text).with_buttons(
            [[R.button("填入登记格式", sample)]]
        )
        return Result.fail(text, card=card)

    # ------------------------------------------------------------------
    # 基础指令
    # ------------------------------------------------------------------
    async def _cmd_menu(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        lines = ["# 娱乐圈模拟器 · 指令菜单", ""]
        for section, items in C.MENU_SECTIONS:
            lines.append(f"**{section}**")
            for cmd, desc in items:
                lines.append(f"- `{cmd}`　{desc}")
            lines.append("")
        lines.append("> 括号中的内容需要替换成你自己的信息后再发送。")
        lines.append("> 卡片上的按钮可以直接点击，会自动把指令填进输入框。")
        return Result.success(card=R.Card(markdown="\n".join(lines)))

    async def _cmd_rename_help(
        self, player: Player, event: AstrMessageEvent, match: re.Match
    ) -> Result:
        """改名指令的用法与当前昵称提示。"""
        if player.custom_name:
            current = f"自定义昵称：**{player.name}**"
        else:
            current = f"当前昵称（来自 QQ）：**{player.name}**"
        return Result.success(
            text=(
                f"{current}\n"
                "发送「改名（新昵称）」即可自定义你在游戏中的昵称，例如：改名娱乐圈大佬"
            )
        )

    async def _cmd_rename(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        """玩家自行修改昵称。"""
        raw = (match.group("name") or "").strip("《》<>「」 ")
        if has_placeholder(raw) or not raw:
            return Result.fail("昵称不能包含占位符或为空，例如：改名娱乐圈大佬")
        old = player.name
        player.name = raw
        player.custom_name = raw
        return Result.success(text=f"好的，你的昵称已由「{old}」改为「{raw}」。（发送「信息」可查看）")

    async def _cmd_register(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if player.points_assigned:
            # 已自由加点过：按规则不回复
            return Result.quiet()
        rest = match.group("rest")
        # 以属性关键词作为分界切出集团名，避免非贪婪匹配只吃到第一个字
        name_match = re.match(
            rf"(?P<name>.*?)\s*(?:集团)?\s*[，,、\s]*"
            rf"(?P<attrs>(?:{PLAYER_ATTR_PATTERN}).*)$",
            rest,
        )
        if not name_match:
            name_match = re.match(rf"(?P<name>.+?)(?:集团)?\s*[，,、\s]*(?P<attrs>.*)$", rest)
        if not name_match:
            return Result.fail("格式：登记（集团名），决策50财商50口才50")
        company = (name_match.group("name") or "").strip("《》<>「」 ")
        attrs_text = name_match.group("attrs") or ""

        if not company or has_placeholder(company):
            return Result.fail(
                "请先把集团名替换成你自己的名称，例如：\n"
                "登记星海集团，决策50财商50口才50"
            )

        values: dict[str, int] = {}
        for cn, key in C.PLAYER_ATTRS.items():
            m = re.search(rf"{cn}\s*[:：]?\s*(\d+)", attrs_text)
            if m:
                values[key] = int(m.group(1))
        if not values:
            total_hint = self.store.init_points
            return Result.fail(
                "没有识别到属性分配。\n"
                "格式示例：「登记星海集团，决策50财商50口才50」\n"
                f"三项属性之和需要等于 {total_hint} 点。"
            )
        total = sum(values.values())
        if total != self.store.init_points:
            per = self.store.init_points // 3
            rest_points = self.store.init_points - per * 2
            return Result.fail(
                f"属性点总和为 {total}，需要正好 {self.store.init_points} 点。\n"
                f"例如：决策{per}财商{per}口才{rest_points}"
            )
        if any(v < 0 for v in values.values()):
            return Result.fail("属性点不能为负数。")

        player.company_name = f"{company}集团"
        for key, value in values.items():
            setattr(player, key, value)
        player.points_assigned = True
        player.cash = max(player.cash, self.store.init_money)

        if not player.companies:
            company_obj = Company(
                cid=U.new_id("c"),
                name=f"{company}{C.DEFAULT_COMPANY_NAME}",
                ctype="文娱公司",
                asset=0,
                stock_price=10.0,
                owner=player.uid,
            )
            company_obj.prev_price = company_obj.stock_price
            player.companies.append(company_obj)
        quest_sys.reset_quests(self.store, player, force=True)
        project_sys.ensure_daily_market(self.store, player)
        self.store.mark_dirty()

        lines = [
            f"# {player.company_name} 成立",
            f"**董事长**：{player.name}",
            "",
            f"**属性**：决策 {player.decision} · 财商 {player.finance} · 口才 {player.eloquence}",
            f"**初始资金**：{U.fmt_money(player.cash)}",
            f"**下属公司**：{player.companies[0].name}",
            f"**今日事务**：{len(player.quests)} 条",
            "",
            "接下来可以：",
            "- 发送「今日行程」处理事务赚取资金",
            "- 发送「今日秀场」招募艺人开始养成",
            "- 发送「指令菜单」查看全部玩法",
        ]
        return Result.success(card=R.Card(markdown="\n".join(lines)))

    async def _cmd_player_panel(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        holding_value = stock_sys.holding_value(self.store, player.uid)
        return Result.success(
            card=R.render_player_panel(
                player,
                self.store.tier_info(player),
                self.store.show_refresh,
                self.store.market_refresh,
                holding_value,
                player.notice_count(),
            )
        )

    async def _cmd_group_panel(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        return Result.success(card=R.render_group_panel(player, self.store.tier_info(player)))

    async def _cmd_reset_self(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        uid = player.uid
        self.store.remove_player(uid)
        return Result.success(
            text="你的存档已重置，发送「登记（集团名），决策50财商50口才50」重新开始。"
        )

    async def _cmd_notices(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        """查看未读消息（主动推送失败时暂存的内容）。"""
        notices = player.take_notices(limit=C.MAX_PENDING_NOTICES)
        if not notices:
            return Result.success(text="当前没有未读消息。")
        return Result.success(card=R.render_notices(notices))

    async def _cmd_sync_menu(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not self._check_admin(event):
            return Result.fail("只有管理员可以同步机器人菜单。")
        if self.menu_manager is None:
            return Result.fail("菜单同步组件未初始化，请检查插件是否正常加载。")
        summary = await self.menu_manager.sync(verbose=True)
        return Result.success(card=self.menu_manager.summary_text(summary))

    # ------------------------------------------------------------------
    # 艺人
    # ------------------------------------------------------------------
    async def _cmd_show(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        return artist_sys.refresh_show(self.store, player)

    async def _cmd_staff(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        return Result.success(card=R.render_staff_list(player))

    async def _cmd_query_artist(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        name = match.group("name")
        artist = player.find_artist(name)
        if artist is None:
            show_artist = next((a for a in player.show_pool if a.name == name), None)
            if show_artist is not None:
                return Result.fail(f"{name} 还在秀场中等待签约，发送「聘用{name}」即可签约。")
            return Result.fail(f"没有找到名为「{name}」的艺人。可发送「员工」查看名册。")
        return Result.success(card=R.render_artist_panel(artist))

    async def _cmd_hire(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        return artist_sys.hire(self.store, player, match.group("name"))

    async def _cmd_fire(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return artist_sys.fire(self.store, player, match.group("name"))

    async def _cmd_promote(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        artist = player.find_artist(match.group("name"))
        if artist is None:
            return Result.fail(f"名册中没有「{match.group('name')}」。")
        text = artist_sys.promote(self.store, player, artist)
        if not text:
            return Result.fail(f"{artist.name} 尚未满足晋升条件：{artist.level_progress()}")
        return Result.success(text=f"{text}")

    async def _cmd_train(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        name = match.group("name")
        artist = player.find_artist(name)
        if artist is None:
            return Result.fail(f"名册中没有「{name}」，可发送「员工」查看。")
        return artist_sys.train(self.store, player, artist, match.group("attr"))

    async def _cmd_rest(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        name = match.group("name")
        artist = player.find_artist(name)
        if artist is None:
            return Result.fail(f"名册中没有「{name}」，可发送「员工」查看。")
        return artist_sys.rest(self.store, player, artist)

    # ------------------------------------------------------------------
    # 今日事务
    # ------------------------------------------------------------------
    async def _cmd_quest(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        return quest_sys.show_quest(self.store, player)

    async def _cmd_quest_summary(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return quest_sys.summary(player)

    async def _cmd_check(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return quest_sys.do_check(self.store, player, match.group("attr"))

    # ------------------------------------------------------------------
    # 项目
    # ------------------------------------------------------------------
    async def _cmd_market(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        result = project_sys.refresh_market(self.store, player)
        if not result.ok:
            return result
        return Result.success(card=R.render_market(player, self.store.market_refresh))

    async def _cmd_project_panel(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        return Result.success(card=R.render_project_panel(player))

    async def _cmd_plan_project_hint(
        self, player: Player, event: AstrMessageEvent, match: re.Match
    ) -> Result:
        invest = parse_amount(match.group("invest"))
        return Result.fail(
            "还差一个项目名。请把项目名补在类型后面再发送，例如：\n"
            f"用{U.fmt_money(invest)}筹划综艺（你的项目名）\n\n"
            "项目名由你自己决定，同名项目会自动追加序号（如 河中仙2）。"
        )

    async def _cmd_plan_project(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        groups = match.groupdict()
        invest = parse_amount(groups.get("invest") or "0")
        ptype = groups.get("type") or ""
        name = (groups.get("name") or "").strip().strip("《》")
        # 玩家不再指定时长：立项后随机 1~8 小时（plan_project 内部处理）
        if has_placeholder(name):
            return Result.fail(
                "项目名里似乎还留着占位内容，请替换成你想要的名字后再发送。\n"
                f"例如：用{U.fmt_money(invest or 1000)}筹划{ptype}长夜列车"
            )
        if invest <= 0:
            return Result.fail("请指定投资额，例如「用1000w筹划综艺（项目名）」。")
        return project_sys.plan_project(self.store, player, invest, ptype, name)

    async def _cmd_start_project(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        return project_sys.start_project(self.store, player, (match.groupdict().get("name") or "").strip())

    async def _cmd_join_project(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        names = split_names(match.group("artists"))
        project_name = match.group("project").strip()
        if not names:
            return Result.fail("请指定要投放的艺人，例如「（艺人名）参加（项目名）」。")
        return project_sys.join_project(self.store, player, names, project_name)

    async def _cmd_invest_market(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        return project_sys.invest_market(self.store, player, int(match.group("index")))

    async def _cmd_cancel_project(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return project_sys.cancel_project(self.store, player, match.group("name"))

    # ------------------------------------------------------------------
    # 股市
    # ------------------------------------------------------------------
    async def _cmd_stock(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        return Result.success(card=stock_sys.render_market_panel(self.store, player))

    async def _cmd_holdings(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return Result.success(card=stock_sys.render_holdings(self.store, player))

    async def _cmd_buy_stock(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        return stock_sys.buy_shares(
            self.store, player, match.group("name"), float(match.group("shares"))
        )

    async def _cmd_sell_stock(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return stock_sys.sell_shares(
            self.store, player, match.group("name"), float(match.group("shares"))
        )

    async def _cmd_open_invest(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        return stock_sys.open_investment(
            self.store,
            player,
            match.group("name"),
            parse_amount(match.group("price")),
            int(match.group("shares")),
        )

    async def _cmd_close_invest(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return stock_sys.close_investment(self.store, player, match.group("name"))

    # ------------------------------------------------------------------
    # 经济
    # ------------------------------------------------------------------
    async def _cmd_bank(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return Result.success(card=R.render_bank(player))

    async def _cmd_deposit(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return eco_sys.deposit_money(self.store, player, parse_amount(match.group("amount")))

    async def _cmd_withdraw(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return eco_sys.withdraw_money(self.store, player, parse_amount(match.group("amount")))

    async def _cmd_interest(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return eco_sys.claim_interest(self.store, player)

    async def _cmd_upgrade_credit(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return eco_sys.upgrade_credit(self.store, player)

    async def _cmd_loan(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return eco_sys.take_loan(self.store, player, parse_amount(match.group("amount")))

    async def _cmd_repay(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return eco_sys.repay_loan(self.store, player, parse_amount(match.group("amount")))

    async def _cmd_shop(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        eco_sys.refresh_shop(self.store, player)
        return Result.success(card=R.render_shop(player))

    async def _cmd_buy(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        return eco_sys.buy_item(
            self.store,
            player,
            (match.group("name") or "").strip(),
            parse_count(match.group("count")),
        )

    async def _cmd_use_item(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return eco_sys.use_item(
            self.store,
            player,
            (match.group("name") or "").strip(),
            (match.groupdict().get("target") or "").strip(),
        )

    async def _cmd_create_company(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not player.points_assigned:
            return self._need_register(player)
        return eco_sys.create_company(self.store, player, match.group("type"))

    async def _cmd_dividend(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        return eco_sys.collect_dividend(self.store, player)

    # ------------------------------------------------------------------
    # 管理
    # ------------------------------------------------------------------
    @staticmethod
    def _check_admin(event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_admin())
        except Exception:
            return False

    async def _cmd_backup_list(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not self._check_admin(event):
            return Result.fail("只有管理员可以查看备份列表。")
        backups = self.store.list_backups()
        if not backups:
            return Result.success(text="暂无备份，可发送「立即备份」创建一份。")
        lines = ["# 备份列表", ""]
        for index, path in enumerate(backups, 1):
            size = path.stat().st_size if path.exists() else 0
            lines.append(f"{index}. `{path.name}`　{size / 1024:.1f} KB")
        lines.append("")
        lines.append("发送「恢复备份文件名」即可回滚。")
        return Result.success(card=R.Card(markdown="\n".join(lines)))

    async def _cmd_backup_now(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not self._check_admin(event):
            return Result.fail("只有管理员可以创建备份。")
        path = await self.store.create_backup()
        if path is None:
            return Result.fail("备份失败，请查看日志。")
        return Result.success(text=f"备份完成：`{path.name}`")

    async def _cmd_backup_restore(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not self._check_admin(event):
            return Result.fail("只有管理员可以恢复备份。")
        name = (match.group("name") or "").strip().strip("`")
        ok = await self.store.restore_backup(name)
        if not ok:
            return Result.fail(f"没有找到备份文件 `{name}`。")
        return Result.success(text=f"已从 `{name}` 恢复存档。")

    async def _cmd_reset_game(self, player: Player, event: AstrMessageEvent, match: re.Match) -> Result:
        if not self._check_admin(event):
            return Result.fail("只有管理员可以重置游戏数据。")
        await self.store.create_backup()
        count = len(self.store.players)
        self.store.players.clear()
        self.store.history_names.clear()
        self.store.group_bind.clear()
        await self.store.save(force=True)
        return Result.success(text=f"已清空全部存档（{count} 位玩家），清空前已自动备份。")
