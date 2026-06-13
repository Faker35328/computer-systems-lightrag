import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - optional progress bar
    tqdm = None


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


SOURCE_RE = re.compile(r"^来源：(.+)$", re.MULTILINE)
REQUIRED_KEYS = ("text", "file_source")


def progress(iterable, *, total=None, desc="", unit="", enabled=True):
    if enabled and tqdm is not None:
        return tqdm(iterable, total=total, desc=desc, unit=unit, dynamic_ncols=True)
    return iterable


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def get_json(url, timeout=30, retries=5, retry_delay=5):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except (TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(retry_delay)
    raise last_error


def post_json(url, payload, timeout=180):
    for key in REQUIRED_KEYS:
        if key not in payload:
            raise ValueError(f"missing payload key: {key}")
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


def post_raw_json(url, payload, timeout=60):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def parse_source_from_markdown(text):
    match = SOURCE_RE.search(text)
    if not match:
        raise ValueError("missing source line in markdown")
    return match.group(1).strip()


def body_length(text):
    body = text.split("---", 1)[1].strip() if "---" in text else text.strip()
    return len(body)


def load_entries(manifest_path, project_root, min_body_chars, start_chapter=None, end_chapter=None):
    manifest = load_json(manifest_path)
    entries = []
    skipped = []
    for doc in manifest.get("documents", []):
        chapter_number = doc.get("chapter_number")
        if start_chapter is not None and chapter_number < start_chapter:
            continue
        if end_chapter is not None and chapter_number > end_chapter:
            continue
        for section in doc.get("sections", []):
            path = project_root / section["output_file"]
            if not path.exists():
                raise FileNotFoundError(path)
            text = path.read_text(encoding="utf-8")
            source = parse_source_from_markdown(text)
            length = body_length(text)
            record = {
                "path": section["output_file"],
                "file_source": source,
                "text": text,
                "chapter_number": section.get("chapter_number"),
                "section_index": section.get("section_index"),
                "section_title": section.get("section_title"),
                "page_label": section.get("page_label"),
                "body_chars": length,
            }
            if length < min_body_chars:
                skipped.append(record)
            else:
                entries.append(record)
    return entries, skipped


def status_counts(base_url):
    data = get_json(base_url.rstrip("/") + "/documents/status_counts", timeout=30)
    return data.get("status_counts", data)


def list_existing_sources(base_url):
    sources = set()
    paginated_url = base_url.rstrip("/") + "/documents/paginated"
    page = 1
    while True:
        try:
            data = post_raw_json(
                paginated_url,
                {
                    "status_filter": None,
                    "page": page,
                    "page_size": 200,
                    "sort_field": "updated_at",
                    "sort_direction": "desc",
                },
            )
        except Exception:
            break
        for item in data.get("documents", []):
            if not isinstance(item, dict):
                continue
            status = item.get("status")
            source = item.get("file_path") or item.get("file_source") or item.get("source")
            if source and status != "failed":
                sources.add(source)
        pagination = data.get("pagination") or {}
        if not pagination.get("has_next"):
            return sources
        page += 1

    try:
        data = get_json(base_url.rstrip("/") + "/documents", timeout=60)
    except Exception:
        return sources

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        status = value.get("status")
        source = value.get("file_path") or value.get("file_source") or value.get("source")
        if source and status != "failed":
            sources.add(source)
        for item in value.values():
            visit(item)

    visit(data)
    return sources


def wait_until_idle(base_url, timeout_seconds, interval_seconds):
    deadline = time.time() + timeout_seconds
    last = None
    while time.time() < deadline:
        last = status_counts(base_url)
        pending = int(last.get("pending", 0) or 0)
        processing = int(last.get("processing", 0) or 0)
        preprocessed = int(last.get("preprocessed", 0) or 0)
        failed = int(last.get("failed", 0) or 0)
        print(
            f"status pending={pending} preprocessed={preprocessed} processing={processing} "
            f"processed={last.get('processed', 0)} failed={failed} all={last.get('all', 0)}",
            flush=True,
        )
        if pending == 0 and processing == 0 and preprocessed == 0:
            return last
        time.sleep(interval_seconds)
    raise TimeoutError(f"LightRAG did not become idle before timeout. Last status: {last}")


def choose_pilot_entries(entries, count):
    preferred = [
        "1.1 Information Is Bits + Context",
        "1.4.1 Hardware Organization of a System",
        "1.5 Caches Matter",
        "2.1.1 Hexadecimal Notation",
        "2.1.3 Addressing and Byte Ordering",
        "2.2.3 Two's-Complement Encodings",
        "2.3.1 Integral Data Types",
        "2.4.2 Floating-Point Encoding",
        "Practice Problem 2.5",
        "Aside The Unicode standard",
    ]
    selected = []
    used = set()
    for fragment in preferred:
        for entry in entries:
            if fragment in entry["file_source"] and entry["file_source"] not in used:
                selected.append(entry)
                used.add(entry["file_source"])
                break
    for entry in entries:
        if len(selected) >= count:
            break
        if entry["file_source"] not in used:
            selected.append(entry)
            used.add(entry["file_source"])
    return selected[:count]


def upload_entries(entries, base_url, pause_seconds, show_progress=True):
    endpoint = base_url.rstrip("/") + "/documents/text"
    results = []
    iterator = progress(entries, total=len(entries), desc="upload textbook", unit="section", enabled=show_progress)
    for entry in iterator:
        payload = {"text": entry["text"], "file_source": entry["file_source"]}
        try:
            status, data = post_json(endpoint, payload)
            result = {
                "path": entry["path"],
                "source": entry["file_source"],
                "status": status,
                "response": data,
                "body_chars": entry["body_chars"],
            }
            print(f"uploaded status={status} source={entry['file_source']}", flush=True)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            result = {
                "path": entry["path"],
                "source": entry["file_source"],
                "status": exc.code,
                "error": body,
                "body_chars": entry["body_chars"],
            }
            print(f"upload failed status={exc.code} source={entry['file_source']} body={body}", flush=True)
        except Exception as exc:
            result = {
                "path": entry["path"],
                "source": entry["file_source"],
                "error": str(exc),
                "body_chars": entry["body_chars"],
            }
            print(f"upload failed source={entry['file_source']} error={exc}", flush=True)
        results.append(result)
        if pause_seconds > 0:
            time.sleep(pause_seconds)
    return results


def has_upload_errors(results):
    return any(item.get("status") not in (200, 201, 202) for item in results)


def successful_sources(results):
    return {item["source"] for item in results if item.get("status") in (200, 201, 202)}


def run_pilot(args, entries, project_root, skipped):
    pilot = choose_pilot_entries(entries, args.pilot_count)
    print(f"pilot_count={len(pilot)}", flush=True)
    results = upload_entries(pilot, args.base_url, args.pause_seconds, show_progress=args.progress)
    final_status = wait_until_idle(args.base_url, args.wait_timeout, args.wait_interval)
    output = {
        "mode": "pilot",
        "uploaded_count": len(results),
        "skipped_short_count": len(skipped),
        "skipped_short": skipped,
        "results": results,
        "final_status": final_status,
    }
    write_json(project_root / args.result_json, output)
    failed = int(final_status.get("failed", 0) or 0)
    if has_upload_errors(results) or failed > 0:
        raise SystemExit("pilot failed; stop before full upload")
    return successful_sources(results)


def run_full(args, entries, project_root, skipped, skip_sources):
    existing_sources = list_existing_sources(args.base_url)
    skip_sources = set(skip_sources) | existing_sources
    remaining = [entry for entry in entries if entry["file_source"] not in skip_sources]
    if args.limit is not None:
        remaining = remaining[: args.limit]
    print(
        f"full_total={len(entries)} existing_sources={len(existing_sources)} "
        f"skip_sources={len(skip_sources)} remaining={len(remaining)}",
        flush=True,
    )
    all_results = []
    batch_statuses = []
    for start in range(0, len(remaining), args.batch_size):
        batch = remaining[start : start + args.batch_size]
        print(f"batch start={start + 1} size={len(batch)}", flush=True)
        batch_results = upload_entries(batch, args.base_url, args.pause_seconds, show_progress=args.progress)
        all_results.extend(batch_results)
        status = wait_until_idle(args.base_url, args.wait_timeout, args.wait_interval)
        batch_statuses.append({"start": start, "size": len(batch), "status": status})
        failed = int(status.get("failed", 0) or 0)
        if has_upload_errors(batch_results) or failed > 0:
            write_json(
                project_root / args.result_json,
                {
                    "mode": "full",
                    "uploaded_count": len(all_results),
                    "skipped_short_count": len(skipped),
                    "skipped_short": skipped,
                    "results": all_results,
                    "batch_statuses": batch_statuses,
                    "final_status": status,
                },
            )
            raise SystemExit("full upload stopped because a batch failed")
    final_status = status_counts(args.base_url)
    output = {
        "mode": "full",
        "uploaded_count": len(all_results),
        "skipped_short_count": len(skipped),
        "skipped_short": skipped,
        "skipped_sources": sorted(skip_sources),
        "results": all_results,
        "batch_statuses": batch_statuses,
        "final_status": final_status,
    }
    write_json(project_root / args.result_json, output)
    return output


def main():
    parser = argparse.ArgumentParser(description="Upload CSAPP textbook section Markdown to LightRAG.")
    parser.add_argument("--manifest", default="processed_textbook_sections/textbook_sections_manifest.json")
    parser.add_argument("--base-url", default="http://localhost:9621")
    parser.add_argument("--mode", choices=("pilot", "full", "pilot-full"), default="pilot")
    parser.add_argument("--pilot-count", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--pause-seconds", type=float, default=0.1)
    parser.add_argument("--wait-timeout", type=int, default=7200)
    parser.add_argument("--wait-interval", type=int, default=20)
    parser.add_argument("--min-body-chars", type=int, default=80)
    parser.add_argument("--start-chapter", type=int)
    parser.add_argument("--end-chapter", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--result-json", default="processed_textbook_sections/upload_textbook_result.json")
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if (
        args.start_chapter is not None
        and args.end_chapter is not None
        and args.start_chapter > args.end_chapter
    ):
        raise SystemExit("--start-chapter cannot be greater than --end-chapter")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")

    project_root = Path.cwd()
    entries, skipped = load_entries(
        project_root / args.manifest,
        project_root,
        args.min_body_chars,
        start_chapter=args.start_chapter,
        end_chapter=args.end_chapter,
    )
    print(f"entries={len(entries)} skipped_short={len(skipped)}", flush=True)
    if not entries:
        raise SystemExit("no entries to upload")

    skip_sources = set()
    if args.mode in ("pilot", "pilot-full"):
        skip_sources.update(run_pilot(args, entries, project_root, skipped))
    if args.mode in ("full", "pilot-full"):
        run_full(args, entries, project_root, skipped, skip_sources)


if __name__ == "__main__":
    main()
