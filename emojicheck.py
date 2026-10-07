"""全量 emoji 检查：确保插件所有文本中不出现 emoji。

用法：python emojicheck.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Emoji 及相关符号的 Unicode 区段
EMOJI_PATTERN = re.compile(
    "["
    "\U0001f000-\U0001f0ff"  # 麻将、多米诺等
    "\U0001f100-\U0001f1ff"  # 带圈字母、区域指示符
    "\U0001f200-\U0001f2ff"  # 带圈表意文字
    "\U0001f300-\U0001f5ff"  # 天气、建筑、人物等
    "\U0001f600-\U0001f64f"  # 表情
    "\U0001f650-\U0001f67f"
    "\U0001f680-\U0001f6ff"  # 交通
    "\U0001f700-\U0001f77f"
    "\U0001f780-\U0001f7ff"
    "\U0001f800-\U0001f8ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001faff"
    "\U00002600-\U000026ff"  # 杂项符号（U+2600 区段，含天气/电话/星形等）
    "\U00002700-\U000027bf"  # 装饰符号（U+2700 区段，含对勾/叉号等）
    "\U00002b00-\U00002bff"  # 箭头与星形符号（U+2B00 区段）
    "\U0000fe0f"  # 变体选择符
    "\U00002049"  # 感叹疑问号
    "\U0000203c"
    "\U00002122"
    "\U00002139"
    "\U00002194-\U00002199"
    "\U000021a9-\U000021aa"
    "]"
)

SKIP_DIRS = {"__pycache__", ".git", "data"}


def scan(root: Path) -> int:
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in relative_parts):
            continue
        if path.suffix.lower() not in {".py", ".md", ".json", ".yaml", ".yml", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            found = EMOJI_PATTERN.findall(line)
            if found:
                total += len(found)
                chars = " ".join(f"{c}(U+{ord(c):04X})" for c in dict.fromkeys(found))
                print(f"{path.relative_to(root)}:{lineno}: {chars}")
                print(f"    {line.strip()[:110]}")
    return total


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    count = scan(root)
    print("=" * 60)
    if count:
        print(f"发现 {count} 处 emoji，请全部清理。")
        sys.exit(1)
    print("检查通过：插件内没有任何 emoji。")
