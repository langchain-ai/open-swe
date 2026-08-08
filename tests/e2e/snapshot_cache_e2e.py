"""End-to-end check of the sandbox repo cache against real LangSmith sandboxes.

Not part of ``make test``: it creates real cloud resources and costs money. Run
it deliberately when the cache mechanics change.

It exists because the unit tests assert on generated shell strings and mocked
SDK calls, which cannot answer the questions that decide whether the feature
works at all:

  A1  Does ``capture_snapshot`` preserve the work dir? If the work dir is a
      mount rather than part of the captured rootfs, the cache vanishes at
      capture and every run silently falls back to a network clone.
  A2  Does a run resolve the same work dir the builder wrote the cache into?
  A3  Are the cache and the checkout destination on one filesystem, so taking a
      repo is a rename and not a full copy?
  A4  Does the sweep's ``git clone`` into the cache path work in the image?
  A5  Does booting from a capture and re-fetching work (the incremental path)?

Every resource it creates is prefixed ``openswe-e2e-`` and cleaned up. Sandboxes
are stopped, never deleted, matching the rule the agent code follows.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
from typing import Any

from langsmith.sandbox import SandboxClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from agent.utils.repo_clone import (  # noqa: E402
    PREFERRED_WORK_DIR,
    build_clone_script,
    build_mirror_sweep_script,
    cache_path_for,
    parse_clone_result,
    parse_mirror_sweep_output,
)

PROD_ENDPOINT = "https://api.smith.langchain.com/v2/sandboxes"
DEFAULT_REPO = "langchain-ai/open-swe"

# Short TTLs: these are throwaways, and the platform reclaims them.
IDLE_TTL_SECONDS = 600
DELETE_AFTER_STOP_SECONDS = 60

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    results.append((label, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{f' -- {detail}' if detail else ''}", flush=True)
    return ok


def run(sandbox: Any, command: str, timeout: int = 300) -> tuple[str, int]:
    result = sandbox.run(command, timeout=timeout)
    return (result.stdout or "").strip(), result.exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO, help="public owner/name to cache")
    parser.add_argument(
        "--key-file",
        default=str(pathlib.Path.home() / ".langsmith/api_keys/gcp_prod"),
        help="file holding the LangSmith API key; LANGSMITH_API_KEY wins (never printed)",
    )
    parser.add_argument("--endpoint", default=PROD_ENDPOINT)
    args = parser.parse_args()

    owner, name = args.repo.split("/", 1)
    stamp = time.strftime("%Y%m%d%H%M%S")
    run_prefix = f"openswe-e2e-{stamp}"
    capture_name = run_prefix

    api_key = (os.environ.get("LANGSMITH_API_KEY") or "").strip()
    if not api_key:
        key_path = pathlib.Path(args.key_file)
        if not key_path.exists():
            print(f"No LANGSMITH_API_KEY and no key file at {key_path}", file=sys.stderr)
            return 2
        api_key = key_path.read_text().strip()
    client = SandboxClient(api_key=api_key, api_endpoint=args.endpoint)

    started_sandboxes: list[str] = []
    timings: dict[str, float] = {}

    def phase(label: str) -> float:
        print(f"\n== {label}", flush=True)
        return time.monotonic()

    try:
        t = phase("Boot builder on the platform default base")
        builder = client.create_sandbox(
            name=f"openswe-e2e-builder-{stamp}",
            idle_ttl_seconds=IDLE_TTL_SECONDS,
            delete_after_stop_seconds=DELETE_AFTER_STOP_SECONDS,
        )
        started_sandboxes.append(builder.name)
        timings["builder_boot"] = time.monotonic() - t

        builder_wd = PREFERRED_WORK_DIR
        run(builder, f"mkdir -p {builder_wd}")
        raw_pwd, _ = run(builder, "pwd")
        versions, _ = run(builder, "git --version; gh --version | head -1")
        print(f"  work dir: {builder_wd} (raw pwd: {raw_pwd})\n  {versions}", flush=True)

        t = phase(f"Warm the cache with {args.repo}")
        sweep = build_mirror_sweep_script(builder_wd, [args.repo], proxy_auth=False)
        out, _code = run(builder, sweep, timeout=900)
        cloned, failed = parse_mirror_sweep_output(out)
        timings["warm"] = time.monotonic() - t
        clone_worked = check(
            "A4  git clone into the cache path works in the sandbox image",
            bool(cloned) and not failed,
            f"cloned={cloned} failed={failed}",
        )
        if not clone_worked:
            print(f"  sweep output:\n{out[-1500:]}", flush=True)
            return 1

        t = phase("Run the post-clone hook (dependency install slot)")
        hook = 'for repo in .repo-cache/*/*; do (cd "$repo" && git rev-parse --short HEAD); done'
        hook_out, hook_code = run(builder, f"set -e\ncd {builder_wd}\n{hook}")
        timings["post_hook"] = time.monotonic() - t
        check(
            "A6  post-clone hook runs with the work dir as cwd",
            hook_code == 0 and len(hook_out) >= 7,
            f"exit={hook_code} out={hook_out[:80]}",
        )

        cache_dir = cache_path_for(builder_wd, owner, name)
        sizes, _ = run(builder, f"du -sh {cache_dir} 2>/dev/null; df -h {builder_wd} | tail -1")
        print(f"  {sizes}", flush=True)

        same_fs, _ = run(
            builder,
            f"a=$(stat -c %d {cache_dir}); b=$(stat -c %d {builder_wd}); "
            'if [ "$a" = "$b" ]; then echo SAME; else echo "DIFFERENT $a $b"; fi',
        )
        check(
            "A3  cache and work dir on one filesystem (mv is a rename)", same_fs == "SAME", same_fs
        )

        t = phase("Capture the snapshot")
        capture = client.capture_snapshot(builder.name, capture_name, timeout=1800)
        timings["capture"] = time.monotonic() - t
        print(f"  capture {capture.id} ({timings['capture']:.1f}s)", flush=True)

        t = phase("Boot a fresh sandbox from the capture")
        runtime = client.create_sandbox(
            snapshot_id=capture.id,
            name=f"openswe-e2e-runtime-{stamp}",
            idle_ttl_seconds=IDLE_TTL_SECONDS,
            delete_after_stop_seconds=DELETE_AFTER_STOP_SECONDS,
        )
        started_sandboxes.append(runtime.name)
        timings["runtime_boot"] = time.monotonic() - t

        runtime_pwd, _ = run(runtime, "pwd")
        runtime_wd = PREFERRED_WORK_DIR
        writable, _ = run(runtime, f"test -d {runtime_wd} && test -w {runtime_wd} && echo OK")
        check(
            "A2  the pinned work dir survives capture and is writable",
            writable == "OK",
            f"pinned={runtime_wd} writable={writable} raw pwd={runtime_pwd}",
        )

        listing, _ = run(runtime, f"ls -a {runtime_wd}; echo '--'; ls {cache_dir} 2>&1 | head -3")
        survived, _ = run(runtime, f"[ -d {cache_dir}/.git ] && echo PRESENT || echo MISSING")
        check(
            "A1  capture preserved the cache in the work dir",
            survived == "PRESENT",
            survived,
        )
        print(f"  work dir contents:\n{listing}", flush=True)

        t = phase("Take the repo out of the cache (the real clone script)")
        script = build_clone_script(work_dir=runtime_wd, owner=owner, name=name)
        out, code = run(runtime, script, timeout=600)
        timings["take_from_cache"] = time.monotonic() - t
        fields = parse_clone_result(out)
        check(
            "A5  clone script succeeded against the captured cache",
            code == 0 and fields.get("path") != "",
            f"exit={code} {fields or out[-400:]}",
        )
        check(
            "     came from the cache, not the network",
            fields.get("source") == "cache",
            f"source={fields.get('source')}",
        )
        check(
            "     fetched origin after taking it",
            fields.get("fetched") == "true",
            f"fetched={fields.get('fetched')}",
        )

        history, _ = run(runtime, f"cd {runtime_wd}/{name} && git rev-list --count HEAD")
        check(
            "     full history, not shallow",
            history.isdigit() and int(history) > 100,
            f"{history} commits",
        )
        shallow, _ = run(
            runtime, f"cd {runtime_wd}/{name} && git rev-parse --is-shallow-repository"
        )
        check("     repository is not shallow", shallow == "false", shallow)

        gone, _ = run(runtime, f"[ -d {cache_dir} ] && echo STILL_THERE || echo CONSUMED")
        check("     cache entry consumed by the move", gone == "CONSUMED", gone)

        t = phase("Cold clone for comparison (no cache)")
        cold = build_clone_script(work_dir=f"{runtime_wd}/cold", owner=owner, name=name)
        run(runtime, f"mkdir -p {runtime_wd}/cold")
        _out, _code = run(runtime, cold, timeout=900)
        timings["cold_clone"] = time.monotonic() - t

    finally:
        print("\n== Cleanup", flush=True)
        for sandbox_name in started_sandboxes:
            try:
                client.stop_sandbox(sandbox_name)
                print(f"  stopped {sandbox_name}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"  could not stop {sandbox_name}: {type(e).__name__}", flush=True)
        # Swept by name, not by a list built as we go: a snapshot that times out
        # mid-build never makes it into such a list and would be left orphaned.
        # A snapshot cannot be deleted while a sandbox still references it, and
        # stopped sandboxes are only reclaimed delete_after_stop_seconds later,
        # so retry rather than leaving snapshots behind in a shared workspace.
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            leftover = list(client.list_snapshots(name_contains=run_prefix, limit=50))
            if not leftover:
                print("  no snapshots left", flush=True)
                break
            for snapshot in leftover:
                try:
                    client.delete_snapshot(snapshot.id)
                    print(f"  deleted snapshot {snapshot.name}", flush=True)
                except Exception:  # noqa: BLE001
                    pass
            else:
                time.sleep(30)
        else:
            names = [s.name for s in client.list_snapshots(name_contains=run_prefix, limit=50)]
            print(f"  LEFTOVER SNAPSHOTS, delete by hand: {names}", flush=True)
        client.close()

    print("\n== Timings")
    for label, seconds in timings.items():
        print(f"  {label:18s} {seconds:7.1f}s")
    if "cold_clone" in timings and "take_from_cache" in timings:
        speedup = timings["cold_clone"] / max(timings["take_from_cache"], 0.001)
        print(f"  cache vs cold clone: {speedup:.1f}x faster")

    failures = [label for label, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failures)}/{len(results)} checks passed")
    for label in failures:
        print(f"  FAILED: {label}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
