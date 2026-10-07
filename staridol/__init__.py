"""娱乐圈模拟器 —— 核心数据包。

模块划分::

    constants.py  游戏静态数据（属性、等级、项目、道具、事务模板、姓名库、菜单配置）
    utils.py      通用工具（骰点、时间、数值格式化、随机名）
    models.py     数据模型（玩家 / 艺人 / 项目 / 事务 / 公司 / 股市公司）
    store.py      持久化存储（data/plugin_data/astrbot_plugin_staridol）
    render.py     文本与 Markdown 卡片渲染
    qqcard.py     QQ 官方机器人 Markdown 卡片 + 按钮发送
    menus.py      QQ 官方机器人自定义菜单与指令面板同步
    notify.py     消息投递策略（主动推送失败时转入离线队列）
    stocks.py     股市系统（独立公司与玩家公司股份）
    systems/      各玩法系统（艺人、项目、事务、经济、每日刷新）
    router.py     指令解析与分发
    scheduler.py  后台定时任务

全部面向玩家的文案均不使用 emoji。
"""

__all__ = [
    "constants",
    "utils",
    "models",
    "store",
    "render",
    "qqcard",
    "menus",
    "notify",
    "stocks",
    "systems",
    "router",
    "scheduler",
]
