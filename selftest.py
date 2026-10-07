"""娱乐圈模拟器 —— 核心玩法自测（不依赖 AstrBot 运行时）。

运行：python selftest.py
"""

from __future__ import annotations

import asyncio
import random
import sys
import tempfile
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PLUGIN_DIR))

from staridol import constants as C  # noqa: E402
from staridol import render as R  # noqa: E402
from staridol import stocks as S  # noqa: E402
from staridol import utils as U  # noqa: E402
from staridol.models import Artist, Company  # noqa: E402
from staridol.store import GameStore  # noqa: E402
from staridol.systems import artist as A  # noqa: E402
from staridol.systems import daily as D  # noqa: E402
from staridol.systems import economy as E  # noqa: E402
from staridol.systems import project as P  # noqa: E402
from staridol.systems import quest as Q  # noqa: E402

OK = 0
FAIL = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {extra}")


def brief(text: str, limit: int = 300) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + " ..."


async def main() -> None:
    random.seed(20240607)
    tmp = Path(tempfile.mkdtemp(prefix="staridol_"))
    store = GameStore(tmp, init_money=1000, init_points=150, show_refresh=3, market_refresh=3)
    await store.load()

    # ---------------------------------------------------------------- 注册
    print("\n=== 1. 注册集团 ===")
    player = store.create_player("10001", "测试总裁")
    player.company_name = "星海集团"
    player.decision, player.finance, player.eloquence = 45, 75, 30
    player.points_assigned = True
    company = Company(
        cid="c1", name="星海文娱公司", ctype="文娱公司", asset=0, stock_price=10.0, owner="10001"
    )
    company.prev_price = 10.0
    player.companies.append(company)

    info = D.ensure_daily(store, player)
    check(len(player.quests) == 5, "初始每日事务为 5 条", f"实际 {len(player.quests)}")
    check(player.cash == 1000, "初始资金 1000w", str(player.cash))
    check(player.show_left == 3, "秀场次数 3 次", str(player.show_left))
    check(player.market_left == 3, "商业活动次数 3 次", str(player.market_left))
    check(len(store.market_companies) == 10, "股市初始化 10 家独立公司", str(len(store.market_companies)))

    # ------------------------------------------------------- 评分公式校验
    print("\n=== 2. 评分公式校验（对齐策划案示例）===")
    check(abs(P.calc_base_score(player, "电视剧") - 0.6) < 1e-9, "电视剧基础评分 = 0.6")
    check(abs(P.calc_base_score(player, "访谈") - 0.5) < 1e-9, "访谈基础评分 ≈ 0.5")

    demo = Artist(aid="t1", name="示例艺人")
    demo.attrs = {
        "eloquence": 40, "acting": 50, "singing": 32, "dance": 21,
        "eq": 41, "iq": 22, "martial": 1, "art": 2,
    }
    check(P.calc_artist_score(demo, "电视剧") == 0.2, "电视剧艺人加分 = 0.2")
    check(P.calc_artist_score(demo, "真人秀") == 0.2, "真人秀艺人加分 = 0.2")
    check(P.calc_revenue(1000, 2.1) == 0, "评分 2.1 收益为 0")
    check(P.calc_revenue(1000, 3.7) == 500, "评分 3.7 返还 500w")
    check(P.calc_revenue(1000, 6.5) == 1150, "评分 6.5 回收 1150w")
    check(P.calc_revenue(1000, 20.0) == 2500, "评分 20 回收 2500w（评分不设上限）")
    check(P.calc_revenue(1000, 50.0) == 5500, "评分 50 回收 5500w")
    check(not hasattr(C, "MAX_SCORE"), "常量中已无评分上限")
    check(C.MAX_ARTISTS_PER_PROJECT == 6, "单个项目艺人上限为 6")

    # ------------------------------------------------------------ 检定规则
    print("\n=== 3. 检定规则校验 ===")
    r = U.roll_check("口才", 50)
    check(1 <= r.roll <= 100, "属性 <100 时骰点范围 1-100", str(r.roll))
    r = U.roll_check("口才", 120)
    check(1 <= r.roll <= 180, "属性 120 时骰点上限 180", f"{r.roll}/{r.upper}")
    check(r.crit_success == (r.roll <= 12), "大成功线 = 属性*0.1")

    # ---------------------------------------------------------------- 秀场
    print("\n=== 4. 秀场与聘用 ===")
    result = A.refresh_show(store, player)
    check(result.ok and len(player.show_pool) == 5, "秀场抽取 5 位艺人")
    check(player.show_left == 2, "秀场次数消耗 1 次")
    star = max(player.show_pool, key=lambda a: a.avg_attr)
    result = A.hire(store, player, star.name)
    check(result.ok and len(player.artists) == 1, "聘用成功并进入员工名册")
    check(player.cash == 1000 - star.salary, "扣除签约金", f"cash={player.cash}")
    check(not A.hire(store, player, "不存在的人").ok, "聘用不存在的艺人被拒绝")

    # ---------------------------------------------------------------- 训练
    print("\n=== 5. 训练（属性 +1 / 体力 -5 / 1 小时）===")
    artist = player.artists[0]
    artist.attrs["dance"] = 45
    artist.stamina = 100
    result = A.train(store, player, artist, "舞蹈")
    check(result.ok, "训练指令成功")
    check(artist.stamina == 95, "体力 -5", str(artist.stamina))
    check(A.training_cost(artist, "dance") == 10, "属性 <50 单点 10w")
    check(not A.train(store, player, artist, "舞蹈").ok, "训练中不可重复安排")
    artist.busy_until = U.now() - 1
    A.finish_training(player, artist)
    check(artist.get("dance") in (46, 47), "训练完成后属性提升")
    check(artist.status == C.STATUS_IDLE, "训练结束后状态复位")

    artist.attrs["dance"] = 55
    check(A.training_cost(artist, "dance") == 30, "属性 50~70 单点 30w")
    artist.attrs["dance"] = 75
    check(A.training_cost(artist, "dance") == 50, "属性 70~90 单点 50w")
    artist.attrs["dance"] = 95
    check(A.training_cost(artist, "dance") == 100, "属性 90+ 单点 100w")
    artist.attrs["dance"] = 45

    # ------------------------------------------------------------ 今日事务
    print("\n=== 6. 今日事务与按钮卡片 ===")
    result = Q.show_quest(store, player)
    card = result.card
    check(card is not None and card.has_buttons(), "事务卡片包含按钮")
    check(len(card.buttons) == 3, "每条事务 3 个按钮")
    check(card.buttons[0][0][1].startswith("检定"), "按钮填入检定指令", card.buttons[0][0][1])
    stranger = store.create_player("20002", "路人")
    check(Q.do_check(store, stranger, "口才").silent, "他人点击按钮时静默不回复")
    check(Q.do_check(store, player, "财商").ok, "本人检定正常执行")
    check(sum(1 for q in player.quests if q.done) == 1, "事务完成计数 +1")

    # ------------------------------------------------------------ 项目流程
    print("\n=== 7. 项目：立项 -> 开始 -> 投放艺人 -> 结算 ===")
    player.cash = 5000
    result = P.plan_project(store, player, 1000, "综艺", "今天吃什么", duration=4)
    check(result.ok, "立项成功", result.text)
    project = player.find_project("今天吃什么")
    check(project is not None and project.invest == 1000, "项目投资 1000w")
    check(player.cash == 4000, "投资额已托管扣除")
    check(
        abs(project.score - U.round_score(P.calc_base_score(player, "综艺") + P.calc_production_bonus(1000))) < 1e-9,
        "初始评分 = 基础分 + 制作加成",
        str(project.score),
    )

    # 重名自动加序号
    result = P.plan_project(store, player, 100, "综艺", "今天吃什么", duration=2)
    check(result.ok and player.find_project("今天吃什么2") is not None, "重名项目自动命名 今天吃什么2")

    result = P.start_project(store, player, "今天吃什么")
    check(result.ok and project.status == C.PROJECT_RUNNING, "项目已开始")
    check(abs(project.end_at - project.start_at - 4 * 3600) < 1, "时长为 4 小时")

    artist.stamina = 100
    artist.status = C.STATUS_IDLE
    artist.busy_until = 0
    salary_before = player.cash
    result = P.join_project(store, player, [artist.name], "今天吃什么")
    check(result.ok, "艺人加入项目", result.text)
    check(player.cash == salary_before - artist.salary, "已发放艺人薪资")
    check(artist.status == C.STATUS_PROJECT, "艺人状态变为参加项目中")
    check(not P.join_project(store, player, [artist.name], "今天吃什么").ok, "同一艺人不可重复参加")

    # 参演上限 6 人
    for i in range(8):
        helper = Artist(aid=f"x{i}", name=f"替补{i}", level="素人")
        helper.attrs = {k: 60 for k in C.ARTIST_ATTRS.values()}
        helper.stamina = 100
        player.artists.append(helper)
    P.join_project(store, player, [f"替补{i}" for i in range(8)], "今天吃什么")
    check(
        len(project.artists) == C.MAX_ARTISTS_PER_PROJECT,
        "单个项目最多 6 位艺人",
        str(len(project.artists)),
    )

    # 项目体力按小时消耗
    print("\n=== 8. 项目期间按小时消耗体力 ===")
    project_artist = next(a for a in player.artists if a.aid == artist.aid)
    project_artist.stamina = 100
    project_artist.project_stamina_at = U.now()
    spent = A.tick_project_stamina(project_artist, U.now() + 3600 * 3)
    check(spent == 3 * C.PROJECT_STAMINA_PER_HOUR, "3 小时消耗 24 点体力", str(spent))
    check(project_artist.stamina == 100 - 3 * C.PROJECT_STAMINA_PER_HOUR, "体力正确扣减")
    extra = A.tick_project_stamina(project_artist, U.now() + 3600 * 3 + 120)
    check(extra == 0, "不足 1 小时不扣体力", str(extra))

    # 结算
    fans_before = project_artist.fans
    fame_before = project_artist.fame
    project.end_at = U.now() - 1
    expected_revenue = P.calc_revenue(project.invest, project.score)
    cash_before_settle = player.cash
    text = P.settle_project(store, player, project)
    check(project.settled and project.status == C.PROJECT_DONE, "项目已结算")
    check(
        player.cash == cash_before_settle + expected_revenue,
        "项目收益按公式到账",
        f"{player.cash} != {cash_before_settle}+{expected_revenue}",
    )
    check(project_artist.fans > fans_before, "项目结束后粉丝增加", f"{fans_before}->{project_artist.fans}")
    check(project_artist.fame >= fame_before, "项目结束后人气增加", f"{fame_before}->{project_artist.fame}")
    check(project_artist.status == C.STATUS_IDLE, "结算后艺人恢复待命")
    print("  " + brief(text, 420).replace("\n", " | "))

    # 高评分不再受上限约束
    big_artist = Artist(aid="big", name="顶流甲", level="顶流")
    big_artist.attrs = {k: 200 for k in C.ARTIST_ATTRS.values()}
    check(P.calc_artist_score(big_artist, "电视剧") == 2.0, "单项艺人加分可超过 1.0", str(P.calc_artist_score(big_artist, "电视剧")))

    # ------------------------------------------------------------ 商业活动
    print("\n=== 9. 商业活动（每日 3 次，刷新即作废）===")
    player.cash = 20000
    result = P.refresh_market(store, player)
    check(result.ok and len(player.market) == C.MARKET_PROJECT_COUNT, "商业活动刷新 5 个项目")
    check(player.market_left == 2, "商业活动次数消耗 1 次", str(player.market_left))
    first_names = [m.name for m in player.market]
    result = P.refresh_market(store, player)
    check(result.ok, "可再次刷新")
    check(player.market_left == 1, "次数继续减少")
    second_names = [m.name for m in player.market]
    check(first_names != second_names, "刷新后列表内容变化")
    check(
        all(m.taken is False for m in player.market),
        "刷新后的项目都是未投资状态",
    )
    result = P.refresh_market(store, player)
    check(result.ok and player.market_left == 0, "第三次刷新后次数归零")
    check(not P.refresh_market(store, player).ok, "次数用完后无法刷新")
    result = P.invest_market(store, player, 1)
    check(result.ok, "投资第 1 号项目成功", result.text)
    check(P.invest_market(store, player, 1).ok is False, "同一项目不可重复投资")
    external = player.projects[-1]
    check(external.external, "外部项目标记正确")
    P.settle_project(store, player, external)
    check(external.settled, "外部项目可结算")

    # ------------------------------------------------------------ 经济
    print("\n=== 10. 银行 / 商城 ===")
    player.cash = 5000
    check(E.deposit_money(store, player, 3000).ok and player.deposit == 3000, "存款 3000w")
    check(E.claim_interest(store, player).ok and player.deposit > 3000, "领取利息成功")
    check(not E.claim_interest(store, player).ok, "同日不可重复领取利息")
    for _ in range(6):
        player.deposit += 5000
        E.upgrade_credit(store, player)
    check(player.credit_level >= 2, "信用等级可提升", str(player.credit_level))
    check(E.take_loan(store, player, 300).ok and player.loan == 300, "贷款成功")
    check(E.repay_loan(store, player, 100).ok and player.loan == 200, "还款成功")

    E.refresh_shop(store, player, force=True)
    check(len(player.shop_stock) == C.SHOP_ITEM_COUNT, "商城上架 8 种道具")
    player.shop_stock["神秘盲盒"] = {"price": 40, "base": 40}
    player.cash = 2000
    check(E.buy_item(store, player, "神秘盲盒", 2).ok, "购买盲盒成功")
    check(player.items.get("神秘盲盒", 0) == 2, "盲盒进入背包")
    check(E.use_item(store, player, "神秘盲盒").ok, "盲盒可使用")
    player.items["演技指导"] = 1
    act_before = artist.get("acting")
    check(E.use_item(store, player, "演技指导", artist.name).ok, "艺人属性道具生效")
    player.items["演技指导"] = 1
    check(not E.use_item(store, player, "演技指导").ok, "艺人道具未指定对象时报错")

    # 招商手册恢复商业活动次数
    player.items["招商手册"] = 1
    before_left = player.market_left
    result = E.use_item(store, player, "招商手册")
    check(result.ok and player.market_left == before_left + 1, "招商手册恢复商业活动次数", result.text)

    discounts = {E.roll_discount(player)[0] for _ in range(200)}
    check(discounts.issubset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10}), "折扣仅取整数档")
    check(max(discounts) == 10 and min(discounts) < 10, "折扣包含无折扣与折扣两种情形")

    # ------------------------------------------------------------ 股市
    print("\n=== 11. 股市：招标 / 买入 / 卖出 / 玩家公司开放投资 ===")
    trader = store.get("10001")
    trader.cash = 100000
    S.refresh_daily(store, force=True)
    open_companies = [c for c in store.market_companies.values() if c.investable]
    check(
        C.MARKET_OPEN_MIN <= len(open_companies) <= C.MARKET_OPEN_MAX,
        f"每日随机开放 {C.MARKET_OPEN_MIN}~{C.MARKET_OPEN_MAX} 家公司招标",
        str(len(open_companies)),
    )
    check(
        all(c.quota == C.STOCK_DAILY_QUOTA for c in open_companies),
        "招标公司当日额度为 10%",
    )
    closed = [c for c in store.market_companies.values() if not c.open]
    check(all(not c.state_text for c in closed), "未开放公司无状态标注")
    check(all(c.state_text == "招标中" for c in open_companies), "开放公司标注招标中")

    target = open_companies[0]
    unit = target.unit_price
    cash_before = trader.cash
    result = S.buy_shares(store, trader, target.name, 4)
    check(result.ok, "买入 4% 股份成功", result.text)
    check(trader.cash == cash_before - 4 * unit, "按单价扣款", f"{trader.cash} vs {cash_before - 4 * unit}")
    check(target.holders.get("10001") == 4, "持股登记正确")
    check(target.quota == C.STOCK_DAILY_QUOTA - 4, "当日额度相应减少")

    result = S.buy_shares(store, trader, target.name, 12)
    check(not result.ok, "单次超过 10% 被拒绝", result.text)
    result = S.buy_shares(store, trader, target.name, 6)
    check(result.ok and target.quota == 0, "买满当日 10% 额度")
    result = S.buy_shares(store, trader, target.name, 1)
    check(not result.ok, "额度用完后停止该次投资", result.text)

    # 多家玩家可共同投资
    trader2 = store.get("20002")
    trader2.cash = 100000
    target.quota = 10
    result = S.buy_shares(store, trader2, target.name, 3)
    check(result.ok, "同一家公司可被多位玩家投资")
    check(target.holders.get("20002") == 3, "第二位投资者持股登记")
    check(target.issued == 13, "已发行份额统计正确", str(target.issued))

    # 卖出
    cash_before = trader.cash
    result = S.sell_shares(store, trader, target.name, 2)
    check(result.ok, "卖出 2% 成功", result.text)
    expected_income = int(2 * target.unit_price * C.STOCK_SELL_RATE)
    check(trader.cash == cash_before + expected_income, "卖出按 85% 折价到账")
    check(target.holders.get("10001") == 8, "卖出后持股减少", str(target.holders.get("10001")))
    check(not S.sell_shares(store, trader, target.name, 99).ok, "卖出超过持仓被拒绝")

    # 100% 被持有后不再开放
    for c in store.market_companies.values():
        c.open = False
        c.quota = 0
    target.holders = {"10001": 100}
    S.refresh_daily(store, force=True)
    check(not target.open, "100% 被持有后不再开放投资")
    check(target.remaining == 0, "剩余可投资份额为 0")

    # 玩家公司开放投资
    own = trader.find_company("星海文娱公司")
    result = S.open_investment(store, trader, own.name, 200, 30)
    check(result.ok, "开放自家公司投资成功", result.text)
    check(own.open_invest and own.unit_price == 200 and own.shares_offered == 30, "开放参数已记录")
    check(own.state_text == "招标中", "自家公司标注招标中")

    result = S.open_investment(store, trader2, own.name, 100, 10)
    check(not result.ok, "非创建者无法修改开放设置", result.text)
    result = S.open_investment(store, trader, own.name, 999999, 10)
    check(not result.ok, "单价超出范围被拒绝")

    owner_cash = trader.cash
    result = S.buy_shares(store, trader2, own.name, 5)
    check(result.ok, "其他玩家可投资玩家公司", result.text)
    check(trader.cash == owner_cash + 5 * 200, "投资款归创建者所有", f"{trader.cash} vs {owner_cash + 1000}")
    check(own.holders.get("20002") == 5, "玩家公司持股登记")

    info = S.holdings_of(store, "20002")
    check(any(name == own.name for name, _s, _p in info), "持股查询包含玩家公司")
    check(S.holding_value(store, "20002") > 0, "持股市值可计算")

    result = S.close_investment(store, trader, own.name)
    check(result.ok and not own.open_invest, "关闭投资成功")
    result = S.buy_shares(store, trader2, own.name, 1)
    check(not result.ok, "关闭后无法继续买入")

    panel = S.render_market_panel(store, trader)
    check("今日股市" in panel.markdown, "股市面板可渲染")
    holdings_card = S.render_holdings(store, trader)
    check("我的持股" in holdings_card.markdown, "持股面板可渲染")

    # 重开时释放持股
    store.remove_player("20002")
    check(own.holders.get("20002") is None, "玩家重开后其持股被释放")

    # ------------------------------------------------------------ 每日刷新
    print("\n=== 12. 每日刷新与档位 ===")
    player.last_quest_date = "1970-01-01"
    player.show_date = "1970-01-01"
    player.market_date = "1970-01-01"
    player.shop_date = "1970-01-01"
    player.asset_tier = 0
    player.cash = 100000
    info = D.ensure_daily(store, player)
    check(info["new_day"], "识别到跨天")
    check(player.show_left == 3, "秀场次数已重置", str(player.show_left))
    check(player.market_left == 3, "商业活动次数已重置", str(player.market_left))
    check(info["market_reset"], "刷新摘要标记商业活动重置")
    check(store.tier_info(player).tier >= 4, "总资产档位提升")
    notice = D.new_day_notice(info, player)
    check("新的一天" in notice and "商业活动" in notice, "跨天提示包含商业活动")
    tier_before = player.asset_tier
    player.cash = 100
    player.deposit = 0
    check(store.tier_info(player).tier == tier_before, "资产下滑后档位不回退")

    # ------------------------------------------------------------ 离线通知队列
    print("\n=== 13. 离线通知队列（推送失败补发）===")
    player.pending_notices.clear()
    player.push_notice("第一条：训练完成")
    player.push_notice("第二条：项目结算")
    check(player.notice_count() == 2, "通知入队计数正确", str(player.notice_count()))
    taken = player.take_notices(limit=1)
    check(len(taken) == 1 and "第一条" in taken[0]["text"], "按批次取出通知")
    check(player.notice_count() == 1, "取出后队列减少")
    for i in range(C.MAX_PENDING_NOTICES + 10):
        player.push_notice(f"溢出测试{i}")
    check(
        player.notice_count() == C.MAX_PENDING_NOTICES,
        f"队列长度上限为 {C.MAX_PENDING_NOTICES}",
        str(player.notice_count()),
    )
    check("溢出测试9" not in player.pending_notices[0]["text"], "超出上限时丢弃最旧的通知")
    player.pending_notices.clear()

    from staridol import notify as N

    check("always_queue" in N.NOTIFY_MODES and "push_first" in N.NOTIFY_MODES, "投递模式枚举完整")

    class _FakeSender:
        def __init__(self, ok: bool):
            self.ok = ok
            self.calls = 0

        async def push(self, umo, card):
            self.calls += 1
            return self.ok

    player.umo = "qq_official:GroupMessage:grp1"
    queued = await N.deliver_one(
        _FakeSender(True), player, R.Card(markdown="排队消息"), mode="always_queue"
    )
    check(queued and player.notice_count() == 1, "always_queue 直接入队")

    player.pending_notices.clear()
    fake = _FakeSender(True)
    queued = await N.deliver_one(fake, player, R.Card(markdown="推送成功"), mode="push_first")
    check(not queued and player.notice_count() == 0, "推送成功时不入队")
    check(fake.calls == 1, "调用了主动推送")

    queued = await N.deliver_one(
        _FakeSender(False), player, R.Card(markdown="推送失败的内容"), mode="push_first"
    )
    check(queued and player.notice_count() == 1, "推送失败时转入离线队列")
    check("推送失败的内容" in player.pending_notices[-1]["text"], "队列内容正确")

    player.pending_notices.clear()
    queued = await N.deliver_one(
        _FakeSender(False), player, R.Card(markdown="丢弃内容"), mode="push_only"
    )
    check(not queued and player.notice_count() == 0, "push_only 失败时丢弃不入队")

    # 推送抛异常时同样兜底入队
    class _BrokenSender:
        async def push(self, umo, card):
            raise RuntimeError("主动消息配额已用尽")

    queued = await N.deliver_one(
        _BrokenSender(), player, R.Card(markdown="异常兜底"), mode="push_first"
    )
    check(queued and player.notice_count() == 1, "推送抛异常时转入离线队列")

    # 批量投递
    player.pending_notices.clear()
    queued = await N.deliver_many(
        _FakeSender(False),
        [(player, R.Card(markdown="批量一")), (player, R.Card(markdown="批量二"))],
        mode="push_first",
    )
    check(queued and player.notice_count() == 2, "批量投递全部入队", str(player.notice_count()))
    check(N.normalize_mode("不存在的模式") == "push_first", "非法模式回退为默认值")

    player.push_notice("待补发内容")
    text = R.format_notices(player.take_notices())
    check("离线消息" in text and "待补发内容" in text, "离线消息渲染正常")
    check(player.notice_count() == 0, "渲染后队列清空")

    # 面板不暴露账号编号，并提示未读消息
    panel = R.render_player_panel(player, store.tier_info(player), notice_count=2)
    check(player.uid not in panel.markdown, "玩家面板不含账号编号")
    check("未读消息" in panel.markdown, "玩家面板提示未读消息")
    check(S.holder_name(store, "10001") == player.name, "持股人显示为昵称")
    check(S.holder_name(store, "nobody") == "某位玩家", "未知持股人使用占位称呼")

    # ------------------------------------------------------------ 存档往返
    print("\n=== 14. 存档读写 ===")
    player.cash = 12345
    await store.save(force=True)
    store2 = GameStore(tmp, init_money=1000)
    await store2.load()
    loaded = store2.get("10001")
    check(loaded is not None, "存档可重新载入")
    check(loaded.cash == 12345, "金钱字段一致")
    check(len(loaded.artists) == len(player.artists), "艺人数一致")
    check(len(store2.market_companies) == 10, "股市公司随存档持久化")

    # ------------------------------------------------------------ 渲染抽查
    print("\n=== 15. 面板渲染抽查 ===")
    cards = [
        ("玩家面板", R.render_player_panel(player, store.tier_info(player))),
        ("集团面板", R.render_group_panel(player, store.tier_info(player))),
        ("艺人面板", R.render_artist_panel(artist)),
        ("员工名册", R.render_staff_list(player)),
        ("项目面板", R.render_project_panel(player)),
        ("银行", R.render_bank(player)),
        ("商城", R.render_shop(player)),
        ("商业活动", R.render_market(player)),
        ("事务进度", R.render_quest_summary(player)),
        ("股市", S.render_market_panel(store, player)),
        ("持股", S.render_holdings(store, player)),
    ]
    for label, card in cards:
        plain = card.text_for(False)
        md = card.text_for(True)
        ok = bool(md) and "**" not in plain and "None" not in md
        check(ok, f"{label}渲染正常（{len(md)} 字符）", brief(plain, 80))

    # ------------------------------------------------------------ emoji 检查
    print("\n=== 16. 文案 emoji 检查 ===")
    import emojicheck

    emoji_count = emojicheck.scan(PLUGIN_DIR)
    check(emoji_count == 0, "插件内无 emoji", f"发现 {emoji_count} 处")

    print("\n" + "=" * 56)
    print(f"自测完成：通过 {OK} 项，失败 {FAIL} 项")
    print("=" * 56)
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
