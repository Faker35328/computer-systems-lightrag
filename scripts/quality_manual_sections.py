import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - optional progress bar
    tqdm = None


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


MOJIBAKE_PATTERNS = ("璇", "鏉", "绔", "灏", "锛", "鈥", "â", "Ã")
REQUIRED_LABELS = ("来源：", "来源文件：", "PDF页码：", "章节：", "小节：")


def progress(iterable, *, total=None, desc="", unit="", enabled=True):
    if enabled and tqdm is not None:
        return tqdm(iterable, total=total, desc=desc, unit=unit, dynamic_ncols=True)
    return iterable


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def metadata_value(text, label):
    for line in text.splitlines():
        if line.startswith(label):
            return line[len(label) :].strip()
    return ""


def body_text(text):
    return text.split("---", 1)[1].strip() if "---" in text else text.strip()


def scan_file(path, manifest_entry=None):
    text = Path(path).read_text(encoding="utf-8")
    body = body_text(text)
    issues = []
    for label in REQUIRED_LABELS:
        if not metadata_value(text, label):
            issues.append(f"missing_{label.rstrip('：')}")
    if any(pattern in text for pattern in MOJIBAKE_PATTERNS):
        issues.append("mojibake_candidate")
    if "![](images/" in text or re.search(r"!\[[^\]]*\]\(", text):
        issues.append("image_placeholder")
    if len(body) < 80:
        issues.append("short_body")
    if not body:
        issues.append("empty_body")

    return {
        "path": str(path).replace("\\", "/"),
        "source": metadata_value(text, "来源："),
        "pdf_pages": metadata_value(text, "PDF页码："),
        "section": metadata_value(text, "小节："),
        "body_chars": len(body),
        "has_table": "<table" in text,
        "has_code": "```" in text,
        "has_equation": "$$" in text,
        "issues": issues,
        "manifest_body_chars": manifest_entry.get("body_chars") if manifest_entry else None,
    }


def render_markdown(summary, records):
    lines = [
        "# 手册小节 Markdown 质检报告",
        "",
        f"- Markdown 文件数：{summary['markdown_count']}",
        f"- Manifest 小节数：{summary['manifest_section_count']}",
        f"- 来源标签缺失文件数：{summary['issue_counts'].get('missing_来源', 0)}",
        f"- 明显乱码候选数：{summary['issue_counts'].get('mojibake_candidate', 0)}",
        f"- 图片占位数：{summary['issue_counts'].get('image_placeholder', 0)}",
        f"- 空/极短小节数：{summary['issue_counts'].get('empty_body', 0) + summary['issue_counts'].get('short_body', 0)}",
        f"- 含表格小节：{summary['feature_counts']['table']}",
        f"- 含代码块小节：{summary['feature_counts']['code']}",
        f"- 含公式小节：{summary['feature_counts']['equation']}",
        "",
        "## 按文档统计",
        "",
        "| 文档 | 小节数 | 表格 | 代码 | 公式 | 问题数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for title, row in sorted(summary["by_document"].items()):
        lines.append(
            f"| {title} | {row['sections']} | {row['tables']} | {row['codes']} | {row['equations']} | {row['issues']} |"
        )

    issue_records = [record for record in records if record["issues"]]
    lines.extend(["", "## 问题候选", ""])
    if not issue_records:
        lines.append("未发现需要自动标记的问题。")
    else:
        for record in issue_records[:100]:
            lines.append(f"- `{record['path']}`: {', '.join(record['issues'])}")
        if len(issue_records) > 100:
            lines.append(f"- 其余 {len(issue_records) - 100} 条见 JSON 报告。")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Quality check generated manual section Markdown.")
    parser.add_argument("--output-root", default="processed_manual_sections")
    parser.add_argument("--manifest", default="processed_manual_sections/manual_sections_manifest.json")
    parser.add_argument("--json-report", default="processed_manual_sections/manual_sections_quality_report.json")
    parser.add_argument("--md-report", default="processed_manual_sections/manual_sections_quality_report.md")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    project_root = Path.cwd()
    output_root = project_root / args.output_root
    manifest = load_json(project_root / args.manifest)

    entries = {}
    document_by_path = {}
    for doc in manifest.get("documents", []):
        for section in doc.get("sections", []):
            output_file = section["output_file"]
            entries[output_file] = section
            document_by_path[output_file] = doc["pdf_name"]

    markdown_paths = sorted(output_root.glob("*/*.md"))
    records = []
    issue_counts = Counter()
    by_document = defaultdict(lambda: {"sections": 0, "tables": 0, "codes": 0, "equations": 0, "issues": 0})

    iterator = progress(markdown_paths, total=len(markdown_paths), desc="manual qc", unit="file", enabled=not args.no_progress)
    for path in iterator:
        rel = path.resolve().relative_to(project_root.resolve()).as_posix()
        record = scan_file(path, entries.get(rel))
        records.append(record)
        for issue in record["issues"]:
            issue_counts[issue] += 1
        doc_name = document_by_path.get(rel, "unknown")
        by_document[doc_name]["sections"] += 1
        by_document[doc_name]["tables"] += int(record["has_table"])
        by_document[doc_name]["codes"] += int(record["has_code"])
        by_document[doc_name]["equations"] += int(record["has_equation"])
        by_document[doc_name]["issues"] += len(record["issues"])

    summary = {
        "markdown_count": len(markdown_paths),
        "manifest_section_count": sum(doc.get("section_count", 0) for doc in manifest.get("documents", [])),
        "issue_counts": dict(issue_counts),
        "feature_counts": {
            "table": sum(1 for record in records if record["has_table"]),
            "code": sum(1 for record in records if record["has_code"]),
            "equation": sum(1 for record in records if record["has_equation"]),
        },
        "by_document": dict(by_document),
    }
    report = {"summary": summary, "records": records}
    write_json(project_root / args.json_report, report)
    Path(project_root / args.md_report).write_text(render_markdown(summary, records), encoding="utf-8", newline="\n")
    print(f"markdown_count={summary['markdown_count']} manifest_section_count={summary['manifest_section_count']}")
    print(f"issues={summary['issue_counts']}")
    print(f"json_report={project_root / args.json_report}")
    print(f"md_report={project_root / args.md_report}")


if __name__ == "__main__":
    main()
