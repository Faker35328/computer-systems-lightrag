import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - optional progress bar
    tqdm = None


REQUIRED_LABELS = ("课程：", "资料类型：", "来源：", "来源文件：", "PDF页码：", "章节：")
MOJIBAKE_PATTERNS = (
    "璇剧",
    "绗",
    "锛",
    "鍥",
    "æ",
    "ç",
    "è",
    "é",
    "ï¼",
    "�",
)
HEADER_FOOTER_PATTERNS = (
    "Tianjin University",
    "天津大学",
    "Copyright",
    "All rights reserved",
)
PLACEHOLDER_TEXT = "本页主要为图片或版式内容，MinerU 未抽取到可入库文本。"


def progress(iterable, *, total=None, desc="", unit="", enabled=True):
    if enabled and tqdm is not None:
        return tqdm(iterable, total=total, desc=desc, unit=unit, dynamic_ncols=True)
    return iterable


def rel(path, base):
    return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()


def load_manifest(output_root):
    manifest_path = output_root / "courseware_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def split_metadata_and_body(text):
    if "\n---\n" not in text:
        return text, ""
    meta, body = text.split("\n---\n", 1)
    return meta, body.strip()


def count_body_chars(body):
    lines = [line.strip() for line in body.splitlines()]
    useful = []
    for line in lines:
        if not line:
            continue
        if line.startswith("#"):
            continue
        useful.append(line)
    return len("\n".join(useful).strip())


def extract_pdf_page(meta):
    source_match = re.search(r"^来源：(.+)$", meta, re.MULTILINE)
    file_match = re.search(r"^来源文件：(.+)$", meta, re.MULTILINE)
    page_match = re.search(r"^PDF页码：(\d+)$", meta, re.MULTILINE)
    chapter_match = re.search(r"^章节：(.+)$", meta, re.MULTILINE)
    return {
        "source": source_match.group(1).strip() if source_match else "",
        "source_file": file_match.group(1).strip() if file_match else "",
        "page": int(page_match.group(1)) if page_match else None,
        "chapter": chapter_match.group(1).strip() if chapter_match else "",
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

    header_footer_hits = [pattern for pattern in HEADER_FOOTER_PATTERNS if pattern in body]
    if header_footer_hits:
        issues["header_footer_hits"] = header_footer_hits

    body_chars = count_body_chars(body)
    if not body.strip():
        issues["empty_body"] = True
    if body_chars < 30:
        issues["short_body_chars"] = body_chars
    if PLACEHOLDER_TEXT in body:
        issues["image_only_placeholder"] = True

    return issues


def analyze_file(path, output_root):
    text = path.read_text(encoding="utf-8")
    meta, body = split_metadata_and_body(text)
    source_info = extract_pdf_page(meta)
    issues = detect_issues(text, meta, body)
    return {
        "path": rel(path, output_root.parent),
        "source": source_info["source"],
        "source_file": source_info["source_file"],
        "page": source_info["page"],
        "chapter": source_info["chapter"],
        "chars": len(text),
        "body_chars": count_body_chars(body),
        "has_table": "<table" in body.lower(),
        "has_code": "```" in body,
        "has_equation": "$$" in body or "\\(" in body or "\\[" in body,
        "issues": issues,
    }


def summarize(records, manifest):
    issue_counts = Counter()
    for record in records:
        for issue_name in record["issues"]:
            issue_counts[issue_name] += 1

    by_pdf = defaultdict(lambda: {"pages": 0, "tables": 0, "codes": 0, "equations": 0, "issues": Counter()})
    for record in records:
        key = record["source_file"] or "UNKNOWN"
        by_pdf[key]["pages"] += 1
        by_pdf[key]["tables"] += int(record["has_table"])
        by_pdf[key]["codes"] += int(record["has_code"])
        by_pdf[key]["equations"] += int(record["has_equation"])
        for issue_name in record["issues"]:
            by_pdf[key]["issues"][issue_name] += 1

    return {
        "manifest_pdf_count": manifest.get("pdf_count"),
        "manifest_total_pages": manifest.get("total_pages"),
        "markdown_file_count": len(records),
        "issue_counts": dict(sorted(issue_counts.items())),
        "feature_counts": {
            "pages_with_table": sum(1 for record in records if record["has_table"]),
            "pages_with_code": sum(1 for record in records if record["has_code"]),
            "pages_with_equation": sum(1 for record in records if record["has_equation"]),
        },
        "by_pdf": {
            key: {
                "pages": value["pages"],
                "tables": value["tables"],
                "codes": value["codes"],
                "equations": value["equations"],
                "issues": dict(sorted(value["issues"].items())),
            }
            for key, value in sorted(by_pdf.items())
        },
    }


def issue_records(records, issue_name, limit=50):
    rows = []
    for record in records:
        if issue_name in record["issues"]:
            rows.append(
                {
                    "path": record["path"],
                    "source": record["source"],
                    "page": record["page"],
                    "detail": record["issues"][issue_name],
                    "body_chars": record["body_chars"],
                }
            )
    return rows[:limit]


def write_markdown_report(report, output_path):
    summary = report["summary"]
    lines = [
        "# 课件 Markdown 质检报告",
        "",
        "## 总览",
        "",
        f"- Markdown 文件数：{summary['markdown_file_count']}",
        f"- Manifest PDF 数：{summary['manifest_pdf_count']}",
        f"- Manifest 总页数：{summary['manifest_total_pages']}",
        f"- 含表格页面：{summary['feature_counts']['pages_with_table']}",
        f"- 含代码块页面：{summary['feature_counts']['pages_with_code']}",
        f"- 含公式页面：{summary['feature_counts']['pages_with_equation']}",
        "",
        "## 问题统计",
        "",
    ]

    if summary["issue_counts"]:
        for name, count in summary["issue_counts"].items():
            lines.append(f"- {name}: {count}")
    else:
        lines.append("- 未发现问题。")

    lines.extend(["", "## 重点验收项", ""])
    checks = [
        ("来源标签缺失", "missing_labels"),
        ("明显乱码", "mojibake_hits"),
        ("图片占位", "image_placeholder"),
        ("页眉页脚残留候选", "header_footer_hits"),
        ("空正文", "empty_body"),
        ("极短正文", "short_body_chars"),
        ("纯图片占位说明", "image_only_placeholder"),
    ]
    for label, issue in checks:
        count = summary["issue_counts"].get(issue, 0)
        lines.append(f"- {label}: {count}")

    for issue in ("missing_labels", "mojibake_hits", "image_placeholder", "header_footer_hits", "empty_body", "short_body_chars", "image_only_placeholder"):
        rows = report["samples"].get(issue, [])
        if not rows:
            continue
        lines.extend(["", f"## {issue} 样例", ""])
        for row in rows[:20]:
            detail = row.get("detail")
            lines.append(f"- `{row['path']}` | page={row.get('page')} | body_chars={row.get('body_chars')} | detail={detail}")

    lines.extend(["", "## 抽查章节", ""])
    for item in report["spot_checks"]:
        lines.append(f"- `{item['path']}` | exists={item['exists']} | notes={item['notes']}")

    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8", newline="\n")


def build_spot_checks(output_root):
    candidates = [
        "计算机系统基础1/02._环境与工具/计算机系统基础1_02._环境与工具_p001.md",
        "计算机系统基础1/13._存储器层次结构/计算机系统基础1_13._存储器层次结构_p019.md",
        "计算机系统基础2/02._异常/计算机系统基础2_02._异常_p001.md",
        "计算机系统基础2/05._虚拟内存/计算机系统基础2_05._虚拟内存_p022.md",
        "计算机系统基础2/14._同步：进阶/计算机系统基础2_14._同步：进阶_p027.md",
    ]
    rows = []
    for candidate in candidates:
        path = output_root / Path(candidate)
        notes = []
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if any(pattern in text for pattern in MOJIBAKE_PATTERNS):
                notes.append("has_mojibake_candidate")
            if "![](images/" in text:
                notes.append("has_image_placeholder")
            for label in REQUIRED_LABELS:
                if label not in text:
                    notes.append(f"missing_{label}")
            if "<table" in text.lower():
                notes.append("has_table")
            if "```" in text:
                notes.append("has_code")
            if "$$" in text:
                notes.append("has_equation")
        rows.append({"path": candidate, "exists": path.exists(), "notes": notes or ["ok"]})
    return rows


def main():
    parser = argparse.ArgumentParser(description="Quality scan generated courseware Markdown files.")
    parser.add_argument("--output-root", default="processed_markdown_v2")
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    output_root = Path(args.output_root)
    manifest = load_manifest(output_root)
    markdown_files = sorted(path for path in output_root.rglob("*.md") if path.name != "quality_report.md")
    records = [
        analyze_file(path, output_root)
        for path in progress(markdown_files, total=len(markdown_files), desc="scan markdown", unit="file", enabled=args.progress)
    ]
    summary = summarize(records, manifest)
    samples = {
        issue: issue_records(records, issue)
        for issue in (
            "missing_labels",
            "mojibake_hits",
            "image_placeholder",
            "header_footer_hits",
            "empty_body",
            "short_body_chars",
            "image_only_placeholder",
        )
    }
    report = {
        "summary": summary,
        "samples": samples,
        "spot_checks": build_spot_checks(output_root),
    }

    json_path = output_root / "quality_report.json"
    md_path = output_root / "quality_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    write_markdown_report(report, md_path)

    print(f"markdown_file_count={summary['markdown_file_count']}")
    print(f"issue_counts={json.dumps(summary['issue_counts'], ensure_ascii=False, sort_keys=True)}")
    print(f"pages_with_table={summary['feature_counts']['pages_with_table']}")
    print(f"pages_with_code={summary['feature_counts']['pages_with_code']}")
    print(f"pages_with_equation={summary['feature_counts']['pages_with_equation']}")
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")


if __name__ == "__main__":
    main()
