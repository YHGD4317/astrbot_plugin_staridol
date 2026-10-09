"""Verify tier-6 quest backfill bug fix + blindbox range."""
from pathlib import Path
import tempfile

PLUGIN_DIR = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(PLUGIN_DIR))

from staridol import constants as C
from staridol.store import GameStore
from staridol.systems import daily as D
from staridol.systems import economy as E
import asyncio

tmp = Path(tempfile.mkdtemp(prefix="verify_tier_"))
store = GameStore(tmp, init_money=1000, init_points=150, show_refresh=3, market_refresh=3)
asyncio.run(store.load())

player = store.create_player("10001", "测试总裁")
player.company_name = "星海集团"
player.decision, player.finance, player.eloquence = 45, 75, 30
player.points_assigned = True
D.ensure_daily(store, player)
assert player.asset_tier == 0 and len(player.quests) == 5, f"fresh: tier={player.asset_tier}, quests={len(player.quests)}"
print("[OK] fresh player: tier 0, 5 quests")

# Scenario A: normal mid-day jump to tier 3 -> 8 quests
player.cash = 40000  # tier 3 (30000~70000)
info = store.tier_info(player)
assert player.asset_tier == 3, f"expected tier 3, got {player.asset_tier}"
assert len(player.quests) == 8, f"tier3 should have 8 quests, got {len(player.quests)}"
assert info.quest_count == 8
print("[OK] jump to tier 3 -> 8 quests")

# Scenario B: the reported bug. Player's asset_tier is recorded lower than actual tier,
# and current quests are under-filled. Simulate: asset_tier stored as 3 but assets already
# reach tier 6, only 8 quests exist. Should backfill to 5+6=11.
player.asset_tier = 3                      # stale stored tier (under-count)
player.cash = 200000                      # well into tier 6 (>=300000? no: 200000 < 300000)
# tier 6 threshold = 300000; set cash above it
player.cash = 400000                       # tier 6 (300000~700000)
info = store.tier_info(player)
assert player.asset_tier == 6, f"expected tier 6, got {player.asset_tier}"
assert info.quest_count == 11, f"quest_count should be 5+6=11, got {info.quest_count}"
assert len(player.quests) == 11, f"backfill should make 11 quests, got {len(player.quests)}"
print("[OK] stale tier3->real tier6 backfilled to 11 quests (was 8)")

# Scenario C: retroactive补发 for a player already above target tier with too few quests
player.quests = player.quests[:3]          # simulate a player who got just 3 of today's quests
store.tier_info(player)
assert len(player.quests) == 11, f"retroactive fill should restore 11, got {len(player.quests)}"
print("[OK] retroactive补齐 to 11 quests after trimming to 3")

# Scenario D: player above target (used 事务刷新卡) must NOT be reduced
player.asset_tier = 6
added = __import__("staridol.systems.quest", fromlist=["add_quests"]).add_quests(store, player, 3)
assert len(player.quests) == 14, f"after 3 refresh cards, 14 quests expected, got {len(player.quests)}"
store.tier_info(player)
assert len(player.quests) == 14, f"backfill must not remove extra quests, got {len(player.quests)}"
print("[OK] no reduction when quests exceed target (事务刷新卡 stack preserved)")

# ---- Blindbox range ----
assert C.BOX_MONEY_RANGE == (-8000, 8000), f"range should be (-8000,8000), got {C.BOX_MONEY_RANGE}"
import random
orig_randint = random.randint
random.randint = lambda a, b: -8000  # extreme loss
player.cash, player.deposit = 100, 0
k, v = E.open_box(store, player)
assert v == -8000 and player.money == 0, f"loss capped, money={player.money}"
random.randint = lambda a, b: 8000
player.cash = 0
k, v = E.open_box(store, player)
assert v == 8000 and player.cash == 8000, f"gain applied, cash={player.cash}"
random.randint = orig_randint
print("[OK] blindbox range -8000w~+8000w, loss capped to 0, gain credited")

print("\nALL TIER + BLINDBOX CHECKS PASSED")
