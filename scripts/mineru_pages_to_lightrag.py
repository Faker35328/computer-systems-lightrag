import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - optional CLI nicety
    tqdm = None


SKIP_TYPES = {"header", "footer", "page_header", "page_footer"}


def progress_iter(iterable, *, total=None, desc=None, unit=None, enabled=True):
    if enabled and tqdm is not None:
        return tqdm(iterable, total=total, desc=desc, unit=unit, dynamic_ncols=True)
    return iterable


def compact_text(value):
    if value is None:
        return ""
    text = str(value).replace("\uf070", "-")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def markdown_heading(text, level):
    text = compact_text(text)
    if not text:
        return ""
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 0
    hashes = "#" * min(max(level + 1, 2), 4) if level else ""
    return f"{hashes} {text}" if hashes else text


def render_table(item):
    caption = "\n".join(compact_text(x) for x in item.get("table_caption", []) if compact_text(x))
    body = item.get("table_body") or ""
    footnote = "\n".join(compact_text(x) for x in item.get("table_footnote", []) if compact_text(x))
    parts = []
    if caption:
        parts.append(f"表格说明：{caption}")
    if body:
        parts.append(body.strip())
    if footnote:
        parts.append(f"表格注释：{footnote}")
    return "\n\n".join(parts)


def render_code(item):
    body = item.get("code_body") or ""
    body = body.strip()
    if not body:
        return ""
    sub_type = item.get("sub_type") or ""
    language = "text"
    lowered = sub_type.lower()
    if "python" in lowered:
        language = "python"
    elif "c" in lowered or "algorithm" in lowered:
        language = "c"
    elif "asm" in lowered or "assembly" in lowered:
        language = "asm"
    return f"```{language}\n{body}\n```"


def render_captioned_media(item, label):
    captions = item.get(f"{label}_caption", []) or []
    footnotes = item.get(f"{label}_footnote", []) or []
    content = compact_text(item.get("content", ""))
    lines = []
    for caption in captions:
        text = compact_text(caption)
        if text:
            lines.append(text)
    for footnote in footnotes:
        text = compact_text(footnote)
        if text:
            lines.append(text)
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
        return markdown_heading(item.get("text", ""), item.get("text_level"))
    if item_type == "table":
        return render_table(item)
    if item_type == "code":
        return render_code(item)
    if item_type == "chart":
        return render_captioned_media(item, "chart")
    if item_type == "image":
        return render_captioned_media(item, "image")
    text = compact_text(item.get("text") or item.get("content") or "")
    return text


def load_pages(content_list_path):
    data = json.loads(Path(content_list_path).read_text(encoding="utf-8"))
    pages = defaultdict(list)
    for item in data:
        if "page_idx" not in item:
            continue
        pages[int(item["page_idx"])].append(item)
    return dict(sorted(pages.items()))


def build_page_markdown(items, page_number, course, material_type, source_file, source_prefix, chapter):
    source_label = f"{source_prefix} 第 {page_number} 页"
    header = [
        f"# {chapter} - 第 {page_number:03d} 页",
        "",
        f"课程：{course}",
        f"资料类型：{material_type}",
        f"来源：{source_label}",
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
        body.append("本页主要为图片或版式内容，未抽取到可入库文本。")
    return "\n\n".join(header + body).strip() + "\n"


def write_pages(pages, output_dir, course, material_type, source_file, source_prefix, chapter, stem, show_progress=True):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    page_items = list(pages.items())
    for page_idx, items in progress_iter(page_items, total=len(page_items), desc="generate markdown", unit="page", enabled=show_progress):
        page_number = page_idx + 1
        md = build_page_markdown(
            items,
            page_number=page_number,
            course=course,
            material_type=material_type,
            source_file=source_file,
            source_prefix=source_prefix,
            chapter=chapter,
        )
        out_path = output_dir / f"{stem}_p{page_number:03d}.md"
        out_path.write_text(md, encoding="utf-8")
        written.append((page_number, out_path, md))
    return written


def post_json(url, payload, timeout=120):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        try:
            return response.status, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return response.status, raw


def get_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def upload_pages(written_pages, base_url, source_prefix, pause_seconds, show_progress=True):
    endpoint = base_url.rstrip("/") + "/documents/text"
    results = []
    page_iter = progress_iter(written_pages, total=len(written_pages), desc="upload pages", unit="page", enabled=show_progress)
    for page_number, out_path, text in page_iter:
        file_source = f"{source_prefix} 第 {page_number} 页"
        payload = {"text": text, "file_source": file_source}
        try:
            status, data = post_json(endpoint, payload)
            results.append({"page": page_number, "status": status, "source": file_source, "response": data})
            print(f"uploaded page={page_number:03d} status={status} source={file_source}", flush=True)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            results.append({"page": page_number, "status": exc.code, "source": file_source, "error": body})
            print(f"upload failed page={page_number:03d} status={exc.code} source={file_source} body={body}", flush=True)
        except Exception as exc:
            results.append({"page": page_number, "source": file_source, "error": str(exc)})
            print(f"upload failed page={page_number:03d} source={file_source} error={exc}", flush=True)
        if pause_seconds > 0:
            time.sleep(pause_seconds)
    return results


def wait_until_idle(base_url, timeout_seconds, interval_seconds):
    deadline = time.time() + timeout_seconds
    status_url = base_url.rstrip("/") + "/documents/status_counts"
    last = None
    while time.time() < deadline:
        last = get_json(status_url)
        counts = last.get("status_counts", last)
        pending = int(counts.get("pending", 0) or 0)
        processing = int(counts.get("processing", 0) or 0)
        preprocessed = int(counts.get("preprocessed", 0) or 0)
        failed = int(counts.get("failed", 0) or 0)
        print(
            f"status pending={pending} preprocessed={preprocessed} processing={processing} "
            f"processed={counts.get('processed', counts.get('completed', 0))} failed={failed} all={counts.get('all')}",
            flush=True,
        )
        if pending == 0 and processing == 0 and preprocessed == 0:
            return last
        time.sleep(interval_seconds)
    return last


def main():
    parser = argparse.ArgumentParser(description="Convert MinerU content_list.json into page-level Markdown and upload to LightRAG.")
    parser.add_argument("--content-list", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--course", default="计算机系统基础")
    parser.add_argument("--material-type", default="课件")
    parser.add_argument("--source-file", required=True)
    parser.add_argument("--source-prefix", required=True)
    parser.add_argument("--chapter", required=True)
    parser.add_argument("--stem", default="page")
    parser.add_argument("--base-url", default="http://localhost:9621")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--wait-timeout", type=int, default=1800)
    parser.add_argument("--wait-interval", type=int, default=15)
    parser.add_argument("--pause-seconds", type=float, default=0)
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--result-json")
    args = parser.parse_args()

    pages = load_pages(args.content_list)
    if not pages:
        print("No pages found in content_list.", file=sys.stderr)
        return 2

    written = write_pages(
        pages,
        args.output_dir,
        course=args.course,
        material_type=args.material_type,
        source_file=args.source_file,
        source_prefix=args.source_prefix,
        chapter=args.chapter,
        stem=args.stem,
        show_progress=args.progress,
    )
    print(f"generated_pages={len(written)} output_dir={Path(args.output_dir).resolve()}", flush=True)

    result = {"generated_pages": len(written), "output_dir": str(Path(args.output_dir).resolve())}
    if args.upload:
        result["uploads"] = upload_pages(written, args.base_url, args.source_prefix, args.pause_seconds, show_progress=args.progress)
    if args.wait:
        result["final_status"] = wait_until_idle(args.base_url, args.wait_timeout, args.wait_interval)

    if args.result_json:
        Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
