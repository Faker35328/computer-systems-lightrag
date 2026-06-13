import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - optional progress bar
    tqdm = None


REQUIRED_LABELS = ("课程：", "资料类型：", "来源：", "来源文件：", "教材页码：", "章节：", "小节：")
MOJIBAKE_PATTERNS = (
    "璇剧",
    "绗",
    "锛",
    "鍥",
    "æ",
    "ç",
    "è",
    "ï¼",
    "�",
)


def progress(iterable, *, total=None, desc="", unit="", enabled=True):
    if enabled and tqdm is not None:
        return tqdm(iterable, total=total, desc=desc, unit=unit, dynamic_ncols=True)
    return iterable


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def rel(path, base):
    return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()


def split_metadata_and_body(text):
    if "\n---\n" not in text:
        return text, ""
    meta, body = text.split("\n---\n", 1)
    return meta, body.strip()


def body_text_chars(body):
    useful = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        useful.append(line)
    return len("\n".join(useful).strip())


def extract_metadata(meta):
    def match_line(label):
        match = re.search(rf"^{re.escape(label)}(.+)$", meta, re.MULTILINE)
        return match.group(1).strip() if match else ""

    return {
        "course": match_line("课程："),
        "doc_type": match_line("资料类型："),
        "source": match_line("来源："),
        "source_file": match_line("来源文件："),
        "page_label": match_line("教材页码："),
        "chapter": match_line("章节："),
        "section": match_line("小节："),
    }


def detect_issues(text, meta, body):
    issues = {}
    missing_labels = [label for label in REQUIRED_LABELS if label not in meta]
    if missing_labels:
        issues["missing_labels"] = missing_labels

    mojibake_hits = [pattern for pattern in MOJIBAKE_PATTERNS if pattern in text]
    if mojibake_hits:
        issues["mojibake_hits"] = mojibake_hits

    if "![](images/" in text or re.search(r"!\[[^\]]*\]\([^)]*images/[^)]*\)", text):
        issues["image_placeholder"] = True

    chars = body_text_chars(body)
    if not body.strip():
        issues["empty_body"] = True
    if chars < 80:
        issues["short_body_chars"] = chars

    return issues


def analyze_markdown(path, project_root):
    text = path.read_text(encoding="utf-8")
    meta, body = split_metadata_and_body(text)
    metadata = extract_metadata(meta)
    issues = detect_issues(text, meta, body)
    body_lower = body.lower()
    return {
        "path": rel(path, project_root),
        "source": metadata["source"],
        "source_file": metadata["source_file"],
        "page_label": metadata["page_label"],
        "chapter": metadata["chapter"],
        "section": metadata["section"],
        "chars": len(text),
        "body_chars": body_text_chars(body),
        "has_table": "<table" in body_lower or re.search(r"^\|.+\|$", body, re.MULTILINE) is not None,
        "has_code": "```" in body,
        "has_equation": "$$" in body or "\\(" in body or "\\[" in body or "\\frac" in body,
        "issues": issues,
    }


def manifest_index(manifest):
    entries = {}
    chapter_counts = {}
    for doc in manifest.get("documents", []):
        chapter = doc.get("chapter_number")
        chapter_counts[chapter] = doc.get("section_count", 0)
        for section in doc.get("sections", []):
            output_file = section.get("output_file")
            if output_file:
                entries[output_file] = section
    return entries, chapter_counts


def summarize(records, manifest_entries, chapter_counts, all_markdown_paths, project_root):
    issue_counts = Counter()
    for record in records:
        for issue_name in record["issues"]:
            issue_counts[issue_name] += 1

    by_chapter = defaultdict(lambda: {"sections": 0, "tables": 0, "codes": 0, "equations": 0, "issues": Counter()})
    for record in records:
        chapter_match = re.search(r"Chapter\s+(\d+)", record["chapter"])
        chapter = int(chapter_match.group(1)) if chapter_match else None
        by_chapter[chapter]["sections"] += 1
        by_chapter[chapter]["tables"] += int(record["has_table"])
        by_chapter[chapter]["codes"] += int(record["has_code"])
        by_chapter[chapter]["equations"] += int(record["has_equation"])
        for issue_name in record["issues"]:
            by_chapter[chapter]["issues"][issue_name] += 1

    record_paths = {record["path"] for record in records}
    manifest_paths = set(manifest_entries)
    all_markdown_rel = {rel(path, project_root) for path in all_markdown_paths}
    return {
        "markdown_file_count": len(records),
        "scanned_from_manifest": True,
        "all_markdown_file_count": len(all_markdown_rel),
        "manifest_section_count": len(manifest_entries),
        "manifest_chapter_count": len(chapter_counts),
        "manifest_chapters": sorted(chapter for chapter in chapter_counts if chapter is not None),
        "issue_counts": dict(sorted(issue_counts.items())),
        "feature_counts": {
            "sections_with_table": sum(1 for record in records if record["has_table"]),
            "sections_with_code": sum(1 for record in records if record["has_code"]),
            "sections_with_equation": sum(1 for record in records if record["has_equation"]),
        },
        "manifest_missing_files": sorted(manifest_paths - record_paths),
        "extra_markdown_files": sorted(all_markdown_rel - manifest_paths),
        "by_chapter": {
            str(chapter): {
                "sections": value["sections"],
                "manifest_sections": chapter_counts.get(chapter, 0),
                "tables": value["tables"],
                "codes": value["codes"],
                "equations": value["equations"],
                "issues": dict(sorted(value["issues"].items())),
            }
            for chapter, value in sorted(by_chapter.items(), key=lambda item: item[0] or 0)
        },
    }


def issue_records(records, issue_name, limit=80):
    rows = []
    for record in records:
        if issue_name in record["issues"]:
            rows.append(
                {
                    "path": record["path"],
                    "source": record["source"],
                    "page_label": record["page_label"],
                    "detail": record["issues"][issue_name],
                    "body_chars": record["body_chars"],
                }
            )
    return rows[:limit]


def write_markdown_report(report, path):
    summary = report["summary"]
    lines = [
        "# 教材小节 Markdown 质检报告",
        "",
        "## 总览",
        "",
        f"- Markdown 文件数：{summary['markdown_file_count']}",
        f"- 目录内 Markdown 总数：{summary['all_markdown_file_count']}",
        f"- 是否按 manifest 扫描：{summary['scanned_from_manifest']}",
        f"- Manifest 小节数：{summary['manifest_section_count']}",
        f"- Manifest 章节数：{summary['manifest_chapter_count']}",
        f"- Manifest 章节：{', '.join(str(chapter) for chapter in summary['manifest_chapters'])}",
        f"- 含表格小节：{summary['feature_counts']['sections_with_table']}",
        f"- 含代码块小节：{summary['feature_counts']['sections_with_code']}",
        f"- 含公式小节：{summary['feature_counts']['sections_with_equation']}",
        "",
        "## 问题统计",
        "",
    ]
    if summary["issue_counts"]:
        for name, count in summary["issue_counts"].items():
            lines.append(f"- {name}: {count}")
    else:
        lines.append("- 未发现问题。")

    lines.extend(["", "## Manifest 对齐", ""])
    lines.append(f"- Manifest 缺失文件：{len(summary['manifest_missing_files'])}")
    lines.append(f"- Manifest 外 Markdown：{len(summary['extra_markdown_files'])}")

    lines.extend(["", "## 按章节统计", ""])
    lines.append("| Chapter | Markdown | Manifest | Tables | Code | Equations | Issues |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for chapter, row in summary["by_chapter"].items():
        issue_text = ", ".join(f"{name}:{count}" for name, count in row["issues"].items()) or "-"
        lines.append(
            f"| {chapter} | {row['sections']} | {row['manifest_sections']} | "
            f"{row['tables']} | {row['codes']} | {row['equations']} | {issue_text} |"
        )

    lines.extend(["", "## 问题样例", ""])
    for issue_name, records in report["issue_samples"].items():
        lines.append(f"### {issue_name}")
        if not records:
            lines.append("")
            lines.append("无。")
            lines.append("")
            continue
        for record in records[:20]:
            lines.append(
                f"- `{record['path']}` | {record['page_label']} | chars={record['body_chars']} | {record['detail']}"
            )
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description="Quality check generated CSAPP textbook section Markdown.")
    parser.add_argument("--output-root", default="processed_textbook_sections")
    parser.add_argument("--manifest", default="processed_textbook_sections/textbook_sections_manifest.json")
    parser.add_argument("--json-report", default="processed_textbook_sections/textbook_sections_quality_report.json")
    parser.add_argument("--md-report", default="processed_textbook_sections/textbook_sections_quality_report.md")
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    project_root = Path.cwd()
    output_root = project_root / args.output_root
    manifest = read_json(project_root / args.manifest)
    manifest_entries, chapter_counts = manifest_index(manifest)
    all_markdown_paths = [
        path
        for path in output_root.rglob("*.md")
        if path.name not in {"textbook_sections_quality_report.md"}
    ]
    all_markdown_paths.sort()
    markdown_paths = []
    for output_file in sorted(manifest_entries):
        path = project_root / output_file
        if path.exists():
            markdown_paths.append(path)

    records = []
    iterator = progress(markdown_paths, total=len(markdown_paths), desc="textbook qc", unit="file", enabled=args.progress)
    for path in iterator:
        records.append(analyze_markdown(path, project_root))

    summary = summarize(records, manifest_entries, chapter_counts, all_markdown_paths, project_root)
    issue_names = [
        "missing_labels",
        "mojibake_hits",
        "image_placeholder",
        "empty_body",
        "short_body_chars",
    ]
    report = {
        "summary": summary,
        "issue_samples": {name: issue_records(records, name) for name in issue_names},
    }
    write_json(project_root / args.json_report, report)
    write_markdown_report(report, project_root / args.md_report)
    print(f"json_report={project_root / args.json_report}", flush=True)
    print(f"md_report={project_root / args.md_report}", flush=True)
    print(
        f"markdown_file_count={summary['markdown_file_count']} "
        f"manifest_section_count={summary['manifest_section_count']} "
        f"issues={summary['issue_counts']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
