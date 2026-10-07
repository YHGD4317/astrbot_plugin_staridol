"""指令解析自测：用轻量 mock 替代 AstrBot 运行时，验证正则路由。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import types
from pathlib import Path

PLUGIN_DIR = Path(r"D:\AstrBot Launcher\.astrbot_launcher\instances\38bdbc12-a897-4343-9ca3-9303b0f8c6d4\core\data\plugins\astrbot_plugin_staridol")
sys.path.insert(0, str(PLUGIN_DIR))


# --------------------------------------------------------------------------
# mock astrbot
# --------------------------------------------------------------------------
def _install_mock() -> None:
    class _Logger:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = _Logger()

    class AstrBotConfig(dict):
        pass

    api.AstrBotConfig = AstrBotConfig

    event_mod = types.ModuleType("astrbot.api.event")

    class AstrMessageEvent:
        pass

    class MessageChain:
        def __init__(self, chain=None, **kwargs):
            self.chain = chain or []

        def use_markdown(self, value=True):
            return self

    class MessageEventResult(MessageChain):
        pass

    class _Filter:
        class EventMessageType:
            ALL = "all"

        @staticmethod
        def command(*a, **k):
            return lambda fn: fn

        @staticmethod
        def event_message_type(*a, **k):
            return lambda fn: fn

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain
    event_mod.MessageEventResult = MessageEventResult
    event_mod.filter = _Filter()

    components = types.ModuleType("astrbot.api.message_components")

    class Plain:
        def __init__(self, text=""):
            self.text = text

    components.Plain = Plain

    star_mod = types.ModuleType("astrbot.api.star")

    class Context:
        pass

    class Star:
        def __init__(self, context=None):
            self.context = context

    def register(*a, **k):
        def deco(cls):
            return cls

        return deco

    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.register = register

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.message_components"] = components
    sys.modules["astrbot.api.star"] = star_mod


_install_mock()

from staridol.router import Router, normalize, parse_amount, split_names  # noqa: E402
from staridol.store import GameStore  # noqa: E402

OK = 0
FAIL = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {extra}")


def main() -> None:
    store = GameStore(Path(tempfile.mkdtemp(prefix="staridol_router_")))
    router = Router(store, sender=None)

    cases: list[tuple[str, str | None]] = [
        # 基础
        ("登记星海集团，决策50财商50口才50", "register"),
        ("登记星海集团 决策 40 财商 60 口才 50", "register"),
        ("/登记星海集团，决策50财商50口才50", "register"),
        ("信息", "player_panel"),
        ("个人面板", "player_panel"),
        ("集团面板", "group_panel"),
        ("指令菜单", "menu"),
        ("菜单", "menu"),
        ("重开", "reset_self"),
        # 艺人
        ("今日秀场", "show"),
        ("今日选秀", "show"),
        ("聘用林星野", "hire"),
        ("解聘林星野", "fire"),
        ("查询林星野", "query_artist"),
        ("员工", "staff"),
        ("林星野去训练舞蹈", "train"),
        ("苏清和去练习唱功", "train"),
        ("林星野去训练口才", "train"),
        ("林星野去休息", "rest"),
        ("晋升林星野", "promote"),
        # 事务
        ("今日行程", "quest"),
        ("检定口才", "check"),
        ("检定 决策", "check"),
        ("检定财商", "check"),
        ("事务进度", "quest_summary"),
        # 项目
        ("用1000w筹划综艺今天吃什么", "plan_project"),
        ("用1000w筹划4小时综艺今天吃什么", "plan_project"),
        ("用 500 万 筹划 电视剧 长夜列车", "plan_project"),
        ("用1000w筹划综艺", "plan_project_hint"),
        ("项目开始", "start_project"),
        ("今天吃什么项目开始", "start_project"),
        ("林星野参加今天吃什么", "join_project"),
        ("林星野、苏清和、白露参加今天吃什么", "join_project"),
        ("林星野参加《今天吃什么》", "join_project"),
        ("项目面板", "project_panel"),
        ("商业活动", "market"),
        ("投资1号项目", "invest_market"),
        ("投资3号", "invest_market"),
        ("取消项目今天吃什么", "cancel_project"),
        # 股市
        ("今日股市", "stock"),
        ("股市", "stock"),
        ("持股", "holdings"),
        ("我的持股", "holdings"),
        ("投资长河影业5%", "buy_stock"),
        ("买入万象影视3%", "buy_stock"),
        ("卖出长河影业2%", "sell_stock"),
        ("开放投资星海影视公司，单价200w，放出30%", "open_invest"),
        ("开放投资星海影视公司 单价200 放出30", "open_invest"),
        ("关闭投资星海影视公司", "close_invest"),
        # 管理
        ("未读消息", "notices"),
        ("查看通知", "notices"),
        ("同步机器人菜单", "sync_menu"),
        ("备份列表", "backup_list"),
        ("立即备份", "backup_now"),
        ("恢复备份staridol_20240101_120000.json", "backup_restore"),
        ("重置游戏", "reset_game"),
        # 经济
        ("银行账目", "bank"),
        ("存款1000", "deposit"),
        ("存入 500w", "deposit"),
        ("取款1000", "withdraw"),
        ("领取利息", "interest"),
        ("升级信用", "upgrade_credit"),
        ("贷款5000", "loan"),
        ("还款1000", "repay"),
        ("打开商城", "shop"),
        ("购买神秘盲盒", "buy"),
        ("购买2个神秘盲盒", "buy"),
        ("使用幸运符", "use_item"),
        ("使用演技指导给林星野", "use_item"),
        ("使用能量饮料给苏清和", "use_item"),
        ("创建影视公司", "create_company"),
        ("收取分红", "dividend"),
        # 非游戏消息
        ("今天天气怎么样", None),
        ("在吗", None),
        ("哈哈哈哈", None),
        ("晚上吃什么好呢", None),
        ("你叫什么名字", None),
    ]

    print("=== 指令路由匹配 ===")
    for text, expected in cases:
        normalized = normalize(text)
        matched = None
        for pattern, name in router.rules:
            if pattern.match(normalized):
                matched = name
                break
        status = "PASS" if matched == expected else "FAIL"
        check(matched == expected, f"{text} → {matched}", f"期望 {expected}")
        if status == "FAIL":
            print(f"  {status} {text!r} → {matched}（期望 {expected}）")
    print(f"  共 {len(cases)} 条指令用例，通过 {OK}，失败 {FAIL}")

    # 参数解析
    print("\n=== 参数解析 ===")
    amount_cases = [
        ("1000", 1000),
        ("1000w", 1000),
        ("1000万", 1000),
        ("1.5亿", 15000),
        ("0.5", 0),
    ]
    for raw, expected in amount_cases:
        got = parse_amount(raw)
        ok = (got == expected) or (expected == 0 and got == 0) or (raw == "0.5" and got == 0)
        check(got == expected, f"parse_amount({raw}) = {got}", f"期望 {expected}")

    names = split_names("林星野、苏清和，白露 江晚")
    check(names == ["林星野", "苏清和", "白露", "江晚"], "split_names 分隔解析", str(names))

    # 关键分支：投资额解析
    m = None
    for pattern, name in router.rules:
        mm = pattern.match(normalize("用1000w筹划综艺今天吃什么"))
        if mm and name == "plan_project":
            m = mm
            break
    check(m is not None and parse_amount(m.group("invest")) == 1000, "筹划指令投资额 = 1000w")
    check(m is not None and m.group("type") == "综艺", "筹划指令类型 = 综艺")
    check(m is not None and m.group("name") == "今天吃什么", "筹划指令名称解析")

    m2 = None
    for pattern, name in router.rules:
        mm = pattern.match(normalize("使用演技指导给林星野"))
        if mm and name == "use_item":
            m2 = mm
            break
    check(
        m2 is not None and m2.group("name") == "演技指导" and m2.group("target") == "林星野",
        "道具与目标解析",
        str(m2.groups() if m2 else None),
    )

    # ------------------------------------------------------------------
    # 插件入口模块可导入性（使用 mock 的 AstrBot）
    # ------------------------------------------------------------------
    print("\n=== 插件入口加载 ===")
    # 规则与处理方法一一对应
    missing = [name for _, name in router.rules if not hasattr(router, f"_cmd_{name}")]
    check(not missing, "所有路由规则都有对应处理方法", str(missing))

    plugins_dir = PLUGIN_DIR.parent
    if str(plugins_dir) not in sys.path:
        sys.path.insert(0, str(plugins_dir))

    class _FakeLogger:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

    class _Ctx:
        def __init__(self):
            self.logger = _FakeLogger()

    try:
        import astrbot_plugin_staridol.main as plugin_main

        check(True, "main.py 可被导入")
        cls = plugin_main.StarIdolPlugin
        check(hasattr(cls, "on_message"), "注册了消息监听入口")
        check(hasattr(cls, "initialize"), "实现了 initialize")
        check(hasattr(cls, "terminate"), "实现了 terminate")
        instance = cls(_Ctx(), {})
        check(instance.router is None, "未初始化时 router 为空")
        check(instance._cfg("daily_quest_base", 5) == 5, "配置读取回退默认值")
        check(instance._cfg("init_money", 1000) == 1000, "配置读取 init_money")
        from astrbot_plugin_staridol.staridol.qqcard import CardSender, _build_keyboard

        kb = _build_keyboard([[("1.电话询问·口才", "检定口才")]])
        ok_btn = (
            kb is not None
            and kb["content"]["rows"][0]["buttons"][0]["action"]["type"] == 2
            and kb["content"]["rows"][0]["buttons"][0]["action"]["data"] == "检定口才"
        )
        check(ok_btn, "按钮 payload 结构正确（type=2 指令按钮）", str(kb))
        check(_build_keyboard([]) is None, "空按钮返回 None")
        sender = CardSender(_Ctx(), use_card=True)
        check(sender.use_card, "卡片发送器初始化")

        # 机器人菜单 / 指令面板 payload 合规性
        from astrbot_plugin_staridol.staridol.menus import BotMenuManager

        menu = BotMenuManager.build_menu_payload()
        items = menu["menu"]["items"]
        check(len(items) <= 10, "自定义菜单主项不超过 10 个", str(len(items)))
        check(all(len(i["name"]) <= 10 for i in items), "自定义菜单名称不超过 10 字符")
        sub_items = [s for i in items if i.get("type") == "menu" for s in i["sub_menu_items"]]
        check(all(len(s["name"]) <= 14 for s in sub_items), "子菜单名称不超过 14 字符")
        check(
            all(s.get("send_message") for s in sub_items if s["type"] == "send_message"),
            "子菜单消息项均带 send_message",
        )
        panel = BotMenuManager.build_panel_payload()
        panel_items = panel["panel"]["items"]
        check(len(panel_items) <= 20, "指令面板元素不超过 20 个", str(len(panel_items)))
        check(all(len(i["name"]) <= 14 for i in panel_items), "面板元素名不超过 14 字符")
        check(all(len(i["desc"]) <= 30 for i in panel_items), "面板元素描述不超过 30 字符")
        check(
            all("（" not in i["name"] and "(" not in i["name"] for i in panel_items),
            "指令面板元素名不含占位符",
        )
        check(
            all(i["type"] == "command" for i in panel_items),
            "指令面板元素均为指令类型",
        )
        menu_texts = " ".join(
            str(i.get("send_message", "")) for i in items
        ) + " ".join(str(s.get("send_message", "")) for s in sub_items)
        check("今天吃什么" not in menu_texts, "菜单中不含具体项目名示例")

        # 菜单同步在无平台环境下应安全返回
        manager = BotMenuManager(store, _Ctx(), enabled=True, logger=_FakeLogger())
        loop = asyncio.new_event_loop()
        try:
            summary = loop.run_until_complete(manager.sync())
        finally:
            loop.close()
        check(isinstance(summary, dict), "菜单同步返回摘要对象")
        check(summary.get("platforms") == 0, "无平台时同步结果为空且不抛异常")
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        check(False, f"main.py 导入失败：{exc}")

    print("\n" + "=" * 50)
    print(f"路由自测：通过 {OK}，失败 {FAIL}")
    print("=" * 50)
    if FAIL:
        sys.exit(1)


class FakeEvent:
    """模拟 AstrMessageEvent，用于跑通完整指令链路。"""

    def __init__(self, text: str, uid: str = "10001", name: str = "测试总裁", group: str = "grp1"):
        self.message_str = text
        self._uid = uid
        self._name = name
        self._group = group
        self.unified_msg_origin = f"qq_official:GroupMessage:{group}"
        self.stopped = False
        self.sent: list = []

    def get_sender_id(self) -> str:
        return self._uid

    def get_sender_name(self) -> str:
        return self._name

    def get_group_id(self) -> str:
        return self._group

    def get_platform_id(self) -> str:
        return "qq_official_inst"

    def get_platform_name(self) -> str:
        return "qq_official"

    def is_admin(self) -> bool:
        return False

    def stop_event(self) -> None:
        self.stopped = True

    def chain_result(self, chain):
        return chain

    def plain_result(self, text):
        return text

    async def send(self, result) -> None:
        self.sent.append(result)


async def integration() -> None:
    """完整链路：注册 → 事务 → 秀场 → 聘用 → 训练 → 项目 → 经济。"""
    global OK, FAIL
    print("\n=== 端到端指令链路 ===")
    store = GameStore(Path(tempfile.mkdtemp(prefix="staridol_e2e_")))
    await store.load()
    router = Router(store, sender=None)

    async def run(text: str, uid: str = "10001") -> object:
        event = FakeEvent(text, uid=uid)
        result = await router.handle(event)
        return result

    # 1. 未注册时核心指令给出引导
    result = await run("今日行程")
    check(result is not None and not result.ok, "未注册时提示先登记")
    check(
        "登记" in (result.text or "") + (result.card.markdown if result.card else ""),
        "引导文本包含登记示例",
    )

    # 2. 注册
    result = await run("登记星海集团，决策50财商50口才50")
    check(result is not None and result.ok, "登记成功")
    player = store.get("10001")
    check(player is not None and player.decision == 50 and player.eloquence == 50, "属性分配生效")
    check(player.points_assigned, "加点标记已记录")
    check(any("星海" in c.name for c in player.companies), "自动创建文娱公司")

    # 3. 重复登记 → 静默
    result = await run("登记另一个集团，决策10财商10口才10")
    check(result is not None and result.silent, "已加点后再次登记无回复")

    # 4. 今日行程 → 卡片带按钮
    result = await run("今日行程")
    check(result.card is not None and result.card.has_buttons(), "今日行程返回带按钮卡片")
    check(result.card.buttons[0][0][1].startswith("检定"), "按钮填入检定指令")

    # 5. 检定（本人）
    result = await run("检定口才")
    check(result.ok and result.card is not None, "检定返回结果卡片")
    check("检定" in result.card.markdown, "结果卡片包含检定描述")

    # 6. 他人检定 → 静默
    result = await run("检定口才", uid="20002")
    check(result is not None and result.silent, "他人无待办事务时静默")

    # 7. 秀场 → 聘用
    result = await run("今日秀场")
    check(result.card is not None and len(player.show_pool) == 5, "秀场抽取 5 人")
    star = player.show_pool[0].name
    result = await run(f"聘用{star}")
    check(result.ok and len(player.artists) == 1, f"聘用 {star} 成功")

    # 8. 训练
    result = await run(f"{star}去训练演技")
    check(result.ok and player.artists[0].status == "training", "训练安排成功")

    # 9. 项目全流程
    player.cash = 6000
    result = await run("用800w筹划电视剧长夜列车")
    check(result.ok, "立项成功", result.text)
    project = player.find_project("长夜列车")
    check(project is not None, "项目已创建")
    result = await run("项目开始")
    check(result.ok and project.status == "running", "项目已开始")
    player.artists[0].busy_until = 0
    player.artists[0].status = "idle"
    player.artists[0].stamina = 100
    result = await run(f"{star}参加长夜列车")
    check(result.ok, "艺人参加项目成功", result.text)
    result = await run("项目面板")
    check(result.card is not None and "长夜列车" in result.card.markdown, "项目面板包含项目")
    result = await run("商业活动")
    check(result.card is not None and len(player.market) == 5, "商业活动列表")
    check(player.market_left == 2, "商业活动消耗一次机会", str(player.market_left))
    result = await run("投资1号项目")
    check(result.ok, "投资外部项目", result.text)

    # 10.5 股市
    from astrbot_plugin_staridol.staridol import stocks as S

    S.refresh_daily(store, force=True)
    target = next(c for c in store.market_companies.values() if c.investable)
    player.cash = 50000
    result = await run("今日股市")
    check(result.card is not None and "今日股市" in result.card.markdown, "股市面板正常")
    check(target.name in result.card.markdown, "股市面板包含招标公司")
    result = await run(f"投资{target.name}5%")
    check(result.ok and target.holders.get(player.uid) == 5, "股市买入指令生效", result.text)
    result = await run("持股")
    check(result.ok and target.name in result.card.markdown, "持股面板显示持仓")
    result = await run(f"卖出{target.name}2%")
    check(result.ok and target.holders.get(player.uid) == 3, "股市卖出指令生效", result.text)
    own = player.find_company("星海文娱公司")
    result = await run(f"开放投资{own.name}，单价200w，放出30%")
    check(result.ok and own.open_invest, "开放自家公司投资", result.text)
    result = await run(f"关闭投资{own.name}")
    check(result.ok and not own.open_invest, "关闭自家公司投资")

    # 10. 银行 / 商城
    player.cash = 5000
    result = await run("存款2000")
    check(result.ok and player.deposit == 2000, "存款指令生效")
    result = await run("取款500")
    check(result.ok and player.deposit == 1500, "取款指令生效")
    result = await run("贷款300")
    check(result.ok and player.loan == 300, "贷款指令生效")
    result = await run("打开商城")
    check(result.card is not None, "商城卡片返回")
    item = next(iter(player.shop_stock))
    result = await run(f"购买{item}")
    check(result.ok and player.items.get(item, 0) >= 1, f"购买 {item} 成功", result.text)

    # 11. 员工 / 查询 / 面板
    for cmd, key in [
        ("员工", "员工"),
        (f"查询{star}", star),
        ("信息", "董事长面板"),
        ("集团面板", "星海集团"),
        ("今日股市", "股市"),
        ("事务进度", "今日事务进度"),
        ("指令菜单", "指令菜单"),
    ]:
        result = await run(cmd)
        text = (result.card.markdown if result.card else result.text) or ""
        check(result.ok and key in text, f"{cmd} 面板正常", text[:60])

    # 11.5 离线消息随回复补发
    player.pending_notices.clear()
    player.push_notice("离线通知：林星野训练完成")
    result = await run("信息")
    text = (result.card.markdown if result.card else result.text) or ""
    check("离线消息" in text and "训练完成" in text, "离线消息随下一次回复补发")
    check(player.notice_count() == 0, "补发后队列清空")
    result = await run("未读消息")
    text = (result.card.markdown if result.card else result.text) or ""
    check("没有未读消息" in text, "无消息时给出提示")

    # 12. 非指令消息放行
    result = await router.handle(FakeEvent("今天天气不错，适合出去走走"))
    check(result is None, "闲聊消息不拦截")

    # 13. 存档落盘
    await store.save(force=True)
    reloaded = GameStore(store.data_dir)
    await reloaded.load()
    check(reloaded.get("10001") is not None, "端到端数据可持久化")
    check(reloaded.get("10001").notice_count() == 0, "离线队列随存档持久化")

    print("\n" + "=" * 50)
    print(f"端到端自测：累计通过 {OK}，失败 {FAIL}")
    print("=" * 50)


if __name__ == "__main__":
    main()
    asyncio.run(integration())
    if FAIL:
        sys.exit(1)
