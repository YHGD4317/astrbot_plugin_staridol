"""QQ 官方机器人「自定义菜单」与「指令面板」同步。

对应 QQ 机器人开放平台的两组接口：

* 全局自定义菜单 ``PUT /v2/menu``：仅 C2C 单聊生效，按钮点击后把
  ``send_message`` 的内容填入聊天输入框；
* 指令面板 ``POST /v2/panels`` / ``PUT /v2/panels/{panel_id}``：群聊与单聊
  均可配置，面板元素点击后把元素名填入输入框。

插件启动时会自动同步一次，管理员也可以随时发送「同步机器人菜单」手动触发。
接口失败只会记录日志，不影响游戏本体。
"""

from __future__ import annotations

import asyncio
from typing import Any

from . import constants as C


def _route(method: str, path: str, **params: Any) -> Any:
    """构造 botpy 的 Route 对象（延迟导入，缺失时不报错）。"""
    from botpy.http import Route

    return Route(method, path, **params)


class BotMenuManager:
    """负责把插件内置的菜单配置同步到 QQ 机器人。"""

    #: 支持菜单 / 面板同步的平台适配器名
    PLATFORM_NAMES = {"qq_official", "qq_official_webhook"}

    def __init__(self, store: Any, context: Any, *, enabled: bool = True, logger: Any = None) -> None:
        self.store = store
        self.context = context
        self.enabled = bool(enabled)
        self.logger = logger

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------
    def _log(self, level: str, message: str) -> None:
        if self.logger is None:
            return
        getattr(self.logger, level, self.logger.info)(message)

    def qq_platforms(self) -> list[Any]:
        """列出当前所有 QQ 官方平台适配器实例。"""
        result: list[Any] = []
        manager = getattr(self.context, "platform_manager", None)
        insts = getattr(manager, "platform_insts", None)
        if not insts:
            return result
        for platform in insts:
            try:
                name = str(platform.meta().name)
            except Exception:
                continue
            if name in self.PLATFORM_NAMES:
                result.append(platform)
        return result

    @staticmethod
    def _client_of(platform: Any) -> Any:
        client = getattr(platform, "client", None)
        if client is None and hasattr(platform, "get_client"):
            try:
                client = platform.get_client()
            except Exception:
                client = None
        if client is None or getattr(client, "api", None) is None:
            return None
        if getattr(client.api, "_http", None) is None:
            return None
        return client

    async def _request(self, client: Any, method: str, path: str, payload: dict, **params: Any) -> Any:
        route = _route(method, path, **params)
        return await client.api._http.request(route, json=payload)

    # ------------------------------------------------------------------
    # 菜单与面板内容
    # ------------------------------------------------------------------
    @staticmethod
    def build_menu_payload() -> dict:
        """根据 constants.CUSTOM_MENU 生成自定义菜单请求体。"""
        items: list[dict[str, Any]] = []
        for name, kind, content in C.CUSTOM_MENU:
            if kind == "send_message":
                items.append({"type": "send_message", "name": name, "send_message": str(content)})
            elif kind == "link":
                items.append({"type": "link", "name": name, "link": str(content)})
            elif kind == "menu":
                sub_items = [
                    {
                        "type": sub_kind,
                        "name": sub_name,
                        **(
                            {"send_message": str(sub_content)}
                            if sub_kind == "send_message"
                            else {"link": str(sub_content)}
                        ),
                    }
                    for sub_name, sub_kind, sub_content in content  # type: ignore[union-attr]
                ]
                items.append({"type": "menu", "name": name, "sub_menu_items": sub_items})
        return {"menu": {"items": items}}

    @staticmethod
    def build_panel_payload() -> dict:
        """根据 constants.COMMAND_PANEL 生成指令面板请求体。"""
        items = [
            {"type": "command", "name": name, "desc": desc, "only_admin": False}
            for name, desc in C.COMMAND_PANEL
        ]
        return {"panel": {"items": items, "remark": C.COMMAND_PANEL_REMARK}}

    # ------------------------------------------------------------------
    # 同步
    # ------------------------------------------------------------------
    async def sync(self, *, verbose: bool = False) -> dict:
        """执行一次完整同步，返回结果摘要。"""
        summary: dict[str, Any] = {"platforms": 0, "menu": [], "panel": [], "errors": []}
        if not self.enabled:
            summary["errors"].append("菜单同步已在配置中关闭")
            return summary

        platforms = self.qq_platforms()
        summary["platforms"] = len(platforms)
        if not platforms:
            summary["errors"].append("没有找到在线的 QQ 官方机器人平台适配器")
            return summary

        for platform in platforms:
            client = self._client_of(platform)
            if client is None:
                summary["errors"].append("平台适配器尚未连接，暂时无法同步")
                continue

            # ---- 自定义菜单（C2C）----
            try:
                result = await self._request(client, "PUT", "/v2/menu", self.build_menu_payload())
                version = 0
                if isinstance(result, dict):
                    version = int(result.get("version") or 0)
                self.store.bot_menu_version = version
                summary["menu"].append(f"自定义菜单已同步（版本 {version}）")
            except Exception as exc:
                summary["errors"].append(f"自定义菜单同步失败：{exc}")
                self._log("warning", f"[staridol] 自定义菜单同步失败：{exc}")

            # ---- 指令面板（群聊 + 单聊）----
            for scope in ("group", "c2c"):
                try:
                    message = await self._sync_panel(client, scope)
                    summary["panel"].append(message)
                except Exception as exc:
                    summary["errors"].append(f"{scope} 指令面板同步失败：{exc}")
                    self._log("warning", f"[staridol] {scope} 指令面板同步失败：{exc}")

        self.store.mark_dirty()
        await self.store.save()
        if verbose and self.logger is not None:
            self._log("info", f"[staridol] 菜单同步结果：{summary}")
        return summary

    async def _sync_panel(self, client: Any, scope: str) -> str:
        """创建或更新指定场景的指令面板。"""
        payload = self.build_panel_payload()
        panels: dict[str, str] = dict(getattr(self.store, "command_panels", {}) or {})
        panel_id = panels.get(scope, "")

        if panel_id:
            try:
                await self._request(
                    client, "PUT", "/v2/panels/{panel_id}", payload, panel_id=panel_id
                )
                return f"{scope} 指令面板已更新（{panel_id}）"
            except Exception as exc:
                # 面板可能已被删除，回退到重新创建
                self._log("warning", f"[staridol] 更新 {scope} 面板失败，尝试重新创建：{exc}")
                panel_id = ""

        create_payload = {
            "scope": scope,
            "target_type": "all",
            **payload,
        }
        result = await self._request(client, "POST", "/v2/panels", create_payload)
        new_id = ""
        if isinstance(result, dict):
            new_id = str(result.get("panel_id") or "")
        if new_id:
            panels[scope] = new_id
            self.store.command_panels = panels
        return f"{scope} 指令面板已创建（{new_id or '未知 ID'}）"

    # ------------------------------------------------------------------
    # 便捷入口
    # ------------------------------------------------------------------
    async def sync_later(self, delay: float = 8.0) -> None:
        """延迟同步（等待平台适配器连接完成）。"""
        await asyncio.sleep(max(0.0, delay))
        try:
            await self.sync()
        except Exception as exc:
            self._log("warning", f"[staridol] 启动时同步机器人菜单失败：{exc}")

    def summary_text(self, summary: dict) -> Any:
        """把同步结果整理成面向管理员的卡片。"""
        from .render import Card

        lines = ["# 机器人菜单同步结果", ""]
        lines.append(f"**检测到平台**：{summary.get('platforms', 0)} 个")
        for item in summary.get("menu", []):
            lines.append(f"- {item}")
        for item in summary.get("panel", []):
            lines.append(f"- {item}")
        errors = summary.get("errors") or []
        if errors:
            lines.append("")
            lines.append("**未完成项**")
            for item in errors:
                lines.append(f"- {item}")
        lines.append("")
        lines.append("提示：自定义菜单仅在 QQ 单聊生效，指令面板在群聊与单聊均会出现。")
        lines.append("菜单内容会填入聊天输入框，发送前请把括号中的占位内容替换成自己的信息。")
        return Card(markdown="\n".join(lines))
