import argparse
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - progress is optional
    tqdm = None


REQUIRED_KEYS = ("text", "file_source")


def progress(iterable, *, total=None, desc="", unit="", enabled=True):
    if enabled and tqdm is not None:
        return tqdm(iterable, total=total, desc=desc, unit=unit, dynamic_ncols=True)
    return iterable


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def post_json(url, payload, timeout=120):
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


def get_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def status_counts(base_url):
    data = get_json(base_url.rstrip("/") + "/documents/status_counts", timeout=30)
    return data.get("status_counts", data)


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


def parse_source_from_markdown(text):
    match = re.search(r"^来源：(.+)$", text, flags=re.MULTILINE)
    if not match:
        raise ValueError("missing 来源 line in markdown")
    return match.group(1).strip()


def load_entries(manifest_path, project_root):
    manifest = load_json(manifest_path)
    entries = []
    for doc in manifest.get("documents", []):
        for output_file in doc.get("output_files", []):
            path = project_root / output_file
            if not path.exists():
                raise FileNotFoundError(path)
            text = path.read_text(encoding="utf-8")
            file_source = parse_source_from_markdown(text)
            entries.append(
                {
                    "path": output_file,
                    "file_source": file_source,
                    "text": text,
                    "course_dir": doc.get("course_dir"),
                    "pdf_name": doc.get("pdf_name"),
                }
            )
    return entries


def choose_pilot_entries(entries):
    wanted_fragments = [
        "02. 环境与工具.pdf 第 1 页",
        "13. 存储器层次结构.pdf 第 19 页",
        "13. 存储器层次结构.pdf 第 40 页",
        "13. 存储器层次结构.pdf 第 41 页",
        "02. 异常.pdf 第 1 页",
        "05. 虚拟内存.pdf 第 22 页",
        "14. 同步：进阶.pdf 第 27 页",
        "12. 处理器体系结构.pdf 第 22 页",
        "06. 地址翻译.pdf 第 5 页",
        "10. 网络.pdf 第 23 页",
    ]
    selected = []
    used = set()
    for fragment in wanted_fragments:
        for entry in entries:
            if fragment in entry["file_source"] and entry["file_source"] not in used:
                selected.append(entry)
                used.add(entry["file_source"])
                break
    for entry in entries:
        if len(selected) >= 10:
            break
        if entry["file_source"] not in used:
            selected.append(entry)
            used.add(entry["file_source"])
    return selected


def upload_entries(entries, base_url, pause_seconds, show_progress=True):
    endpoint = base_url.rstrip("/") + "/documents/text"
    results = []
    iterator = progress(entries, total=len(entries), desc="upload", unit="page", enabled=show_progress)
    for entry in iterator:
        payload = {"text": entry["text"], "file_source": entry["file_source"]}
        try:
            status, data = post_json(endpoint, payload)
            result = {
                "path": entry["path"],
                "source": entry["file_source"],
                "status": status,
                "response": data,
            }
            print(f"uploaded status={status} source={entry['file_source']}", flush=True)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            result = {
                "path": entry["path"],
                "source": entry["file_source"],
                "status": exc.code,
                "error": body,
            }
            print(f"upload failed status={exc.code} source={entry['file_source']} body={body}", flush=True)
        except Exception as exc:
            result = {
                "path": entry["path"],
                "source": entry["file_source"],
                "error": str(exc),
            }
            print(f"upload failed source={entry['file_source']} error={exc}", flush=True)
        results.append(result)
        if pause_seconds > 0:
            time.sleep(pause_seconds)
    return results


def successful_sources(results):
    return {item["source"] for item in results if item.get("status") in (200, 201, 202)}


def load_existing_sources(status_json_path):
    path = Path(status_json_path)
    if not path.exists():
        raise FileNotFoundError(f"existing status json not found: {path}")
    data = load_json(path)
    sources = set()
    if isinstance(data, dict):
        items = data.values()
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError(f"unsupported status json shape: {type(data).__name__}")
    for item in items:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        file_path = item.get("file_path")
        if file_path and status != "failed":
            sources.add(file_path)
    return sources


def has_upload_errors(results):
    return any(item.get("status") not in (200, 201, 202) for item in results)


def run_pilot(args, entries, project_root):
    pilot = choose_pilot_entries(entries)
    print(f"pilot_count={len(pilot)}", flush=True)
    results = upload_entries(pilot, args.base_url, args.pause_seconds, show_progress=args.progress)
    final_status = wait_until_idle(args.base_url, args.wait_timeout, args.wait_interval)
    output = {
        "mode": "pilot",
        "uploaded_count": len(results),
        "results": results,
        "final_status": final_status,
    }
    write_json(project_root / args.result_json, output)
    failed = int(final_status.get("failed", 0) or 0)
    if has_upload_errors(results) or failed > 0:
        raise SystemExit("pilot failed; stop before full upload")
    return successful_sources(results)


def run_full(args, entries, project_root, skip_sources):
    remaining = [entry for entry in entries if entry["file_source"] not in skip_sources]
    print(f"full_total={len(entries)} skip_sources={len(skip_sources)} remaining={len(remaining)}", flush=True)
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
        "skipped_sources": sorted(skip_sources),
        "results": all_results,
        "batch_statuses": batch_statuses,
        "final_status": final_status,
    }
    write_json(project_root / args.result_json, output)
    return output


def main():
    parser = argparse.ArgumentParser(description="Upload processed courseware Markdown pages to LightRAG.")
    parser.add_argument("--manifest", default="processed_markdown_v2/courseware_manifest.json")
    parser.add_argument("--base-url", default="http://localhost:9621")
    parser.add_argument("--mode", choices=("pilot", "full", "pilot-full"), default="pilot")
    parser.add_argument("--batch-size", type=int, default=30)
    parser.add_argument("--pause-seconds", type=float, default=0.1)
    parser.add_argument("--wait-timeout", type=int, default=7200)
    parser.add_argument("--wait-interval", type=int, default=20)
    parser.add_argument("--result-json", default="processed_markdown_v2/upload_result.json")
    parser.add_argument("--existing-status-json", help="Skip file_source values already present in a LightRAG kv_store_doc_status.json file.")
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    project_root = Path.cwd()
    entries = load_entries(project_root / args.manifest, project_root)
    print(f"entries={len(entries)}", flush=True)
    if len(entries) == 0:
        raise SystemExit("no entries to upload")

    skip_sources = set()
    if args.existing_status_json:
        skip_sources.update(load_existing_sources(project_root / args.existing_status_json))
        print(f"existing_status_sources={len(skip_sources)}", flush=True)
    if args.mode in ("pilot", "pilot-full"):
        skip_sources.update(run_pilot(args, entries, project_root))
    if args.mode in ("full", "pilot-full"):
        run_full(args, entries, project_root, skip_sources)


if __name__ == "__main__":
    main()
