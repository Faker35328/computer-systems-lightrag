import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True

import mineru_pages_to_lightrag as page_ingest


DEFAULT_MINERU_EXE = Path(r"D:\Anaconda_envs\envs\mineru\Scripts\mineru.exe")
DEFAULT_MINERU_API_EXE = DEFAULT_MINERU_EXE.with_name("mineru-api.exe")


def safe_name(value):
    value = value.strip()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r'[<>:"/\\|?*&]+', "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("._ ") or "document"


def display_pdf_name(pdf_name):
    return re.sub(r"^(\d+[.．])\s+", r"\1", pdf_name)


def chapter_from_stem(stem):
    chapter = re.sub(r"^\d+[.．]\s*", "", stem).strip()
    return chapter or stem.strip()


def discover_pdfs(pdf_root, only=None, limit=None):
    pdfs = sorted(Path(pdf_root).rglob("*.pdf"), key=lambda p: str(p))
    if only:
        pdfs = [p for p in pdfs if only in str(p)]
    if limit is not None:
        pdfs = pdfs[:limit]
    return pdfs


def find_content_list(mineru_output_root, course_dir, pdf_stem):
    candidates = [
        Path(mineru_output_root) / course_dir / pdf_stem / "auto" / f"{pdf_stem}_content_list.json",
        Path(mineru_output_root) / pdf_stem / "auto" / f"{pdf_stem}_content_list.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def mineru_target_root(mineru_output_root, course_dir, legacy_output):
    root = Path(mineru_output_root)
    return root if legacy_output else root / course_dir


def make_mineru_env(max_concurrent_requests=1, processing_window_size=8):
    env = os.environ.copy()
    env["MINERU_API_MAX_CONCURRENT_REQUESTS"] = str(max(1, int(max_concurrent_requests)))
    env["MINERU_PROCESSING_WINDOW_SIZE"] = str(max(1, int(processing_window_size)))
    no_proxy_values = ["127.0.0.1", "localhost", "::1"]
    for key in ("NO_PROXY", "no_proxy"):
        current = env.get(key, "")
        parts = [item.strip() for item in current.split(",") if item.strip()]
        for value in no_proxy_values:
            if value not in parts:
                parts.append(value)
        env[key] = ",".join(parts)
    return env


def get_free_port(host):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def wait_for_mineru_api(api_url, timeout_seconds):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.time() + timeout_seconds
    health_url = api_url.rstrip("/") + "/health"
    last_error = None
    while time.time() < deadline:
        try:
            with opener.open(health_url, timeout=5) as response:
                if 200 <= response.status < 300:
                    return True
        except Exception as exc:
            last_error = exc
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for MinerU API at {health_url}: {last_error}")


def start_mineru_api(args):
    host = args.mineru_api_host
    port = args.mineru_api_port or get_free_port(host)
    api_url = f"http://{host}:{port}"
    cmd = [str(args.mineru_api_exe), "--host", host, "--port", str(port)]
    env = make_mineru_env(args.mineru_api_concurrency, args.mineru_processing_window_size)
    print(
        "mineru-api:",
        " ".join(cmd),
        f"(concurrency={args.mineru_api_concurrency}, window={args.mineru_processing_window_size})",
        flush=True,
    )
    process = subprocess.Popen(cmd, env=env)
    wait_for_mineru_api(api_url, args.mineru_api_startup_timeout)
    print(f"mineru-api ready: {api_url}", flush=True)
    return process, api_url


def stop_mineru_api(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=20)


def run_mineru(
    mineru_exe,
    pdf_path,
    output_root,
    backend,
    method,
    lang,
    formula,
    table,
    api_url=None,
    max_concurrent_requests=1,
    processing_window_size=8,
):
    pdf_path = Path(pdf_path).resolve()
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(mineru_exe),
        "-p",
        str(pdf_path),
        "-o",
        str(output_root),
        "-b",
        backend,
        "-l",
        lang,
        "-m",
        method,
        "-f",
        "true" if formula else "false",
        "-t",
        "true" if table else "false",
    ]
    if api_url:
        cmd.extend(["--api-url", api_url])
    env = make_mineru_env(max_concurrent_requests, processing_window_size)
    print("mineru:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, env=env)


def run_mineru_path(args, input_path, output_root, api_url=None):
    input_path = Path(input_path).resolve()
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(args.mineru_exe),
        "-p",
        str(input_path),
        "-o",
        str(output_root),
        "-b",
        args.backend,
        "-l",
        args.lang,
        "-m",
        args.method,
        "-f",
        "true" if args.formula else "false",
        "-t",
        "true" if args.table else "false",
    ]
    if api_url:
        cmd.extend(["--api-url", api_url])
    env = make_mineru_env(args.mineru_api_concurrency, args.mineru_processing_window_size)
    print("mineru:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, env=env)


def refresh_plan_content_list(plan, mineru_output_root):
    plan["content_list"] = find_content_list(
        mineru_output_root,
        plan["course_dir"],
        plan["pdf_path"].stem,
    )
    return plan["content_list"]


def run_directory_batch_mineru(args, plans):
    missing_by_course = {}
    for plan in plans:
        if plan["content_list"] is None:
            missing_by_course.setdefault(plan["course_dir"], []).append(plan)

    for course_dir, course_plans in missing_by_course.items():
        course_pdf_dir = Path(args.pdf_root) / course_dir
        output_root = mineru_target_root(args.mineru_output, course_dir, args.legacy_mineru_output)
        print(
            f"\n=== MinerU directory batch: {course_dir} | missing_pdfs={len(course_plans)} ===",
            flush=True,
        )
        run_mineru_path(args, course_pdf_dir, output_root)

    for plan in plans:
        refresh_plan_content_list(plan, args.mineru_output)


def post_json(url, payload, timeout=60):
    return page_ingest.post_json(url, payload, timeout=timeout)


def collect_existing_sources(base_url):
    existing = set()
    page = 1
    total_pages = 1
    endpoint = base_url.rstrip("/") + "/documents/paginated"
    while page <= total_pages:
        payload = {
            "page": page,
            "page_size": 200,
            "sort_field": "file_path",
            "sort_direction": "asc",
        }
        _, response = post_json(endpoint, payload, timeout=60)
        for doc in response.get("documents", []):
            file_path = doc.get("file_path")
            if file_path:
                existing.add(file_path)
        pagination = response.get("pagination", {})
        total_pages = int(pagination.get("total_pages") or 1)
        page += 1
    return existing


def build_document_plan(pdf_root, mineru_output_root, processed_output_root, pdf_path, legacy_mineru_output):
    pdf_root = Path(pdf_root)
    pdf_path = Path(pdf_path)
    try:
        rel_path = pdf_path.relative_to(pdf_root)
    except ValueError:
        rel_path = pdf_path.name

    course_dir = pdf_path.parent.name
    source_prefix = f"{course_dir}：{display_pdf_name(pdf_path.name)}"
    chapter = chapter_from_stem(pdf_path.stem)
    source_file = rel_path.as_posix() if hasattr(rel_path, "as_posix") else str(rel_path)
    content_list = find_content_list(mineru_output_root, course_dir, pdf_path.stem)
    mineru_output_arg = mineru_target_root(mineru_output_root, course_dir, legacy_mineru_output)
    output_dir = Path(processed_output_root) / safe_name(course_dir) / safe_name(pdf_path.stem)
    stem = f"{safe_name(course_dir)}_{safe_name(pdf_path.stem)}"
    return {
        "pdf_path": pdf_path,
        "course_dir": course_dir,
        "source_prefix": source_prefix,
        "chapter": chapter,
        "source_file": source_file,
        "content_list": content_list,
        "mineru_output_arg": mineru_output_arg,
        "output_dir": output_dir,
        "stem": stem,
    }


def process_one(plan, args, existing_sources):
    pdf_path = plan["pdf_path"]
    print(f"\n=== {pdf_path} ===", flush=True)

    content_list = plan["content_list"]
    if content_list is None:
        if args.skip_mineru:
            print(f"skip: MinerU content_list not found for {pdf_path}", flush=True)
            return {
                "pdf": str(pdf_path),
                "status": "skipped_missing_mineru",
                "message": "Run with --no-skip-mineru --only <PDF name> to parse this PDF with MinerU.",
            }
        if args.dry_run:
            print(f"would run MinerU -> {plan['mineru_output_arg']}", flush=True)
            return {"pdf": str(pdf_path), "status": "dry_run_missing_mineru"}
        run_mineru(
            args.mineru_exe,
            pdf_path,
            plan["mineru_output_arg"],
            args.backend,
            args.method,
            args.lang,
            args.formula,
            args.table,
            api_url=getattr(args, "active_mineru_api_url", None),
            max_concurrent_requests=args.mineru_api_concurrency,
            processing_window_size=args.mineru_processing_window_size,
        )
        content_list = find_content_list(args.mineru_output, plan["course_dir"], pdf_path.stem)
        if content_list is None:
            raise FileNotFoundError(f"MinerU finished but content_list still not found for {pdf_path}")
    else:
        print(f"reuse MinerU: {content_list}", flush=True)

    if args.dry_run:
        pages = page_ingest.load_pages(content_list)
        print(f"would generate {len(pages)} page Markdown files -> {plan['output_dir']}", flush=True)
        if args.upload:
            print("would upload page-level documents to LightRAG", flush=True)
        return {"pdf": str(pdf_path), "status": "dry_run", "pages": len(pages)}

    pages = page_ingest.load_pages(content_list)
    written_pages = page_ingest.write_pages(
        pages,
        plan["output_dir"],
        course=args.course,
        material_type=args.material_type,
        source_file=plan["source_file"],
        source_prefix=plan["source_prefix"],
        chapter=plan["chapter"],
        stem=plan["stem"],
        show_progress=args.progress,
    )
    print(f"generated pages={len(written_pages)} dir={plan['output_dir']}", flush=True)

    result = {
        "pdf": str(pdf_path),
        "content_list": str(content_list),
        "output_dir": str(plan["output_dir"]),
        "generated_pages": len(written_pages),
        "source_prefix": plan["source_prefix"],
    }

    if not args.upload:
        result["status"] = "generated_only"
        return result

    upload_pages = written_pages
    if args.skip_existing:
        filtered = []
        skipped = []
        for page_number, out_path, text in written_pages:
            source = f"{plan['source_prefix']} 第 {page_number} 页"
            if source in existing_sources:
                skipped.append(source)
            else:
                filtered.append((page_number, out_path, text))
        upload_pages = filtered
        print(f"skip_existing={len(skipped)} upload_remaining={len(upload_pages)}", flush=True)
        result["skipped_existing"] = len(skipped)

    uploads = page_ingest.upload_pages(
        upload_pages,
        args.base_url,
        plan["source_prefix"],
        args.pause_seconds,
        show_progress=args.progress,
    )
    for upload in uploads:
        if upload.get("status") == 200:
            existing_sources.add(upload["source"])
    result["uploads"] = uploads
    result["uploaded_pages"] = len([x for x in uploads if x.get("status") == 200])

    if args.wait_after_each and upload_pages:
        result["final_status"] = page_ingest.wait_until_idle(args.base_url, args.wait_timeout, args.wait_interval)

    return result


def parse_args():
    parser = argparse.ArgumentParser(description="Batch MinerU PDF parsing and page-level LightRAG ingestion.")
    parser.add_argument("--pdf-root", default="知识库")
    parser.add_argument("--mineru-output", default="mineru_output")
    parser.add_argument("--processed-output", default="processed_markdown")
    parser.add_argument("--mineru-exe", type=Path, default=DEFAULT_MINERU_EXE if DEFAULT_MINERU_EXE.exists() else Path("mineru"))
    parser.add_argument(
        "--mineru-server",
        choices=["per-pdf", "directory-batch", "persistent"],
        default="per-pdf",
        help=(
            "per-pdf is the safest mode for low-memory Windows machines; directory-batch parses each course directory once; persistent starts one mineru-api "
            "and reuses it for all PDFs; per-pdf lets mineru CLI start a temporary API for each PDF."
        ),
    )
    parser.add_argument("--mineru-api-exe", type=Path, default=DEFAULT_MINERU_API_EXE if DEFAULT_MINERU_API_EXE.exists() else Path("mineru-api"))
    parser.add_argument("--mineru-api-url", help="Use an already running MinerU API instead of starting one.")
    parser.add_argument("--mineru-api-host", default="127.0.0.1")
    parser.add_argument("--mineru-api-port", type=int, default=0)
    parser.add_argument("--mineru-api-startup-timeout", type=int, default=300)
    parser.add_argument("--mineru-api-concurrency", type=int, default=1, help="MinerU API concurrent parse request limit. Keep 1 on 16GB machines.")
    parser.add_argument("--mineru-processing-window-size", type=int, default=8, help="MinerU pipeline processing window size. Smaller values reduce memory pressure.")
    parser.add_argument("--base-url", default="http://localhost:9621")
    parser.add_argument("--course", default="计算机系统基础")
    parser.add_argument("--material-type", default="课件")
    parser.add_argument("--backend", default="pipeline")
    parser.add_argument(
        "--method",
        default="auto",
        choices=["auto", "txt", "ocr"],
        help=(
            "MinerU parse method. Default auto keeps high-quality parsing. "
            "Use txt only when explicitly choosing a lighter text-layer parse."
        ),
    )
    parser.add_argument("--lang", default="ch")
    parser.add_argument("--formula", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--table", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--only", help="Only process PDFs whose full path contains this text.")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--skip-mineru",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Only consume existing MinerU content_list.json files. "
            "Default false: missing PDFs are parsed by MinerU."
        ),
    )
    parser.add_argument("--legacy-mineru-output", action="store_true", help="Write new MinerU outputs directly under mineru_output instead of mineru_output/<course_dir>.")
    parser.add_argument("--upload", action="store_true", help="Upload generated page documents to LightRAG.")
    parser.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--wait-after-each", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--wait-timeout", type=int, default=3600)
    parser.add_argument("--wait-interval", type=int, default=20)
    parser.add_argument("--pause-seconds", type=float, default=0.2)
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--result-json", default="processed_markdown/batch_ingest_result.json")
    return parser.parse_args()


def main():
    args = parse_args()
    pdfs = discover_pdfs(args.pdf_root, only=args.only, limit=args.limit)
    if not pdfs:
        print("No PDF files found.", file=sys.stderr)
        return 2

    plans = [
        build_document_plan(
            args.pdf_root,
            args.mineru_output,
            args.processed_output,
            pdf,
            args.legacy_mineru_output,
        )
        for pdf in pdfs
    ]

    missing_mineru_count = len([plan for plan in plans if plan["content_list"] is None])
    print(
        f"pdf_count={len(plans)} upload={args.upload} dry_run={args.dry_run} "
        f"mineru_server={args.mineru_server} "
        f"mineru_concurrency={args.mineru_api_concurrency} "
        f"mineru_window={args.mineru_processing_window_size} method={args.method} "
        f"formula={args.formula} table={args.table} missing_mineru={missing_mineru_count}",
        flush=True,
    )
    for idx, plan in enumerate(plans, 1):
        status = "has_mineru" if plan["content_list"] else "needs_mineru"
        print(f"{idx:02d}. {status} | {plan['source_prefix']} | {plan['pdf_path']}", flush=True)

    existing_sources = set()
    if args.upload and args.skip_existing and not args.dry_run:
        existing_sources = collect_existing_sources(args.base_url)
        print(f"existing LightRAG sources={len(existing_sources)}", flush=True)

    results = []
    mineru_api_process = None
    args.active_mineru_api_url = None
    try:
        if (
            not args.dry_run
            and not args.skip_mineru
            and missing_mineru_count > 0
            and args.mineru_server == "directory-batch"
        ):
            run_directory_batch_mineru(args, plans)
            missing_mineru_count = len([plan for plan in plans if plan["content_list"] is None])
            print(f"after directory batch missing_mineru={missing_mineru_count}", flush=True)

        should_use_persistent_mineru = (
            not args.dry_run
            and not args.skip_mineru
            and missing_mineru_count > 0
            and args.mineru_server == "persistent"
        )
        if should_use_persistent_mineru:
            if args.mineru_api_url:
                args.active_mineru_api_url = args.mineru_api_url.rstrip("/")
                wait_for_mineru_api(args.active_mineru_api_url, args.mineru_api_startup_timeout)
                print(f"reuse mineru-api: {args.active_mineru_api_url}", flush=True)
            else:
                mineru_api_process, args.active_mineru_api_url = start_mineru_api(args)

        plan_iter = page_ingest.progress_iter(plans, total=len(plans), desc="process PDFs", unit="pdf", enabled=args.progress and not args.dry_run)
        for plan in plan_iter:
            try:
                results.append(process_one(plan, args, existing_sources))
            except Exception as exc:
                print(f"ERROR {plan['pdf_path']}: {exc}", flush=True)
                results.append({"pdf": str(plan["pdf_path"]), "status": "error", "error": str(exc)})
                if not args.upload:
                    continue
    finally:
        stop_mineru_api(mineru_api_process)

    result = {"pdf_count": len(plans), "results": results}
    if not args.dry_run:
        result_path = Path(args.result_json)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"result_json={result_path.resolve()}", flush=True)
    return 0 if all(item.get("status") != "error" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
