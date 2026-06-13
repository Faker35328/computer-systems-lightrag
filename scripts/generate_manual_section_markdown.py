import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - optional progress bar
    tqdm = None


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


SKIP_TYPES = {"header", "footer", "page_header", "page_footer", "page_number"}
REPEATED_TITLES = {
    "INTEL 80386",
    "PROGRAMMER'S REFERENCE MANUAL 1986",
    "INTEL 80386 PROGRAMMER'S REFERENCE MANUAL 1986",
}


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


def relative_posix(path, root):
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def safe_name(value, max_len=90):
    value = re.sub(r"\s+", "_", value.strip())
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"_+", "_", value).strip("._ ")
    return (value or "section")[:max_len]


def clean_text(value):
    if value is None:
        return ""
    value = str(value).replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def flatten_text(value):
    if isinstance(value, list):
        return "\n".join(clean_text(item) for item in value if clean_text(item))
    return clean_text(value)


def item_page(item):
    page = item.get("page_idx")
    return int(page) + 1 if isinstance(page, int) else None


def text_value(item):
    return clean_text(item.get("text"))


def is_repeated_title(text):
    normalized = re.sub(r"\s+", " ", text.strip())
    return normalized in REPEATED_TITLES


def is_section_heading(item):
    if item.get("type") != "text":
        return False
    level = item.get("text_level")
    if level not in (1, 2):
        return False
    text = text_value(item)
    if not text or is_repeated_title(text):
        return False
    return True


def render_caption(label, value):
    text = flatten_text(value)
    return f"**{label}:** {text}" if text else ""


def render_item(item):
    item_type = item.get("type")
    if item_type in SKIP_TYPES:
        return ""

    if item_type in {"text", "aside_text"}:
        text = text_value(item)
        if not text or is_repeated_title(text):
            return ""
        level = item.get("text_level")
        if level in (1, 2):
            depth = min(6, int(level) + 2)
            return f"{'#' * depth} {text}"
        return text

    if item_type == "table":
        parts = [
            render_caption("表格标题", item.get("table_caption")),
            clean_text(item.get("table_body")),
            render_caption("表格脚注", item.get("table_footnote")),
        ]
        return "\n\n".join(part for part in parts if part)

    if item_type == "code":
        parts = [render_caption("代码标题", item.get("code_caption"))]
        code = clean_text(item.get("code_body"))
        if code:
            if code.startswith("```"):
                parts.append(code)
            else:
                parts.append(f"```text\n{code}\n```")
        parts.append(render_caption("代码脚注", item.get("code_footnote")))
        return "\n\n".join(part for part in parts if part)

    if item_type == "equation":
        text = clean_text(item.get("text"))
        if not text:
            return ""
        if item.get("text_format") == "latex":
            return f"$$\n{text}\n$$"
        return text

    if item_type in {"chart", "image"}:
        parts = []
        if item_type == "chart":
            parts.append(render_caption("图表标题", item.get("chart_caption")))
            parts.append(clean_text(item.get("content")))
            parts.append(render_caption("图表脚注", item.get("chart_footnote")))
        else:
            parts.append(render_caption("图片标题", item.get("image_caption")))
            parts.append(render_caption("图片脚注", item.get("image_footnote")))
        return "\n\n".join(part for part in parts if part)

    return ""


def has_renderable_items(items):
    return any(render_item(item) for item in items)


def collect_sections(items, default_title):
    sections = []
    current = {"title": default_title, "items": []}

    for item in items:
        if item.get("type") in SKIP_TYPES:
            continue
        if item.get("type") == "text" and is_repeated_title(text_value(item)):
            continue

        if is_section_heading(item) and has_renderable_items(current["items"]):
            sections.append(current)
            current = {"title": text_value(item), "items": [item]}
        else:
            if not current["items"] and is_section_heading(item):
                current["title"] = text_value(item)
            current["items"].append(item)

    if has_renderable_items(current["items"]):
        sections.append(current)

    return sections


def page_label(pages):
    pages = sorted(page for page in pages if page is not None)
    if not pages:
        return "unknown"
    return str(pages[0]) if pages[0] == pages[-1] else f"{pages[0]}-{pages[-1]}"


def manual_title(pdf_stem):
    return "i386手册勘误" if "勘误" in pdf_stem else "i386手册"


def build_markdown(section, pdf_stem, pdf_name):
    title = section["title"]
    parts = [render_item(item) for item in section["items"]]
    body = "\n\n".join(part for part in parts if part).strip()
    pages = {item_page(item) for item in section["items"]}
    pages_label = page_label(pages)
    manual = manual_title(pdf_stem)
    source = f"{manual}: {pdf_name} - {title} (PDF pp. {pages_label})"
    header = [
        f"# {manual} - {title}",
        "",
        "课程：计算机系统基础",
        "资料类型：手册",
        f"来源：{source}",
        f"来源文件：手册/{pdf_name}",
        f"PDF页码：{pages_label}",
        f"章节：{manual}",
        f"小节：{title}",
        "",
        "---",
        "",
    ]
    return "\n".join(header) + body.strip() + "\n", source, pages_label, len(body)


def discover_content_lists(mineru_root):
    return sorted(Path(mineru_root).glob("*/auto/*_content_list.json"))


def generate_for_content_list(path, output_root, project_root):
    path = Path(path)
    pdf_stem = path.name[: -len("_content_list.json")]
    pdf_name = f"{pdf_stem}.pdf"
    items = load_json(path)
    if not isinstance(items, list):
        raise ValueError(f"content_list must be a list: {path}")

    sections = collect_sections(items, manual_title(pdf_stem))
    output_dir = Path(output_root) / safe_name(pdf_stem, 120)
    output_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    feature_counts = defaultdict(int)
    for index, section in enumerate(sections, 1):
        markdown, source, pages, body_chars = build_markdown(section, pdf_stem, pdf_name)
        filename = f"{safe_name(pdf_stem, 50)}_sec{index:03d}_{safe_name(section['title'], 70)}.md"
        out_path = output_dir / filename
        out_path.write_text(markdown, encoding="utf-8", newline="\n")

        rendered = "\n".join(render_item(item) for item in section["items"])
        if "<table" in rendered:
            feature_counts["tables"] += 1
        if "```" in rendered:
            feature_counts["codes"] += 1
        if "$$" in rendered:
            feature_counts["equations"] += 1

        entries.append(
            {
                "section_index": index,
                "section_title": section["title"],
                "output_file": relative_posix(out_path, project_root),
                "file_source": source,
                "page_label": pages,
                "body_chars": body_chars,
                "item_count": len(section["items"]),
            }
        )

    return {
        "manual_title": manual_title(pdf_stem),
        "pdf_name": pdf_name,
        "source_pdf": f"知识库/手册/{pdf_name}",
        "content_list": relative_posix(path, project_root),
        "output_dir": relative_posix(output_dir, project_root),
        "section_count": len(entries),
        "feature_counts": dict(feature_counts),
        "sections": entries,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate section-level Markdown from MinerU i386 manual outputs.")
    parser.add_argument("--mineru-root", default="mineru_output/手册")
    parser.add_argument("--output-root", default="processed_manual_sections")
    parser.add_argument("--manifest", default="processed_manual_sections/manual_sections_manifest.json")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    project_root = Path.cwd()
    content_lists = discover_content_lists(project_root / args.mineru_root)
    if not content_lists:
        raise SystemExit("no manual content_list.json files found")

    documents = []
    iterator = progress(content_lists, total=len(content_lists), desc="manual", unit="pdf", enabled=not args.no_progress)
    for content_list in iterator:
        doc = generate_for_content_list(content_list, project_root / args.output_root, project_root)
        documents.append(doc)
        print(f"generated {doc['pdf_name']} sections={doc['section_count']}", flush=True)

    manifest = {
        "material_type": "manual",
        "manual_count": len(documents),
        "section_count": sum(doc["section_count"] for doc in documents),
        "documents": documents,
    }
    write_json(project_root / args.manifest, manifest)
    print(f"manual_count={manifest['manual_count']} section_count={manifest['section_count']}", flush=True)
    print(f"manifest={project_root / args.manifest}", flush=True)


if __name__ == "__main__":
    main()
