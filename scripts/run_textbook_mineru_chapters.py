import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF_NAME = "Computer.Systems.A.Programmes.Perpective.3e(1).pdf"
DEFAULT_MINERU_EXE = Path(r"D:\Anaconda_envs\envs\mineru\Scripts\mineru.exe")
DEFAULT_MINERU_API_EXE = Path(r"D:\Anaconda_envs\envs\mineru\Scripts\mineru-api.exe")
RESULT_PATH = ROOT / "mineru_output" / "textbook_mineru_chapters_result.json"

CHAPTERS = [
    {"chapter": 3, "start": 199, "end": 386},
    {"chapter": 4, "start": 387, "end": 530},
    {"chapter": 5, "start": 531, "end": 614},
    {"chapter": 6, "start": 615, "end": 702},
    {"chapter": 7, "start": 705, "end": 756},
    {"chapter": 8, "start": 757, "end": 836},
    {"chapter": 9, "start": 837, "end": 922},
    {"chapter": 10, "start": 925, "end": 952},
    {"chapter": 11, "start": 953, "end": 1006},
    {"chapter": 12, "start": 1007, "end": 1076},
]


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_health(url, timeout_seconds):
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            with urlopen(f"{url}/health", timeout=5) as response:
                if response.status == 200:
                    return True
        except URLError as exc:
            last_error = exc
        except Exception as exc:  # noqa: BLE001 - keep health wait robust
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"MinerU API health check timed out: {url}/health ({last_error})")


def make_env():
    env = os.environ.copy()
    hf_home = ROOT / ".hf-cache"
    tmp_dir = ROOT / ".tmp-mineru"
    hf_home.mkdir(parents=True, exist_ok=True)
    (hf_home / "hub").mkdir(parents=True, exist_ok=True)
    (hf_home / "transformers").mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    env.update(
        {
            "HF_HOME": str(hf_home),
            "HUGGINGFACE_HUB_CACHE": str(hf_home / "hub"),
            "TRANSFORMERS_CACHE": str(hf_home / "transformers"),
            "TEMP": str(tmp_dir),
            "TMP": str(tmp_dir),
            "MINERU_API_MAX_CONCURRENT_REQUESTS": "1",
            "MINERU_PROCESSING_WINDOW_SIZE": "8",
        }
    )
    return env


def discover_pdf(path_arg):
    if path_arg:
        path = Path(path_arg)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            raise FileNotFoundError(path)
        return path

    matches = [
        path
        for path in (ROOT / "知识库").rglob(DEFAULT_PDF_NAME)
        if "mineru_output" not in path.parts
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one textbook PDF, found {len(matches)}: {matches}")
    return matches[0]


def expected_content_list(chapter):
    return (
        ROOT
        / "mineru_output"
        / f"教材_chapter{chapter}"
        / "Computer.Systems.A.Programmes.Perpective.3e(1)"
        / "auto"
        / "Computer.Systems.A.Programmes.Perpective.3e(1)_content_list.json"
    )


def validate_content_list(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"content_list is not a list: {path}")
    if not any(isinstance(item, dict) and "page_idx" in item for item in data):
        raise ValueError(f"content_list has no page_idx entries: {path}")
    types = sorted(
        {
            str(item.get("type"))
            for item in data
            if isinstance(item, dict) and item.get("type") is not None
        }
    )
    return {"items": len(data), "types": types}


def start_api(api_exe, env, port, startup_timeout):
    if not api_exe.exists():
        raise FileNotFoundError(api_exe)

    cmd = [str(api_exe), "--host", "127.0.0.1", "--port", str(port)]
    print(f"mineru-api: {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env)
    url = f"http://127.0.0.1:{port}"
    wait_for_health(url, startup_timeout)
    print(f"mineru-api healthy: {url}", flush=True)
    return proc, url


def terminate_api(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=15)


def run_chapter(mineru_exe, env, api_url, pdf_path, chapter_info):
    chapter = chapter_info["chapter"]
    out_dir = ROOT / "mineru_output" / f"教材_chapter{chapter}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(mineru_exe),
        "-p",
        str(pdf_path),
        "-o",
        str(out_dir),
        "-b",
        "pipeline",
        "-l",
        "en",
        "-m",
        "auto",
        "-f",
        "true",
        "-t",
        "true",
        "--start",
        str(chapter_info["start"]),
        "--end",
        str(chapter_info["end"]),
        "--api-url",
        api_url,
    ]
    print(
        f"\n=== Chapter {chapter}: pages {chapter_info['start']}-{chapter_info['end']} ===",
        flush=True,
    )
    print(f"mineru: {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=str(ROOT), env=env, check=True)

    content_list = expected_content_list(chapter)
    if not content_list.exists():
        raise FileNotFoundError(content_list)
    stats = validate_content_list(content_list)
    print(
        f"ok Chapter {chapter}: {content_list} | items={stats['items']} | types={','.join(stats['types'])}",
        flush=True,
    )
    return {
        "chapter": chapter,
        "status": "completed",
        "content_list": str(content_list),
        **stats,
    }


def write_result(data):
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run MinerU for CSAPP textbook chapters 3-12 only."
    )
    parser.add_argument("--pdf", help="Path to the full CSAPP textbook PDF.")
    parser.add_argument("--mineru-exe", default=str(DEFAULT_MINERU_EXE))
    parser.add_argument("--mineru-api-exe", default=str(DEFAULT_MINERU_API_EXE))
    parser.add_argument("--api-url", help="Reuse an existing MinerU API URL.")
    parser.add_argument("--port", type=int, help="Port for the started MinerU API.")
    parser.add_argument("--startup-timeout", type=int, default=180)
    parser.add_argument("--start-chapter", type=int, default=3)
    parser.add_argument("--end-chapter", type=int, default=12)
    return parser.parse_args()


def main():
    args = parse_args()
    env = make_env()
    pdf_path = discover_pdf(args.pdf)
    mineru_exe = Path(args.mineru_exe)
    api_exe = Path(args.mineru_api_exe)
    if not mineru_exe.exists():
        raise FileNotFoundError(mineru_exe)

    selected = [
        item
        for item in CHAPTERS
        if args.start_chapter <= item["chapter"] <= args.end_chapter
    ]
    if not selected:
        raise RuntimeError("No chapters selected.")

    print(f"pdf={pdf_path}", flush=True)
    print(f"chapters={','.join(str(item['chapter']) for item in selected)}", flush=True)
    print(f"HF_HOME={env['HF_HOME']}", flush=True)
    print(f"TEMP={env['TEMP']}", flush=True)

    results = []
    api_proc = None
    api_url = args.api_url
    try:
        if api_url:
            wait_for_health(api_url.rstrip("/"), args.startup_timeout)
            api_url = api_url.rstrip("/")
            print(f"reuse mineru-api: {api_url}", flush=True)
        else:
            port = args.port or find_free_port()
            api_proc, api_url = start_api(api_exe, env, port, args.startup_timeout)

        for chapter_info in selected:
            chapter = chapter_info["chapter"]
            existing = expected_content_list(chapter)
            if existing.exists():
                stats = validate_content_list(existing)
                print(
                    f"skip Chapter {chapter}: existing {existing} | items={stats['items']}",
                    flush=True,
                )
                results.append(
                    {
                        "chapter": chapter,
                        "status": "skipped_existing",
                        "content_list": str(existing),
                        **stats,
                    }
                )
                continue
            result = run_chapter(mineru_exe, env, api_url, pdf_path, chapter_info)
            results.append(result)
            write_result({"status": "running", "results": results})

        summary = {"status": "completed", "results": results}
        write_result(summary)
        print(f"\nresult_json={RESULT_PATH}", flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 - write resumable failure report
        results.append({"status": "failed", "error": str(exc)})
        write_result({"status": "failed", "results": results})
        print(f"\nERROR: {exc}", file=sys.stderr, flush=True)
        print(f"result_json={RESULT_PATH}", flush=True)
        return 1
    finally:
        terminate_api(api_proc)


if __name__ == "__main__":
    raise SystemExit(main())
