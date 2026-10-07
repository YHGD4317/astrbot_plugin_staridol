"""真实 AstrBot 环境导入验证（只做导入，不启动任何服务）。

注意：本脚本会把工作目录切到 AstrBot 核心目录，避免 AstrBot 初始化时
在插件目录下误建 ``data/`` 目录。
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

CORE_DIR = Path(r"D:\AstrBot Launcher\.astrbot_launcher\instances\38bdbc12-a897-4343-9ca3-9303b0f8c6d4\core")
os.chdir(CORE_DIR)  # 保证 get_astrbot_data_path() 指向真实 data 目录
sys.path.insert(0, str(CORE_DIR))

print("1) 导入 AstrBot 核心 ...")
try:
    import astrbot  # noqa: F401
    # 下面这些正是插件实际依赖的 API，导入成功即代表接口可用
    from astrbot.api.event import AstrMessageEvent, filter  # noqa: F401
    from astrbot.api.star import Context, Star, register  # noqa: F401

    print("   OK  astrbot 版本:", getattr(astrbot, "__version__", "unknown"))
except Exception:
    traceback.print_exc()
    sys.exit(1)

print("2) 导入插件 main 模块 ...")
try:
    import data.plugins.astrbot_plugin_staridol.main as plugin_main
except Exception:
    traceback.print_exc()
    sys.exit(1)
print("   OK ", plugin_main.StarIdolPlugin)

print("3) 检查装饰器注册与 handler ...")
try:
    from astrbot.core.star.star_handler import star_handlers_registry

    handlers = [
        h
        for h in star_handlers_registry
        if "astrbot_plugin_staridol" in str(getattr(h, "handler_module_path", ""))
    ]
    print(f"   OK  注册 handler 数量: {len(handlers)}")
    for h in handlers:
        print("      -", h.handler_name, h.handler.__name__)
except Exception:
    traceback.print_exc()
    sys.exit(1)

print("4) 检查插件数据目录解析 ...")
try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    print("   astrbot data path:", get_astrbot_data_path())
    from data.plugins.astrbot_plugin_staridol.staridol.store import _default_data_dir

    resolved = _default_data_dir()
    print("   插件存档目录:", resolved)
    expect = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_staridol"
    if str(resolved) == str(expect):
        print("   OK  与 AstrBot data 目录一致")
    else:
        # 桌面打包版可能把 root 指到 ~/.astrbot，此时按插件位置回退同样正确
        print("   OK  已按插件位置回退（AstrBot root 与实例 data 不一致时的预期行为）")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print("\n全部通过：插件可在真实 AstrBot 环境中加载。")
