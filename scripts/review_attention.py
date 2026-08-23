"""生成知新「注意力快照」：想法流的共振挖掘，替代"日报"。

on-demand、人工触发，不绑定周期、不自动。扫描：
  - 共享捕获层（默认项目根 inbox.md，可用环境变量 ZHIXIN_INBOX 指向
    Workspace 根的单文件捕获层；原始捕获，列为待晋升候选）
  - 知新 notes/ 中 source_type == own_thought 的结构化笔记
    （含 tags / source_project / disposition / date）

输出：按 source_project 分组、重复主题 / 跨标签共鸣簇、未决张力簇与启发式处置建议。

用法：
  python scripts/review_attention.py          # 全量想法流
  python scripts/review_attention.py 30       # 近 30 天切片（按笔记 date）

与 review.py 区分：review.py = 全库状态 + 该推进主题；本脚本 = 想法流共振挖掘。
两者互补不重叠。本脚本只汇总与提醒，不自动改写任何笔记的 disposition。
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
import os
import sys

import frontmatter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHARED_INBOX = Path(os.environ.get("ZHIXIN_INBOX", str(PROJECT_ROOT / "inbox.md")))
DISPOSITIONS = ("待定", "执行", "沉淀", "放弃", "讨论")
RECURRENCE_THRESHOLD = 2  # 同一标签出现 ≥ 此值视为重复主题


def load_own_thoughts(notes_dir: Path) -> list[dict]:
    items: list[dict] = []
    if not notes_dir.is_dir():
        return items
    for path in sorted(notes_dir.glob("*.md")):
        with path.open(encoding="utf-8-sig") as markdown_file:
            document = frontmatter.load(markdown_file)
        if document.metadata.get("source_type") != "own_thought":
            continue
        items.append(
            {
                "title": document.metadata.get("title", "无标题"),
                "date": document.metadata.get("date"),
                "tags": document.metadata.get("tags") or [],
                "source_project": document.metadata.get("source_project", "其他"),
                "disposition": document.metadata.get("disposition", "待定"),
            }
        )
    return items


def parse_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def read_inbox() -> list[str]:
    if not SHARED_INBOX.is_file():
        return []
    lines = SHARED_INBOX.read_text(encoding="utf-8-sig").splitlines()
    # 去掉空行与标题/引用/列表符号行，保留实质条目
    entries = [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith(("#", ">", "-"))
    ]
    return entries


def format_date(value) -> str:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else "未知日期"


def main() -> int:
    slice_days = int(sys.argv[1]) if len(sys.argv) > 1 else None
    today = date.today()

    thoughts = load_own_thoughts(PROJECT_ROOT / "notes")
    inbox_entries = read_inbox()

    if slice_days is not None:
        cutoff = today - timedelta(days=slice_days)
        thoughts = [t for t in thoughts if (parse_date(t["date"]) or today) >= cutoff]

    print("# 知新 · 注意力快照\n")
    print(f"生成日期：{today.isoformat()}")
    if slice_days is not None:
        print(f"窗口：近 {slice_days} 天（按笔记 date）\n")
    else:
        print("窗口：全量想法流\n")

    # 一、共享捕获层原始捕获
    print("## 一、共享捕获层原始捕获（待晋升候选）\n")
    if inbox_entries:
        print(f"共 {len(inbox_entries)} 条原始条目：\n")
        for entry in inbox_entries[:50]:
            print(f"- {entry}")
        if len(inbox_entries) > 50:
            print(f"- …（其余 {len(inbox_entries) - 50} 条略）")
    else:
        print("- 暂无原始捕获（捕获层为空或不存在）")

    # 二、按来源项目分组（source_project 用用户自己的项目白名单）
    print("\n## 二、按来源项目分组的已落库想法\n")
    by_project: dict[str, list[dict]] = defaultdict(list)
    for thought in thoughts:
        by_project[thought["source_project"]].append(thought)
    if by_project:
        for project in sorted(by_project):
            items = by_project[project]
            print(f"### {project}（{len(items)}）\n")
            for thought in sorted(
                items, key=lambda x: parse_date(x["date"]) or date.min, reverse=True
            ):
                tags = "、".join(thought["tags"]) if thought["tags"] else "无"
                print(
                    f"- {format_date(thought['date'])} · "
                    f"[{thought['disposition']}] {thought['title']} · 标签：{tags}"
                )
            print("")
    else:
        print("- 暂无已落库的 own_thought 笔记")

    # 三、重复主题 / 跨标签信号
    print("## 三、重复主题 / 跨标签信号\n")
    tag_to_thoughts: dict[str, list[dict]] = defaultdict(list)
    for thought in thoughts:
        for tag in thought["tags"]:
            tag_to_thoughts[tag].append(thought)
    recurring = {
        tag: ts for tag, ts in tag_to_thoughts.items() if len(ts) >= RECURRENCE_THRESHOLD
    }
    if recurring:
        for tag in sorted(recurring, key=lambda k: -len(recurring[k])):
            ts = recurring[tag]
            print(f"### 标签「{tag}」出现 {len(ts)} 次（共鸣簇）\n")
            for thought in ts:
                print(
                    f"- {thought['title']}（{thought['source_project']} / "
                    f"{thought['disposition']}）"
                )
            print("")
    else:
        print("- 暂无出现 ≥2 次的标签（想法流还不够密，先在捕获层多丢一些）")

    # 四、未决张力（待定 / 讨论）
    print("## 四、未决张力（待定 / 讨论）\n")
    unresolved = [t for t in thoughts if t["disposition"] in ("待定", "讨论")]
    if unresolved:
        cluster: dict[str, list[dict]] = defaultdict(list)
        for thought in unresolved:
            key = "、".join(thought["tags"]) if thought["tags"] else "（无标签）"
            cluster[key].append(thought)
        for key, ts in cluster.items():
            print(f"### 簇：{key}（{len(ts)}）\n")
            for thought in ts:
                print(
                    f"- {thought['title']}（{thought['source_project']} / "
                    f"{thought['disposition']}）"
                )
            has_discuss = any(x["disposition"] == "讨论" for x in ts)
            if len(ts) >= 3:
                suggestion = "沉淀（反复出现，建议纳入知识网）"
            elif has_discuss:
                suggestion = "讨论（拿不准，需对话再定）"
            else:
                suggestion = "待定 / 视近期是否再共鸣决定"
            print(f"  - 处置建议：{suggestion}\n")
    else:
        print("- 暂无待定 / 讨论状态的想法")

    # 五、disposition 分布
    print("## 五、disposition 分布\n")
    counts = Counter(t["disposition"] for t in thoughts)
    if thoughts:
        for disposition in DISPOSITIONS:
            if counts.get(disposition):
                print(f"- {disposition}：{counts[disposition]}")
    else:
        print("- 暂无 own_thought 笔记")

    print("\n---")
    print(
        "说明：本报告只汇总与提醒，不自动改写笔记 disposition；"
        "建议经用户确认后批量更新。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
