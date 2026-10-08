"""持久化存储。

数据统一保存在 ``data/plugin_data/astrbot_plugin_staridol/`` 下（遵循 AstrBot 插件规范：
插件自身目录只放代码，用户数据放 data 目录，避免更新/重装插件时丢档）。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from . import constants as C
from . import utils as U
from .models import MarketCompany, Player

#: 临时增益的数值表（定义在 constants 中，这里保持向后兼容的别名）
BUFF_VALUES: dict[str, int] = {k: int(v["value"]) for k, v in C.BUFFS.items()}

BUFF_NAMES: dict[str, str] = {k: str(v["name"]) for k, v in C.BUFFS.items()}

DATA_FILE = "players.json"
BACKUP_DIR = "backups"
SAVE_VERSION = 1


def _default_data_dir() -> Path:
    """计算插件数据目录 ``<AstrBot data>/plugin_data/astrbot_plugin_staridol``。

    AstrBot 提供了 ``get_astrbot_data_path()``，但其结果依赖运行时的
    ``ASTRBOT_ROOT`` / 当前工作目录 / 是否桌面打包版。为了在任何部署方式下
    都把存档放进真正的 data 目录，这里做一次校验：候选目录下必须存在
    ``plugins`` 子目录（插件本身就是从那里加载的），否则回退到按插件文件
    位置推断的 ``<data>/plugins/<插件>/staridol/store.py``。
    """
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        candidates.append(Path(get_astrbot_data_path()))
    except Exception:
        pass
    if len(here.parents) >= 4:
        # <data>/plugins/<plugin_name>/staridol/store.py -> parents[3] == <data>
        candidates.append(here.parents[3])
    candidates.append(Path("data"))

    for base in candidates:
        try:
            if (base / "plugins").is_dir() or (base / "plugin_data").is_dir():
                return base / "plugin_data" / "astrbot_plugin_staridol"
        except OSError:
            continue
    return Path("data") / "plugin_data" / "astrbot_plugin_staridol"


class GameStore:
    """存档管理器：负责玩家数据的加载、缓存、落盘与备份。"""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        init_money: int = 1000,
        init_points: int = 150,
        show_refresh: int = 3,
        show_artist_count: int = 5,
        market_refresh: int = 3,
        base_quest: int = 5,
        max_artists: int = 30,
        logger: Any = None,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else _default_data_dir()
        self.file_path = self.data_dir / DATA_FILE
        self.backup_dir = self.data_dir / BACKUP_DIR
        self.init_money = int(init_money)
        self.init_points = int(init_points)
        self.show_refresh = int(show_refresh)
        self.show_artist_count = max(1, int(show_artist_count))
        self.market_refresh = max(1, int(market_refresh))
        self.base_quest = int(base_quest)
        self.max_artists = int(max_artists)
        self.logger = logger

        self.players: dict[str, Player] = {}
        self.history_names: set[str] = set()
        self.group_bind: dict[str, str] = {}  # group_id -> 最近一次触发事务的玩家 uid
        self.market_companies: dict[str, MarketCompany] = {}  # 股市独立公司（全局共享）
        self.stock_date: str = ""  # 上次股市招标刷新日期
        self.bot_menu_version: int = 0  # 已同步到 QQ 机器人的菜单版本
        self.command_panels: dict[str, str] = {}  # scope -> 指令面板 ID
        self.lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()
        self._dirty = False
        self._last_save = 0.0
        self._loaded = False

    # ------------------------------------------------------------------
    # 基础 IO
    # ------------------------------------------------------------------
    def _log(self, level: str, msg: str) -> None:
        if self.logger is None:
            return
        getattr(self.logger, level, self.logger.info)(msg)

    async def load(self) -> None:
        """读取存档（不存在则初始化空存档）。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self._loaded = True
            return
        try:
            raw = await asyncio.to_thread(self.file_path.read_text, "utf-8")
            data = json.loads(raw or "{}")
        except Exception as exc:  # 存档损坏时保留原文件并重命名，避免直接覆盖丢失
            broken = self.file_path.with_suffix(f".broken.{int(time.time())}.json")
            try:
                await asyncio.to_thread(shutil.copy2, self.file_path, broken)
            except Exception:
                pass
            self._log("error", f"[staridol] 存档解析失败，已备份到 {broken.name}: {exc}")
            self._loaded = True
            return

        for uid, payload in (data.get("players") or {}).items():
            try:
                player = Player.from_dict(payload)
                if not player.uid:
                    player.uid = str(uid)
                self.players[str(uid)] = player
            except Exception as exc:
                self._log("error", f"[staridol] 玩家 {uid} 数据载入失败：{exc}")
        self.history_names = set(data.get("history_names") or [])
        self.group_bind = {str(k): str(v) for k, v in (data.get("group_bind") or {}).items()}
        for mid, payload in (data.get("market_companies") or {}).items():
            try:
                company = MarketCompany.from_dict(payload)
                self.market_companies[str(mid)] = company
            except Exception as exc:
                self._log("error", f"[staridol] 股市公司 {mid} 数据载入失败：{exc}")
        self.stock_date = str(data.get("stock_date") or "")
        self.bot_menu_version = int(data.get("bot_menu_version") or 0)
        self.command_panels = {
            str(k): str(v) for k, v in (data.get("command_panels") or {}).items()
        }
        legacy_panel = str(data.get("command_panel_id") or "")
        if legacy_panel and "group" not in self.command_panels:
            self.command_panels["group"] = legacy_panel
        self.ensure_market_companies()
        self._loaded = True
        self._log("info", f"[staridol] 已载入 {len(self.players)} 位玩家的存档。")

    def ensure_market_companies(self) -> None:
        """确保股市中始终有固定数量的独立公司（首次运行时生成）。"""
        if self.market_companies:
            return
        pool = list(C.MARKET_COMPANY_POOL)
        import random as _random

        _random.shuffle(pool)
        for ctype, name in pool[: C.MARKET_COMPANY_COUNT]:
            unit_price = _random.randint(*C.STOCK_UNIT_PRICE_RANGE)
            company = MarketCompany(
                mid=U.new_id("s"),
                name=name,
                ctype=ctype,
                unit_price=unit_price,
                prev_price=unit_price,
            )
            self.market_companies[company.mid] = company
        self.mark_dirty()

    def find_market_company(self, name: str) -> MarketCompany | None:
        """按名称查找独立公司（支持模糊匹配前缀）。"""
        name = (name or "").strip()
        if not name:
            return None
        for company in self.market_companies.values():
            if company.name == name:
                return company
        for company in self.market_companies.values():
            if name in company.name or company.name in name:
                return company
        return None

    def mark_dirty(self) -> None:
        self._dirty = True

    async def save(self, force: bool = False) -> None:
        """把内存数据写回磁盘（默认 5 秒节流）。

        Windows 下对目标文件执行原子替换（tmp -> players.json）时，若目标正被
        其他进程占用（例如杀毒扫描、资源管理器预览），``Path.replace`` 会抛出
        WinError。为此这里用一个独立的 I/O 锁把写入串行化，避免多次并发 ``save``
        互相竞争同一个 tmp 文件；替换失败时回退为直接写目标文件。
        """
        if not self._dirty and not force:
            return
        current = time.time()
        if not force and current - self._last_save < 5:
            return
        payload = {
            "version": SAVE_VERSION,
            "saved_at": current,
            "players": {uid: p.to_dict() for uid, p in self.players.items()},
            "history_names": sorted(self.history_names)[-800:],
            "group_bind": dict(self.group_bind),
            "market_companies": {mid: c.to_dict() for mid, c in self.market_companies.items()},
            "stock_date": self.stock_date,
            "bot_menu_version": self.bot_menu_version,
            "command_panels": dict(self.command_panels),
        }
        text = json.dumps(payload, ensure_ascii=False, indent=1)
        # 串行化写盘，避免多个协程并发操作同一个 tmp 文件
        async with self._io_lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.file_path.with_suffix(".tmp")
            try:
                await asyncio.to_thread(self._write_text, tmp, text)
                try:
                    # 原子替换：失败（目标被占用）时回退为直接写目标
                    await asyncio.to_thread(os.replace, tmp, self.file_path)
                    self._dirty = False
                    self._last_save = current
                    return
                except OSError:
                    self._log("warning", "[staridol] 原子替换失败，回退为直接写目标文件。")
                    await asyncio.to_thread(self._write_text, self.file_path, text)
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                    self._dirty = False
                    self._last_save = current
            except Exception as exc:
                self._log("error", f"[staridol] 存档写入失败：{exc}")

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        """以 UTF-8 写文本；用 open + write 而非 write_text，便于控制编码容错。"""
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    async def flush_loop(self, interval: float = 10.0) -> None:
        """后台循环：定期落盘，直到被取消。"""
        try:
            while True:
                await asyncio.sleep(interval)
                await self.save()
        except asyncio.CancelledError:  # pragma: no cover
            await self.save(force=True)
            raise

    # ------------------------------------------------------------------
    # 玩家
    # ------------------------------------------------------------------
    def get(self, uid: str) -> Player | None:
        return self.players.get(str(uid))

    def require(self, uid: str) -> Player | None:
        return self.players.get(str(uid))

    def all_players(self) -> list[Player]:
        return list(self.players.values())

    def create_player(self, uid: str, name: str) -> Player:
        """创建新玩家（未注册集团，等待「登记」指令）。"""
        player = Player(uid=str(uid), name=name or "董事长")
        player.cash = self.init_money
        player.show_left = self.show_refresh
        self.players[str(uid)] = player
        self.mark_dirty()
        return player

    def get_or_create(self, uid: str, name: str = "") -> Player:
        player = self.get(uid)
        if player is None:
            player = self.create_player(uid, name)
        elif name and not player.custom_name and player.name != name:
            # 未自定义昵称时跟随外部昵称更新；已自定义则保持不变
            player.name = name
        return player

    def remove_player(self, uid: str) -> bool:
        """删除玩家存档（重开）。"""
        if str(uid) in self.players:
            player = self.players.pop(str(uid))
            for artist in player.artists:
                self.history_names.add(artist.name)
            self.release_holdings(str(uid))
            self.mark_dirty()
            return True
        return False

    def release_holdings(self, uid: str) -> None:
        """释放某玩家持有的全部外部股份（重开 / 清档时调用）。"""
        for company in self.market_companies.values():
            company.holders.pop(str(uid), None)
        for player in self.players.values():
            for company in player.companies:
                if company.owner != str(uid):
                    company.holders.pop(str(uid), None)

    def record_names(self, names: list[str] | set[str]) -> None:
        """记录出现过的艺人姓名，避免后续重名。"""
        before = len(self.history_names)
        self.history_names.update(names)
        if len(self.history_names) != before:
            self.mark_dirty()

    def new_artist_name(self) -> str:
        """生成一个从未出现过的艺人姓名。"""
        name = U.rand_name(self.history_names)
        self.history_names.add(name)
        self.mark_dirty()
        return name

    def tier_info(self, player: Player) -> C.TierInfo:
        """统计玩家当前的资产档位（只增不减）。

        档位每提升一级，除每日事务 +1 外，今日秀场与商业活动的每日可用次数
        也同步 +1（提升后立即补到今日可用次数中）。
        """
        info = C.tier_of(player.total_asset, self.base_quest)
        if info.tier > player.asset_tier:
            delta = info.tier - player.asset_tier
            player.asset_tier = info.tier
            # 档位提升时同步给今日秀场 / 商业活动各 +1
            player.show_left += delta
            player.market_left += delta
            self.mark_dirty()
        return C.TierInfo(
            tier=player.asset_tier,
            next_threshold=(
                C.ASSET_TIERS[player.asset_tier]
                if player.asset_tier < len(C.ASSET_TIERS)
                else None
            ),
            quest_count=self.base_quest + player.asset_tier,
            reward_multiplier=1.0 + player.asset_tier * 0.35,
        )

    def show_total(self, player: Player) -> int:
        """当前每日秀场可用次数：基础次数 + 已达档位数。"""
        return int(self.show_refresh) + int(player.asset_tier)

    def market_total(self, player: Player) -> int:
        """当前每日商业活动可用次数：基础次数 + 已达档位数。"""
        return int(self.market_refresh) + int(player.asset_tier)

    # ------------------------------------------------------------------
    # 备份
    # ------------------------------------------------------------------
    def list_backups(self) -> list[Path]:
        if not self.backup_dir.exists():
            return []
        return sorted(self.backup_dir.glob("staridol_*.json"), reverse=True)

    async def create_backup(self) -> Path | None:
        await self.save(force=True)
        if not self.file_path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        target = self.backup_dir / f"staridol_{time.strftime('%Y%m%d_%H%M%S')}.json"
        try:
            await asyncio.to_thread(shutil.copy2, self.file_path, target)
        except Exception as exc:
            self._log("error", f"[staridol] 备份失败：{exc}")
            return None
        # 只保留最近 20 份备份
        backups = self.list_backups()
        for old in backups[20:]:
            try:
                old.unlink()
            except Exception:
                pass
        return target

    async def restore_backup(self, name: str) -> bool:
        """按文件名恢复备份。"""
        target = self.backup_dir / name
        if not target.exists():
            target = self.backup_dir / f"{name}.json"
        if not target.exists():
            return False
        try:
            await asyncio.to_thread(shutil.copy2, target, self.file_path)
        except Exception as exc:
            self._log("error", f"[staridol] 恢复备份失败：{exc}")
            return False
        self.players.clear()
        self.history_names.clear()
        self.group_bind.clear()
        self.market_companies.clear()
        self.stock_date = ""
        self.command_panels.clear()
        await self.load()
        self.mark_dirty()
        await self.save(force=True)
        return True
