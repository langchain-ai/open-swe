"""Runs inside a sandbox to find the Python dependencies a pull request adds between modules.

Both commits are read straight from git, so neither needs to be checked out.
Run through ``SandboxScript`` with a request of:

- ``repo``: the clone to read.
- ``base`` and ``head``: the commits to compare, normally the merge base and the head.
- ``roots``: directories holding top-level packages, as ``sys.path`` entries would;
  ``""`` is the repository root.
- ``exclude``: optional ``fnmatch`` globs of paths to leave out, such as tests.
- ``packages``: optional module prefixes to group modules by. A module under none
  of them is grouped by its first ``package_depth`` (default 2) components.
- ``rules``: optional ``{"name", "source", "forbidden", "allow"}`` objects, each a
  list of module patterns: a new import from ``source`` into ``forbidden`` that
  is not into ``allow`` breaks the rule. A pattern matches a module or any of
  its parent packages, with ``fnmatch`` wildcards.

The answer lists, for modules and for packages, every dependency the head adds
and every cycle it creates or grows. An import inside a function counts toward
cycles, since moving an import into a function is how cycles get hidden; one
under ``if TYPE_CHECKING:`` does not, since it never runs.
"""

import ast
import base64
import collections
import fnmatch
import json
import os
import subprocess
import sys
import warnings

GIT = ["git", "-c", "core.quotePath=false"]
RUNTIME = ("top", "local")
MAX_EDGES = 200
MAX_CYCLES = 50
MAX_CYCLE_EDGES = 20
MAX_UNPARSED = 50


class Refused(Exception):
    pass


def git_bytes(args, stdin=None):
    result = subprocess.run([*GIT, *args], input=stdin, capture_output=True)
    if result.returncode != 0:
        raise Refused(result.stderr.decode(errors="replace").strip()[-2000:] or "git failed")
    return result.stdout


def read_blobs(shas):
    if not shas:
        return {}
    data = git_bytes(["cat-file", "--batch"], stdin="".join(sha + "\n" for sha in shas).encode())
    texts = {}
    at = 0
    for sha in shas:
        end = data.index(b"\n", at)
        header = data[at:end].split()
        if header[-1] == b"missing":
            at = end + 1
            continue
        size = int(header[2])
        texts[sha] = data[end + 1 : end + 1 + size].decode("utf-8", errors="replace")
        at = end + 1 + size + 1
    return texts


def matches(module, patterns):
    parts = module.split(".")
    return any(
        fnmatch.fnmatchcase(".".join(parts[:depth]), pattern)
        for pattern in patterns
        for depth in range(1, len(parts) + 1)
    )


def is_type_checking(test):
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def imports_in(text, module, is_package):
    """Each import in a module's source as ``(dotted name, kind, line)``.

    ``kind`` is ``top`` for an import that runs when the module loads, ``local``
    for one inside a function, and ``typing`` for one under ``if TYPE_CHECKING:``.
    ``from a import b`` is named ``a.b``, to be resolved to the longest prefix
    that is a module.
    """
    tree = ast.parse(text)
    package = module.split(".") if is_package else module.split(".")[:-1]
    found = []

    def absolute(node):
        if not node.level:
            return node.module or ""
        if node.level - 1 > len(package):
            return None
        parts = package[: len(package) - (node.level - 1)]
        return ".".join(parts + (node.module.split(".") if node.module else []))

    def visit(node, kind):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, kind, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            source = absolute(node)
            if source is None:
                return
            for alias in node.names:
                if alias.name == "*":
                    found.append((source, kind, node.lineno))
                else:
                    found.append(
                        (source + "." + alias.name if source else alias.name, kind, node.lineno)
                    )
        elif isinstance(node, ast.If) and is_type_checking(node.test):
            for child in node.body:
                visit(child, "typing")
            for child in node.orelse:
                visit(child, kind)
        else:
            inner = kind
            if kind == "top" and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                inner = "local"
            for child in ast.iter_child_nodes(node):
                visit(child, inner)

    visit(tree, "top")
    return found


def _root(root):
    root = root.strip("/")
    while root.startswith("./"):
        root = root[2:]
    return "" if root == "." else root


class Layout:
    """Which files are modules, what they are named, and how they are grouped and ruled."""

    def __init__(self, request):
        self.roots = [_root(root) for root in request.get("roots") or [""]]
        self.exclude = list(request.get("exclude") or [])
        self.packages = sorted(request.get("packages") or [], key=len, reverse=True)
        self.depth = int(request.get("package_depth") or 2)
        self.rules = [
            {
                "name": str(rule["name"]),
                "source": list(rule["source"]),
                "forbidden": list(rule["forbidden"]),
                "allow": list(rule.get("allow") or []),
            }
            for rule in request.get("rules") or []
        ]

    def module(self, path):
        """The dotted module a file defines, or ``None`` when it is not one of ours."""
        if not path.endswith(".py") or any(
            fnmatch.fnmatchcase(path, glob) for glob in self.exclude
        ):
            return None
        prefix = None
        for root in self.roots:
            candidate = root + "/" if root else ""
            if path.startswith(candidate) and (prefix is None or len(candidate) > len(prefix)):
                prefix = candidate
        if prefix is None:
            return None
        parts = path[len(prefix) : -3].split("/")
        if parts[-1] == "__init__":
            parts.pop()
        if not parts or not all(part.isidentifier() for part in parts):
            return None
        return ".".join(parts)

    def package(self, module):
        for package in self.packages:
            if module == package or module.startswith(package + "."):
                return package
        return ".".join(module.split(".")[: self.depth])

    def broken_rules(self, source, target):
        return [
            rule["name"]
            for rule in self.rules
            if matches(source, rule["source"])
            and matches(target, rule["forbidden"])
            and not matches(target, rule["allow"])
        ]


class Snapshot:
    """One commit's modules and the imports between them."""

    def __init__(self, commit, layout, parsed):
        if not isinstance(commit, str) or not commit or commit.startswith("-"):
            raise Refused("base and head must be commits")
        pathspecs = [] if "" in layout.roots else ["--", *layout.roots]
        listing = git_bytes(["ls-tree", "-r", "-z", commit, *pathspecs]).decode(
            "utf-8", errors="replace"
        )
        files = {}
        for entry in listing.split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            mode, kind, sha = meta.split()
            module = layout.module(path)
            if kind != "blob" or mode == "120000" or module is None:
                continue
            # A package shadows a module of the same name, as it does on import.
            if module not in files or path.endswith("/__init__.py"):
                files[module] = (path, sha)
        self.paths = {module: path for module, (path, _) in files.items()}
        parsed.load({sha for _, sha in files.values()})
        self.unparsed = []
        raw = {}
        for module, (path, sha) in files.items():
            found = parsed.imports(sha, module, path.endswith("/__init__.py"))
            if found is None:
                self.unparsed.append(path)
            else:
                raw[module] = found
        # (source, target) -> {line: kind}
        self.edges = collections.defaultdict(dict)
        for module, found in raw.items():
            for dotted, kind, line in found:
                target = self.resolve(dotted)
                if target is not None and target != module:
                    self.edges[(module, target)].setdefault(line, kind)

    def resolve(self, dotted):
        parts = dotted.split(".")
        while parts:
            name = ".".join(parts)
            if name in self.paths:
                return name
            parts.pop()
        return None

    def runtime(self):
        """The module graph of imports that run, as adjacency sets over every module."""
        graph = {module: set() for module in self.paths}
        for (source, target), lines in self.edges.items():
            if any(kind in RUNTIME for kind in lines.values()):
                graph[source].add(target)
        return graph

    def packages(self, layout):
        """``(package edges -> kinds, runtime package graph)``."""
        kinds = collections.defaultdict(set)
        graph = {layout.package(module): set() for module in self.paths}
        for (source, target), lines in self.edges.items():
            source_package, target_package = layout.package(source), layout.package(target)
            if source_package == target_package:
                continue
            kinds[(source_package, target_package)].update(lines.values())
            if any(kind in RUNTIME for kind in lines.values()):
                graph[source_package].add(target_package)
        return kinds, graph


class Parsed:
    """Imports per blob, so a file both commits share is parsed once."""

    def __init__(self):
        self.texts = {}
        self._imports = {}

    def load(self, shas):
        self.texts.update(read_blobs(sorted(set(shas) - set(self.texts))))

    def imports(self, sha, module, is_package):
        key = (sha, module, is_package)
        if key not in self._imports:
            try:
                with warnings.catch_warnings():
                    # Invalid escapes and the like in the parsed code would otherwise print.
                    warnings.simplefilter("ignore")
                    self._imports[key] = imports_in(self.texts.get(sha, ""), module, is_package)
            except (SyntaxError, ValueError, RecursionError):
                self._imports[key] = None
        return self._imports[key]


def cycles_of(graph):
    """The graph's strongly connected components of more than one node (iterative Tarjan)."""
    index = {}
    low = {}
    on_stack = set()
    stack = []
    found = []
    for root in sorted(graph):
        if root in index:
            continue
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(sorted(graph[root])))]
        while work:
            node, children = work[-1]
            descended = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = len(index)
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph[child]))))
                    descended = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    found.append(sorted(component))
    return found


def shortest_path(graph, start, goal):
    previous = {start: None}
    queue = collections.deque([start])
    while queue:
        node = queue.popleft()
        if node == goal:
            path = []
            while node is not None:
                path.append(node)
                node = previous[node]
            return path[::-1]
        for child in sorted(graph.get(node, ())):
            if child not in previous:
                previous[child] = node
                queue.append(child)
    return None


def changed_cycles(base_graph, head_graph):
    """Each cycle in the head that is not one the base already had, or part of one.

    ``merged`` lists the base cycles it joins together, ``joined`` the nodes that
    were in no cycle before, and ``new`` which of those the base did not have at
    all. Each of ``edges`` is a dependency the head adds inside the cycle, with
    a ``path`` that closes the loop through it.
    """
    base_component = {}
    for component in cycles_of(base_graph):
        for node in component:
            base_component[node] = tuple(component)
    changed = []
    for component in sorted(cycles_of(head_graph), key=lambda nodes: (-len(nodes), nodes)):
        merged = sorted({base_component[node] for node in component if node in base_component})
        joined = [node for node in component if node not in base_component]
        if len(merged) <= 1 and not joined:
            continue
        members = set(component)
        edges = [
            {"source": source, "target": target, "path": shortest_path(head_graph, target, source)}
            for source in component
            for target in sorted(head_graph[source] & members)
            if target not in base_graph.get(source, ())
        ]
        for edge in edges:
            edge["path"] = [edge["source"], *edge["path"]]
        changed.append(
            {
                "nodes": component,
                "merged": [list(cycle) for cycle in merged] if len(merged) > 1 else [],
                "joined": joined,
                "new": [node for node in joined if node not in base_graph],
                "edges": edges[:MAX_CYCLE_EDGES],
                "edge_count": len(edges),
            }
        )
    return changed


def compare(request):
    layout = Layout(request)
    parsed = Parsed()
    base = Snapshot(request.get("base"), layout, parsed)
    head = Snapshot(request.get("head"), layout, parsed)

    module_edges = [
        {
            "source": source,
            "target": target,
            "path": head.paths[source],
            "imports": [{"line": line, "kind": lines[line]} for line in sorted(lines)],
            "rules": layout.broken_rules(source, target),
        }
        for (source, target), lines in sorted(head.edges.items())
        if (source, target) not in base.edges
    ]
    base_package_kinds, base_package_graph = base.packages(layout)
    head_package_kinds, head_package_graph = head.packages(layout)
    package_edges = []
    for (source, target), kinds in sorted(head_package_kinds.items()):
        if (source, target) in base_package_kinds:
            continue
        via = [
            [edge["source"], edge["target"]]
            for edge in module_edges
            if layout.package(edge["source"]) == source and layout.package(edge["target"]) == target
        ]
        package_edges.append(
            {"source": source, "target": target, "kinds": sorted(kinds), "via": via[:MAX_EDGES]}
        )
    module_cycles = changed_cycles(base.runtime(), head.runtime())
    package_cycles = changed_cycles(base_package_graph, head_package_graph)
    return {
        "modules": {"base": len(base.paths), "head": len(head.paths)},
        "unparsed": sorted(head.unparsed)[:MAX_UNPARSED],
        "module_edges": module_edges[:MAX_EDGES],
        "module_edge_count": len(module_edges),
        "module_cycles": module_cycles[:MAX_CYCLES],
        "package_edges": package_edges[:MAX_EDGES],
        "package_edge_count": len(package_edges),
        "package_cycles": package_cycles[:MAX_CYCLES],
    }


def main():
    request = json.loads(base64.b64decode(sys.argv[1]).decode())
    try:
        os.chdir(request["repo"])
        result = compare(request)
    except (Refused, OSError) as exc:
        result = {"error": str(exc)}
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
