import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - optional progress bar
    tqdm = None


COURSE_DIRS = ("计算机系统基础1", "计算机系统基础2")
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


def safe_name(value):
    text = compact_text(value)
    text = INVALID_FILENAME_CHARS.sub("_", text)
    text = re.sub(r"\s+", "_", text).strip("._ ")
    return text or "untitled"


def relative_posix(path, base):
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def find_content_list(mineru_root, course_dir, pdf_stem):
    candidates = [
        mineru_root / course_dir / pdf_stem / "auto" / f"{pdf_stem}_content_list.json",
        mineru_root / pdf_stem / "auto" / f"{pdf_stem}_content_list.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def discover_courseware(pdf_root, mineru_root):
    docs = []
    for course_dir in COURSE_DIRS:
        course_path = pdf_root / course_dir
        if not course_path.exists():
            continue
        for pdf_path in sorted(course_path.glob("*.pdf")):
            stem = pdf_path.stem
            content_list = find_content_list(mineru_root, course_dir, stem)
            docs.append(
                {
                    "course_dir": course_dir,
                    "pdf_path": pdf_path,
                    "pdf_name": pdf_path.name,
                    "stem": stem,
                    "chapter": stem,
                    "content_list": content_list,
                }
            )
    return docs


def load_content_items(content_list_path):
    data = json.loads(content_list_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"content_list must be a list: {content_list_path}")
    return data


def group_by_page(items):
    pages = defaultdict(list)
    for item in items:
        if not isinstance(item, dict) or "page_idx" not in item:
            continue
        pages[int(item["page_idx"])].append(item)
    return dict(sorted(pages.items()))


def markdown_heading(text, text_level):
    text = compact_text(text)
    if not text:
        return ""
    try:
        level = int(text_level)
    except (TypeError, ValueError):
        level = 0
    if level <= 0:
        return text
    heading_level = min(max(level + 1, 2), 6)
    return f"{'#' * heading_level} {text}"


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
        parts.append("表格说明：" + "；".join(captions))
    if body:
        parts.append(str(body).strip())
    if footnotes:
        parts.append("表格注释：" + "；".join(footnotes))
    return "\n\n".join(part for part in parts if part)


def infer_code_language(item):
    sub_type = compact_text(item.get("sub_type")).lower()
    body = item.get("code_body") or item.get("text") or item.get("content") or ""
    body_lower = str(body).lower()
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
    body = item.get("code_body") or item.get("text") or item.get("content") or ""
    body = str(body).strip()
    if not body:
        return ""
    language = infer_code_language(item)
    return f"```{language}\n{body}\n```"


def render_equation(item):
    candidates = [
        item.get("text"),
        item.get("latex"),
        item.get("equation"),
        item.get("content"),
    ]
    for candidate in candidates:
        text = str(candidate).strip() if candidate is not None else ""
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
    title = "图表文字说明" if label == "chart" else "图片文字说明"
    return f"{title}：" + "；".join(lines)


def render_item(item):
    item_type = item.get("type")
    if item_type in SKIP_TYPES:
        return ""
    if item_type == "text":
        return markdown_heading(item.get("text") or item.get("content"), item.get("text_level"))
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
    return compact_text(item.get("text") or item.get("content"))


def build_page_markdown(items, *, course_name, course_dir, material_type, pdf_name, chapter, page_number):
    source_prefix = f"{course_dir}：{pdf_name}"
    source_file = f"{course_dir}/{pdf_name}"
    header_lines = [
        f"# {chapter} - 第 {page_number:03d} 页",
        "",
        f"课程：{course_name}",
        f"资料类型：{material_type}",
        f"来源：{source_prefix} 第 {page_number} 页",
        f"来源文件：{source_file}",
        f"PDF页码：{page_number}",
        f"章节：{chapter}",
        "",
        "---",
    ]
    body = []
    for item in items:
        rendered = render_item(item)
        if rendered:
            body.append(rendered)
    if not body:
        body.append("本页主要为图片或版式内容，MinerU 未抽取到可入库文本。")
    return "\n".join(header_lines).strip() + "\n\n" + "\n\n".join(body).strip() + "\n"


def write_doc_pages(doc, pages, output_root, course_name, material_type, project_root, show_progress=True):
    course_dir = doc["course_dir"]
    stem = doc["stem"]
    output_dir = output_root / course_dir / safe_name(stem)
    output_dir.mkdir(parents=True, exist_ok=True)
    file_stem = f"{safe_name(course_dir)}_{safe_name(stem)}"
    output_files = []
    page_items = list(pages.items())

    for page_idx, items in progress(
        page_items,
        total=len(page_items),
        desc=f"{course_dir}/{stem}",
        unit="page",
        enabled=show_progress,
    ):
        page_number = page_idx + 1
        markdown = build_page_markdown(
            items,
            course_name=course_name,
            course_dir=course_dir,
            material_type=material_type,
            pdf_name=doc["pdf_name"],
            chapter=doc["chapter"],
            page_number=page_number,
        )
        out_path = output_dir / f"{file_stem}_p{page_number:03d}.md"
        out_path.write_text(markdown, encoding="utf-8", newline="\n")
        output_files.append(relative_posix(out_path, project_root))

    return output_dir, output_files


def build_manifest(docs, output_root, pdf_root, mineru_root, project_root, course_name, material_type, show_progress=True):
    entries = []
    total_pages = 0
    missing = []
    content_list_count = 0

    for doc in progress(docs, total=len(docs), desc="documents", unit="pdf", enabled=show_progress):
        content_list = doc["content_list"]
        if content_list is None:
            missing.append(doc)
            continue
        content_list_count += 1
        items = load_content_items(content_list)
        pages = group_by_page(items)
        output_dir, output_files = write_doc_pages(
            doc,
            pages,
            output_root,
            course_name,
            material_type,
            project_root,
            show_progress=show_progress,
        )
        total_pages += len(pages)
        entries.append(
            {
                "course": course_name,
                "course_dir": doc["course_dir"],
                "material_type": material_type,
                "pdf_name": doc["pdf_name"],
                "chapter": doc["chapter"],
                "pdf_path": relative_posix(doc["pdf_path"], project_root),
                "content_list": relative_posix(content_list, project_root),
                "output_dir": relative_posix(output_dir, project_root),
                "page_count": len(pages),
                "output_count": len(output_files),
                "source_prefix": f"{doc['course_dir']}：{doc['pdf_name']}",
                "source_file": f"{doc['course_dir']}/{doc['pdf_name']}",
                "output_files": output_files,
            }
        )

    manifest = {
        "course": course_name,
        "material_type": material_type,
        "pdf_root": relative_posix(pdf_root, project_root),
        "mineru_output": relative_posix(mineru_root, project_root),
        "output_root": relative_posix(output_root, project_root),
        "pdf_count": len(docs),
        "content_list_input_count": content_list_count,
        "missing_content_list_count": len(missing),
        "total_pages": total_pages,
        "total_output_files": sum(entry["output_count"] for entry in entries),
        "documents": entries,
        "missing": [
            {
                "course_dir": doc["course_dir"],
                "pdf_name": doc["pdf_name"],
                "pdf_path": relative_posix(doc["pdf_path"], project_root),
            }
            for doc in missing
        ],
    }
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate clean page-level Markdown from MinerU courseware content_list.json files.")
    parser.add_argument("--pdf-root", default="知识库")
    parser.add_argument("--mineru-output", default="mineru_output")
    parser.add_argument("--output-root", default="processed_markdown_v2")
    parser.add_argument("--course", default="计算机系统基础")
    parser.add_argument("--material-type", default="课件")
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    project_root = Path.cwd()
    pdf_root = Path(args.pdf_root)
    mineru_root = Path(args.mineru_output)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    docs = discover_courseware(pdf_root, mineru_root)
    manifest = build_manifest(
        docs,
        output_root,
        pdf_root,
        mineru_root,
        project_root,
        args.course,
        args.material_type,
        show_progress=args.progress,
    )

    manifest_path = output_root / "courseware_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    print(f"pdf_count={manifest['pdf_count']}")
    print(f"content_list_input_count={manifest['content_list_input_count']}")
    print(f"missing_content_list_count={manifest['missing_content_list_count']}")
    print(f"total_pages={manifest['total_pages']}")
    print(f"total_output_files={manifest['total_output_files']}")
    print(f"manifest={manifest_path}")
    if manifest["missing"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
