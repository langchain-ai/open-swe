from langsmith.sandbox import SandboxClient

c = SandboxClient()
for s in c.list_snapshots():
    print(s.id, s.name, s.status)
