import argparse
import json
import re
from pathlib import Path

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - optional progress bar
    tqdm = None


SKIP_TYPES = {
    "header",
    "footer",
    "page_header",
    "page_footer",
    "page_number",
}

TEXT_TRANSLATION = str.maketrans(
    {
        "\uf070": "-",
        "\uf06e": "-",
        "\uf06c": "-",
        "\uf06f": "-",
        "\u25fc": "-",
    }
)
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
CHAPTER_RE = re.compile(r"chapter(\d+)", re.IGNORECASE)
SECTION_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)+\b")


def progress(iterable, *, total=None, desc="", unit="", enabled=True):
    if enabled and tqdm is not None:
        return tqdm(iterable, total=total, desc=desc, unit=unit, dynamic_ncols=True)
    return iterable


def compact_text(value):
    if value is None:
        return ""
    text = str(value).translate(TEXT_TRANSLATION).replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def safe_name(value, max_len=96):
    text = compact_text(value)
    text = INVALID_FILENAME_CHARS.sub("_", text)
    text = re.sub(r"\s+", "_", text).strip("._ ")
    return (text[:max_len].strip("._ ") or "untitled")


def relative_posix(path, base):
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def discover_textbook_content_lists(mineru_root):
    paths = []
    for path in Path(mineru_root).glob("*chapter*/*/auto/*_content_list.json"):
        match = CHAPTER_RE.search(str(path))
        if match:
            paths.append((int(match.group(1)), path))
    return [path for _, path in sorted(paths)]


def filter_content_lists(paths, start_chapter=None, end_chapter=None):
    filtered = []
    for path in paths:
        chapter_number = infer_chapter_number(path)
        if chapter_number is None:
            continue
        if start_chapter is not None and chapter_number < start_chapter:
            continue
        if end_chapter is not None and chapter_number > end_chapter:
            continue
        filtered.append(path)
    return filtered


def infer_chapter_number(path):
    match = CHAPTER_RE.search(str(path))
    return int(match.group(1)) if match else None


def item_page(item):
    value = item.get("page_idx")
    return int(value) if isinstance(value, int) or str(value).isdigit() else None


def load_items(path):
    data = read_json(path)
    if not isinstance(data, list):
        raise ValueError(f"content_list must be a list: {path}")
    return [item for item in data if isinstance(item, dict)]


def page_number_map(items):
    mapping = {}
    for item in items:
        if item.get("type") != "page_number":
            continue
        page = item_page(item)
        text = compact_text(item.get("text"))
        if page is None or not text:
            continue
        match = re.search(r"\d+", text)
        if match:
            mapping[page] = int(match.group(0))
    return mapping


def is_heading_item(item):
    if item.get("type") != "text" or item.get("text_level") is None:
        return False
    text = compact_text(item.get("text"))
    if not text:
        return False
    if len(text) > 140:
        return False
    letters = sum(1 for char in text if char.isalpha())
    digits = sum(1 for char in text if char.isdigit())
    if letters == 0:
        return False
    if digits > letters * 3 and not SECTION_NUMBER_RE.match(text):
        return False
    return True


def heading_level(text, raw_level):
    text = compact_text(text)
    try:
        level = int(raw_level)
    except (TypeError, ValueError):
        level = 0
    if level <= 1:
        return 2
    if SECTION_NUMBER_RE.match(text):
        return 3
    if text.startswith(("Practice Problem", "Homework Problem")):
        return 4
    if text.startswith(("Aside ", "New to C?", "Web Aside")):
        return 4
    return min(max(level + 1, 3), 6)


def render_caption_list(values):
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    return [text for text in (compact_text(value) for value in values) if text]


def render_table(item):
    parts = []
    captions = render_caption_list(item.get("table_caption"))
    footnotes = render_caption_list(item.get("table_footnote"))
    body = item.get("table_body") or item.get("html") or item.get("content") or ""
    if captions:
        parts.append("Table caption: " + " / ".join(captions))
    if body:
        parts.append(str(body).strip())
    if footnotes:
        parts.append("Table footnote: " + " / ".join(footnotes))
    return "\n\n".join(part for part in parts if part)


def infer_code_language(item):
    sub_type = compact_text(item.get("sub_type")).lower()
    body = str(item.get("code_body") or item.get("text") or item.get("content") or "")
    body_lower = body.lower()
    if "python" in sub_type:
        return "python"
    if "asm" in sub_type or "assembly" in sub_type:
        return "asm"
    if "batch" in sub_type or "shell" in sub_type:
        return "bash"
    if "java" in sub_type:
        return "java"
    if "algorithm" in sub_type:
        return "text"
    if "#include" in body_lower or "int main" in body_lower:
        return "c"
    if any(token in body_lower for token in ("movq", "cmpq", "jmp", "retq", "%rax")):
        return "asm"
    return "text"


def render_code(item):
    captions = render_caption_list(item.get("code_caption"))
    footnotes = render_caption_list(item.get("code_footnote"))
    body = str(item.get("code_body") or item.get("text") or item.get("content") or "").strip()
    parts = []
    if captions:
        parts.append("Code caption: " + " / ".join(captions))
    if body:
        if body.startswith("```"):
            parts.append(body)
        else:
            parts.append(f"```{infer_code_language(item)}\n{body}\n```")
    if footnotes:
        parts.append("Code footnote: " + " / ".join(footnotes))
    return "\n\n".join(part for part in parts if part)


def render_equation(item):
    for key in ("text", "latex", "equation", "content"):
        text = str(item.get(key) or "").strip()
        if text:
            return text
    return ""


def render_media(item, label):
    captions = render_caption_list(item.get(f"{label}_caption"))
    footnotes = render_caption_list(item.get(f"{label}_footnote"))
    content = compact_text(item.get("content") or item.get("text"))
    lines = captions + footnotes
    if content:
        lines.append(content)
    if not lines:
        return ""
    title = "Chart note" if label == "chart" else "Image note"
    return f"{title}: " + " / ".join(lines)


def render_footnote(item):
    text = compact_text(item.get("text") or item.get("content"))
    return f"Footnote: {text}" if text else ""


def render_item(item):
    item_type = item.get("type")
    if item_type in SKIP_TYPES:
        return ""
    if item_type == "text":
        text = compact_text(item.get("text") or item.get("content"))
        if not text:
            return ""
        if text.upper() == "CHAPTER":
            return ""
        if item.get("text_level") is not None:
            return f"{'#' * heading_level(text, item.get('text_level'))} {text}"
        return text
    if item_type == "table":
        return render_table(item)
    if item_type == "code":
        return render_code(item)
    if item_type == "equation":
        return render_equation(item)
    if item_type == "chart":
        return render_media(item, "chart")
    if item_type == "image":
        return render_media(item, "image")
    if item_type == "page_footnote":
        return render_footnote(item)
    return compact_text(item.get("text") or item.get("content"))


def section_source(chapter_number, chapter_title, section_title, pages, printed_pages, pdf_name):
    page_values = [printed_pages[page] for page in pages if page in printed_pages]
    if page_values:
        page_label = (
            f"pp. {min(page_values)}-{max(page_values)}"
            if min(page_values) != max(page_values)
            else f"p. {min(page_values)}"
        )
    else:
        page_label = (
            f"PDF slice pages {min(pages) + 1}-{max(pages) + 1}"
            if pages
            else "PDF slice pages unknown"
        )
    return (
        f"CSAPP 3e: Chapter {chapter_number} {chapter_title} - "
        f"{section_title} ({page_label})"
    )


def page_range_label(pages, printed_pages):
    page_values = [printed_pages[page] for page in pages if page in printed_pages]
    if page_values:
        return (
            f"{min(page_values)}-{max(page_values)}"
            if min(page_values) != max(page_values)
            else str(min(page_values))
        )
    if pages:
        return (
            f"PDF slice {min(pages) + 1}-{max(pages) + 1}"
            if min(pages) != max(pages)
            else f"PDF slice {min(pages) + 1}"
        )
    return "unknown"


def collect_sections(items, chapter_number):
    sections = []
    current = None
    chapter_title = None
    first_heading_title = None

    def has_renderable_items(section):
        return any(render_item(item) for item in section["items"])

    for item in items:
        if item.get("type") in SKIP_TYPES:
            continue
        if is_heading_item(item):
            title = compact_text(item.get("text"))
            raw_level = item.get("text_level")
            if first_heading_title is None:
                first_heading_title = title
            if chapter_title is None and int(raw_level or 0) == 1:
                chapter_title = title

            if current and current["items"] and has_renderable_items(current):
                sections.append(current)
            current = {
                "title": title,
                "raw_level": raw_level,
                "items": [item],
            }
            continue

        if current is None:
            if not render_item(item):
                continue
            current = {
                "title": f"Chapter {chapter_number} Preface",
                "raw_level": 1,
                "items": [],
            }
        current["items"].append(item)

    if current and current["items"] and has_renderable_items(current):
        sections.append(current)

    if chapter_title is None:
        chapter_title = first_heading_title
    if chapter_title is None and sections:
        chapter_title = sections[0]["title"]
    return chapter_title or f"Chapter {chapter_number}", sections


def build_markdown(section, chapter_number, chapter_title, printed_pages, pdf_name):
    parts = [render_item(item) for item in section["items"]]
    parts = [part for part in parts if part]
    pages = sorted(
        {
            page
            for page in (item_page(item) for item in section["items"])
            if page is not None
        }
    )
    source = section_source(
        chapter_number,
        chapter_title,
        section["title"],
        pages,
        printed_pages,
        pdf_name,
    )
    page_label = page_range_label(pages, printed_pages)
    title = f"CSAPP 3e Chapter {chapter_number} - {section['title']}"
    metadata = [
        f"# {title}",
        "",
        "课程：计算机系统基础",
        "资料类型：教材",
        f"来源：{source}",
        f"来源文件：教材/{pdf_name}",
        f"教材页码：{page_label}",
        f"章节：Chapter {chapter_number} {chapter_title}",
        f"小节：{section['title']}",
        "",
        "---",
        "",
    ]
    return "\n".join(metadata + parts).strip() + "\n", source, page_label, pages


def generate_for_content_list(path, output_root, project_root):
    chapter_number = infer_chapter_number(path)
    if chapter_number is None:
        raise ValueError(f"cannot infer chapter number from path: {path}")
    items = load_items(path)
    printed_pages = page_number_map(items)
    chapter_title, sections = collect_sections(items, chapter_number)
    auto_dir = path.parent
    pdf_name = "Computer.Systems.A.Programmes.Perpective.3e(1).pdf"
    chapter_dir = output_root / f"chapter_{chapter_number:02d}_{safe_name(chapter_title, 64)}"
    chapter_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    for index, section in enumerate(sections, 1):
        markdown, source, page_label, pages = build_markdown(
            section,
            chapter_number,
            chapter_title,
            printed_pages,
            pdf_name,
        )
        filename = f"ch{chapter_number:02d}_sec{index:03d}_{safe_name(section['title'], 72)}.md"
        output_path = chapter_dir / filename
        output_path.write_text(markdown, encoding="utf-8", newline="\n")
        entries.append(
            {
                "chapter_number": chapter_number,
                "chapter_title": chapter_title,
                "section_index": index,
                "section_title": section["title"],
                "source": source,
                "source_file": f"教材/{pdf_name}",
                "page_label": page_label,
                "slice_page_start": min(pages) + 1 if pages else None,
                "slice_page_end": max(pages) + 1 if pages else None,
                "item_count": len(section["items"]),
                "output_file": relative_posix(output_path, project_root),
                "content_list": relative_posix(path, project_root),
                "mineru_markdown": relative_posix(auto_dir / "Computer.Systems.A.Programmes.Perpective.3e(1).md", project_root),
            }
        )
    return {
        "chapter_number": chapter_number,
        "chapter_title": chapter_title,
        "content_list": relative_posix(path, project_root),
        "section_count": len(entries),
        "sections": entries,
    }


def merge_manifest_documents(manifest_path, selected_chapters, new_documents):
    new_by_chapter = {doc["chapter_number"]: doc for doc in new_documents}
    documents = []
    existing_manifest = None
    if manifest_path.exists():
        existing_manifest = read_json(manifest_path)
        for doc in existing_manifest.get("documents", []):
            chapter_number = doc.get("chapter_number")
            if chapter_number not in selected_chapters:
                documents.append(doc)

    documents.extend(new_by_chapter.values())
    documents.sort(key=lambda doc: doc.get("chapter_number") or 0)
    total_sections = sum(int(doc.get("section_count") or 0) for doc in documents)
    manifest = {
        "textbook": (
            existing_manifest.get("textbook")
            if existing_manifest
            else "Computer Systems: A Programmer's Perspective, 3e"
        ),
        "source_file": (
            existing_manifest.get("source_file")
            if existing_manifest
            else "知识库/教材/Computer.Systems.A.Programmes.Perpective.3e(1).pdf"
        ),
        "documents": documents,
        "chapter_count": len(documents),
        "section_count": total_sections,
    }
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate CSAPP textbook section Markdown from MinerU content_list.json.")
    parser.add_argument("--mineru-root", default="mineru_output")
    parser.add_argument("--output-root", default="processed_textbook_sections")
    parser.add_argument("--manifest", default="processed_textbook_sections/textbook_sections_manifest.json")
    parser.add_argument("--start-chapter", type=int)
    parser.add_argument("--end-chapter", type=int)
    parser.add_argument("--merge-manifest", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if (
        args.start_chapter is not None
        and args.end_chapter is not None
        and args.start_chapter > args.end_chapter
    ):
        raise SystemExit("--start-chapter cannot be greater than --end-chapter")

    project_root = Path.cwd()
    output_root = project_root / args.output_root
    output_root.mkdir(parents=True, exist_ok=True)
    content_lists = discover_textbook_content_lists(project_root / args.mineru_root)
    content_lists = filter_content_lists(content_lists, args.start_chapter, args.end_chapter)
    if not content_lists:
        raise SystemExit("no textbook content_list.json files found")

    new_documents = []
    iterator = progress(content_lists, total=len(content_lists), desc="textbook", unit="chapter", enabled=args.progress)
    for content_list in iterator:
        doc = generate_for_content_list(content_list, output_root, project_root)
        new_documents.append(doc)
        print(
            f"generated chapter={doc['chapter_number']} sections={doc['section_count']} "
            f"title={doc['chapter_title']}",
            flush=True,
        )

    selected_chapters = {doc["chapter_number"] for doc in new_documents}
    manifest_path = project_root / args.manifest
    if args.merge_manifest:
        manifest = merge_manifest_documents(manifest_path, selected_chapters, new_documents)
    else:
        new_documents.sort(key=lambda doc: doc.get("chapter_number") or 0)
        manifest = {
            "textbook": "Computer Systems: A Programmer's Perspective, 3e",
            "source_file": "知识库/教材/Computer.Systems.A.Programmes.Perpective.3e(1).pdf",
            "documents": new_documents,
            "chapter_count": len(new_documents),
            "section_count": sum(int(doc.get("section_count") or 0) for doc in new_documents),
        }
    write_json(manifest_path, manifest)
    print(f"manifest={manifest_path}", flush=True)
    print(f"chapter_count={manifest['chapter_count']} section_count={manifest['section_count']}", flush=True)


if __name__ == "__main__":
    main()
