"""语义健康检查：孤儿笔记、缺页实体、标签漂移、过期主题页、主题间潜在矛盾。

不绑定周期，想看就跑。建议每月或批量入库后运行。
前 4 项为机械检查，第 5 项标注「需 AI 判断」。

固定标签体系不硬编码：通过环境变量 ZHIXIN_FIXED_TAGS（逗号分隔）配置；
未配置时跳过标签漂移检查并提示。source_project 同理，由用户自己的白名单
（可由 validate.py 校验）约束。

用法：
  python scripts/lint.py
"""
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
import os
import sys

import frontmatter

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 固定标签体系（用户通过环境变量配置，逗号分隔；空则跳过标签漂移检查）
FIXED_TAGS = {
    tag.strip()
    for tag in os.environ.get("ZHIXIN_FIXED_TAGS", "").split(",")
    if tag.strip()
}


def load_documents(directory: Path) -> list[tuple[Path, frontmatter.Post]]:
    """加载目录下所有 .md 文件，返回 (路径, frontmatter 文档) 列表。"""
    documents = []
    for path in sorted(directory.glob("*.md")):
        with path.open(encoding="utf-8-sig") as markdown_file:
            doc = frontmatter.load(markdown_file)
        documents.append((path, doc))
    return documents


def parse_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def as_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def collect_referenced_note_filenames(topics, entities) -> set[str]:
    """收集主题页和实体页 sources 字段中引用的所有笔记文件名。"""
    referenced = set()
    for _, doc in topics + entities:
        for src in as_str_list(doc.metadata.get("sources")):
            referenced.add(Path(src).name)
    return referenced


def count_entity_mentions(notes) -> Counter:
    """统计笔记 entities 字段中每个实体的出现次数。"""
    counter: Counter[str] = Counter()
    for _, doc in notes:
        for entity in as_str_list(doc.metadata.get("entities")):
            if entity.strip():
                counter[entity] += 1
    return counter


def get_entity_titles(entities) -> set[str]:
    return {
        title for title in (doc.metadata.get("title", "") for _, doc in entities)
        if title
    }


def check_orphan_notes(notes, referenced_filenames) -> list[str]:
    """检查未被任何主题页/实体页引用的笔记。"""
    orphans = []
    for path, doc in notes:
        if path.name not in referenced_filenames:
            orphans.append(doc.metadata.get("title", path.name))
    return orphans


def check_missing_entities(notes, entities) -> list[tuple[str, int]]:
    """检查 >=2 篇笔记提及但无实体页的对象。"""
    counts = count_entity_mentions(notes)
    existing = get_entity_titles(entities)
    return [(name, count) for name, count in counts.most_common()
            if count >= 2 and name not in existing]


def check_tag_drift(notes) -> list[tuple[str, str]]:
    """检查使用了不在固定标签体系内的标签（未配置标签体系时跳过）。"""
    if not FIXED_TAGS:
        return []
    drift = []
    for path, doc in notes:
        for tag in as_str_list(doc.metadata.get("tags")):
            if tag not in FIXED_TAGS:
                drift.append((path.name, tag))
    return drift


def check_stale_topics(notes, topics) -> list[tuple[str, date, list[tuple[str, date]]]]:
    """检查 updated_at 后有新匹配笔记但未织入的主题页。

    返回 (主题标题, updated_at, [(笔记标题, 笔记日期)]) 列表。
    """
    stale = []
    for _, doc in topics:
        updated_at = parse_date(doc.metadata.get("updated_at"))
        if not updated_at:
            continue

        topic_tags = set(as_str_list(doc.metadata.get("tags")))
        topic_sources = {Path(src).name for src in as_str_list(doc.metadata.get("sources"))}

        new_unwoven = []
        for note_path, note_doc in notes:
            note_date = parse_date(note_doc.metadata.get("date"))
            if not note_date or note_date <= updated_at:
                continue
            note_tags = set(as_str_list(note_doc.metadata.get("tags")))
            if note_tags & topic_tags and note_path.name not in topic_sources:
                new_unwoven.append(
                    (note_doc.metadata.get("title", note_path.name), note_date)
                )

        if new_unwoven:
            new_unwoven.sort(key=lambda item: item[1], reverse=True)
            title = doc.metadata.get("title", "无标题")
            stale.append((title, updated_at, new_unwoven))
    return stale


def check_topic_tag_overlap(topics) -> list[tuple[str, str, set[str]]]:
    """找出共享 >=2 标签的主题对（需 AI 判断是否有矛盾）。"""
    topic_data = [
        (doc.metadata.get("title", path.name), set(as_str_list(doc.metadata.get("tags"))))
        for path, doc in topics
    ]
    pairs = []
    for i, (title_a, tags_a) in enumerate(topic_data):
        for title_b, tags_b in topic_data[i + 1:]:
            overlap = tags_a & tags_b
            if len(overlap) >= 2:
                pairs.append((title_a, title_b, overlap))
    return pairs


def main() -> int:
    notes = load_documents(PROJECT_ROOT / "notes")
    topics = load_documents(PROJECT_ROOT / "wiki" / "topics")
    entities = load_documents(PROJECT_ROOT / "wiki" / "entities")
    today = date.today()

    print("# 知新语义健康检查\n")
    print(f"生成日期：{today.isoformat()}\n")
    print(f"笔记 {len(notes)} 篇 / 主题页 {len(topics)} 个 / 实体页 {len(entities)} 个\n")

    # 1. 孤儿笔记
    referenced = collect_referenced_note_filenames(topics, entities)
    orphans = check_orphan_notes(notes, referenced)
    print("## 1. 孤儿笔记（未被任何主题页/实体页引用）\n")
    if orphans:
        for title in orphans:
            print(f"- {title}")
    else:
        print("- 无")
    print()

    # 2. 缺页实体
    missing = check_missing_entities(notes, entities)
    print("## 2. 缺页实体（>=2 篇笔记提及但无实体页）\n")
    if missing:
        for name, count in missing:
            print(f"- {name}（{count} 篇笔记提及）")
    else:
        print("- 无")
    print()

    # 3. 标签漂移
    drift = check_tag_drift(notes)
    print("## 3. 标签漂移（使用了不在固定标签体系内的标签）\n")
    if not FIXED_TAGS:
        print("- 未配置固定标签体系（ZHIXIN_FIXED_TAGS），跳过该项")
    elif drift:
        for filename, tag in drift:
            print(f"- {filename}: `{tag}`")
    else:
        print("- 无")
    print()

    # 4. 过期主题页
    stale = check_stale_topics(notes, topics)
    print("## 4. 过期主题页（updated_at 后有新匹配笔记但未织入）\n")
    if stale:
        for title, updated_at, new_notes in stale:
            print(f"- **{title}**（最后更新 {updated_at}，{len(new_notes)} 篇待织入）")
            for note_title, note_date in new_notes:
                print(f"  - {note_date} · {note_title}")
    else:
        print("- 无")
    print()

    # 5. 主题间潜在矛盾
    pairs = check_topic_tag_overlap(topics)
    print("## 5. 主题间潜在矛盾（共享 >=2 标签，需 AI 判断）\n")
    if pairs:
        for title_a, title_b, overlap in pairs:
            print(f"- **{title_a}** <-> **{title_b}**（共享：{', '.join(sorted(overlap))}）")
    else:
        print("- 无")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
