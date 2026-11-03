"""Drive one long open-swe Slack thread through the e2e harness, then print storage."""
import sqlite3, sys, time
import httpx

B, M = "http://127.0.0.1:2024", "http://127.0.0.1:2025"
DB = "/Users/cwlbraa/src/open-swe-transcript/tests/e2e/.mda-build/.langgraph_api/core.sqlite"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 25

def wait_idle(thread_id: str, want_runs: int) -> None:
    deadline = time.time() + 300
    while time.time() < deadline:
        runs = httpx.get(f"{M}/threads/{thread_id}/runs", params={"limit": 200}).json()
        if isinstance(runs, list) and len(runs) >= want_runs and all(r["status"] not in ("pending", "running") for r in runs):
            bad = [r["status"] for r in runs if r["status"] != "success"]
            if bad:
                print("non-success runs:", bad)
            return
        time.sleep(1)
    raise TimeoutError(f"thread {thread_id} not idle with {want_runs} runs")

httpx.post(f"{B}/control/reset").raise_for_status()
sent = httpx.post(f"{B}/mock/slack/send", json={"text": "<@U0BOT> please add a greet() helper and open a PR"}).json()
thread_ts, thread_id = sent["thread_ts"], sent["thread_id"]
t0 = time.time()
wait_idle(thread_id, 1)
for i in range(N):
    text = f"<@U0BOT> E2E_SUBAGENT_TASK follow-up {i}" if i == 4 else f"<@U0BOT> follow-up {i}: can you also double check the tests?"
    httpx.post(f"{B}/mock/slack/send", json={"text": text, "thread_ts": thread_ts}).raise_for_status()
    wait_idle(thread_id, i + 2)
print(f"thread {thread_id}: {N + 1} runs in {time.time() - t0:.1f}s")

def timed(fn, n=5):
    ts = []
    for _ in range(n):
        s = time.perf_counter(); fn(); ts.append((time.perf_counter() - s) * 1000)
    return f"median {sorted(ts)[n // 2]:.1f}ms"

state = httpx.get(f"{M}/threads/{thread_id}/state").json()
print("messages in state:", len(state["values"]["messages"]))
print("get_state:", timed(lambda: httpx.get(f"{M}/threads/{thread_id}/state").raise_for_status()))
print("history(limit=100):", timed(lambda: httpx.post(f"{M}/threads/{thread_id}/history", json={"limit": 100}, timeout=60).raise_for_status()))

db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
q = lambda sql: db.execute(sql).fetchall()
print("checkpoints (all ns):", q("select count(*), coalesce(sum(length(checkpoint)+length(metadata)),0) from checkpoints"))
print("checkpoints by ns kind:", q("select case when checkpoint_ns='' then 'root' else 'sub' end, count(*) from checkpoints group by 1"))
print("blobs total:", q("select count(*), coalesce(sum(length(blob)),0) from checkpoint_blobs"))
print("blobs[messages] (type):", q("select type, count(*), coalesce(sum(length(blob)),0) from checkpoint_blobs where channel='messages' group by type"))
print("writes[messages]:", q("select count(*), coalesce(sum(length(blob)),0) from checkpoint_writes where channel='messages'"))
try:
    print("transcripts:", q("select count(*), coalesce(max(head),0), sum(parent_transcript_id is not null) from transcripts"))
    print("transcript_entries by kind:", q("select kind, count(*), coalesce(sum(length(blob)),0) from transcript_entries group by kind"))
except sqlite3.OperationalError as e:
    print("transcripts:", e)
