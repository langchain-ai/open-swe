import json, sqlite3
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.channels.transcript import TranscriptEntry, TranscriptRef, TranscriptChannel
t = sqlite3.connect("file:SCRATCH/transcript-long.sqlite?mode=ro", uri=True)
serde = JsonPlusSerializer()
rows = t.execute("select seq, kind, item_id, type, blob from transcript_entries order by seq").fetchall()
ch = TranscriptChannel().from_checkpoint(TranscriptRef("x", rows[-1][0], entries=[TranscriptEntry(s, k, i, serde.loads_typed((ty, b))) for s, k, i, ty, b in rows]))
import re
norm = lambda typ, content, calls: (typ, re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}|\b[0-9a-f]{7,40}\b|[0-9]", "0", str(content)), tuple(c["name"] for c in calls or []))
tm = [norm(m.type, m.content, getattr(m, "tool_calls", None)) for m in ch.get()]
dm = [norm(m["type"], m["content"], m.get("tool_calls")) for m in json.load(open("SCRATCH/delta-long-state.json"))["values"]["messages"]]
print("same type/content/tool-call sequence:", tm == dm, len(tm), len(dm))
diffs = [(i, a, b) for i, (a, b) in enumerate(zip(tm, dm)) if a != b]
print("diffs:", len(diffs))
for i, a, b in diffs[:3]: print(i, a[:2], "|", b[:2])
