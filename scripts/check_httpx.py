import ast
import re
import sys
import tokenize
from collections import Counter
from collections.abc import Iterable
from io import StringIO
from pathlib import Path

CLIENTS = frozenset({"AsyncClient", "Client", "AsyncHTTPTransport", "HTTPTransport"})
REASON = "Use httpx2 for owned HTTP clients and transports; httpx is for SDK compatibility only."


class HttpxCheck:
    def __init__(
        self, path: Path, previous: str | None = None, added: set[int] | None = None
    ) -> None:
        source = path.read_text()
        self.tree = ast.parse(source, filename=str(path))
        self.sdk_exceptions = {
            token.start[0]
            for token in tokenize.generate_tokens(StringIO(source).readline)
            if token.type == tokenize.COMMENT and re.fullmatch(r"# httpx-sdk: \S.*", token.string)
        }
        self.parents = {
            child: node for node in ast.walk(self.tree) for child in ast.iter_child_nodes(node)
        }
        self.existing_imports: set[ast.alias] = set()
        if previous is not None:
            tree = ast.parse(previous)
            parents = {
                child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
            }
            imports = Counter(
                self.import_key(node, parents)
                for node in ast.walk(tree)
                if isinstance(node, ast.alias)
            )
            for alias in sorted(
                (node for node in ast.walk(self.tree) if isinstance(node, ast.alias)),
                key=lambda node: (
                    bool(
                        added
                        and added.intersection(
                            range(node.lineno, (node.end_lineno or node.lineno) + 1)
                        )
                    ),
                    node.lineno,
                    node.col_offset,
                ),
            ):
                key = self.import_key(alias, self.parents)
                if imports[key]:
                    self.existing_imports.add(alias)
                    imports[key] -= 1
        self.bindings: dict[
            ast.AST, dict[str, list[tuple[ast.AST, ast.expr | ast.alias | None]]]
        ] = {}
        for node in ast.walk(self.tree):
            scope = self.scope_of(node)
            bindings = self.bindings.setdefault(scope, {})
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bindings.setdefault(alias.asname or alias.name, []).append((alias, alias))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        bindings.setdefault(target.id, []).append((target, node.value))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bindings.setdefault(node.name, []).append((node, None))
            elif isinstance(node, ast.arg):
                bindings.setdefault(node.arg, []).append((node, None))

    @classmethod
    def violations(cls, diff: Iterable[str]) -> list[str]:
        added: dict[str, set[int]] = {}
        hunks: dict[str, list[tuple[int, int, list[str]]]] = {}
        path = ""
        line_number = 0
        for line in diff:
            if line.startswith("+++ "):
                path = line[6:].strip() if line.startswith("+++ b/") else ""
            elif line.startswith("@@ "):
                match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
                if match is None:
                    raise ValueError(f"Invalid diff hunk: {line}")
                line_number = int(match[1])
                count = int(match[2]) if match[2] is not None else 1
                start = line_number - 1 if count else line_number
                hunks.setdefault(path, []).append((max(start, 0), count, []))
            elif line.startswith("+"):
                if path:
                    added.setdefault(path, set()).add(line_number)
                line_number += 1
            elif line.startswith("-") and not line.startswith("--- "):
                if path and hunks.get(path):
                    hunks[path][-1][2].append(line[1:])
            elif line.startswith(" "):
                if path and hunks.get(path):
                    hunks[path][-1][2].append(line[1:])
                line_number += 1
        errors: list[str] = []
        for path, lines in added.items():
            previous = Path(path).read_text().splitlines(keepends=True)
            for start, count, original in reversed(hunks.get(path, [])):
                previous[start : start + count] = original
            errors.extend(
                f"{path}:{line}: {REASON}"
                for line in cls(Path(path), "".join(previous), lines).client_lines(lines)
            )
        return errors

    @staticmethod
    def import_key(
        alias: ast.alias, parents: dict[ast.AST, ast.AST]
    ) -> tuple[tuple[str, ...], str | None, str, str | None]:
        node = parents[alias]
        module = node.module if isinstance(node, ast.ImportFrom) else None
        scope: list[str] = []
        while node in parents:
            child = node
            node = parents[node]
            if isinstance(node, ast.If):
                scope.append(f"{ast.dump(node.test)}:{child in node.body}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope.append(node.name)
        return tuple(scope), module, alias.name, alias.asname

    def client_lines(self, added: set[int]) -> list[int]:
        violations: set[int] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and node.lineno not in self.sdk_exceptions:
                for name, lines in self.resolve(node.func, set()):
                    if name in {f"httpx.{client}" for client in CLIENTS}:
                        violations.update(added.intersection(lines))
        return sorted(violations)

    def scope_of(self, node: ast.AST) -> ast.AST:
        parent = self.parents.get(node, self.tree)
        while not isinstance(
            parent, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            parent = self.parents[parent]
        return parent

    def immediately_invoked(self, function: ast.AST) -> bool:
        if not isinstance(function, ast.FunctionDef) or function.decorator_list:
            return False
        if any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in ast.walk(function)):
            return False
        parent = self.parents[function]
        for _, statements in ast.iter_fields(parent):
            if not isinstance(statements, list) or function not in statements:
                continue
            index = statements.index(function) + 1
            if index >= len(statements):
                return False
            statement = statements[index]
            if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
                return False
            call = statement.value
            if (
                not isinstance(call.func, ast.Name)
                or call.func.id != function.name
                or call.args
                or call.keywords
            ):
                return False
            references = [
                node
                for node in ast.walk(self.scope_of(function))
                if isinstance(node, ast.Name) and node.id == function.name
            ]
            return references == [call.func]
        return False

    def branches(self, node: ast.AST, scope: ast.AST | None = None) -> dict[ast.If, bool]:
        branches: dict[ast.If, bool] = {}
        scope = scope if scope is not None else self.scope_of(node)
        deferred = False
        while node in self.parents and node is not scope:
            parent = self.parents[node]
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                deferred = deferred or not self.immediately_invoked(parent)
            if deferred and isinstance(parent, (ast.For, ast.AsyncFor, ast.While)):
                branches.clear()
            if isinstance(parent, ast.If) and node is not parent.test:
                branches[parent] = node in parent.body
            node = parent
        return branches

    def reaching_bindings(
        self, node: ast.Name, scope: ast.AST
    ) -> list[ast.expr | ast.alias | None]:
        local = scope is self.scope_of(node)
        remaining = [(self.branches(node, scope), False)]
        bindings: list[ast.expr | ast.alias | None] = []
        ordered = sorted(
            self.bindings[scope][node.id],
            key=lambda item: (item[0].lineno, item[0].col_offset),
            reverse=True,
        )
        loop: ast.For | ast.AsyncFor | ast.While | None = None
        ancestor: ast.AST = node
        while ancestor in self.parents and ancestor is not scope:
            ancestor = self.parents[ancestor]
            if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                if not self.immediately_invoked(ancestor):
                    break
            if isinstance(ancestor, (ast.For, ast.AsyncFor, ast.While)):
                loop = ancestor
                break
        candidates = [(origin, value, self.branches(origin), False) for origin, value in ordered]
        if loop is not None:
            remaining += [(path.copy(), True) for path, _ in remaining]
            repeated = set(ast.walk(loop))
            candidates = (
                [
                    (origin, value, conditions, carried)
                    for origin, value, conditions, carried in candidates
                    if origin in repeated and origin.lineno < node.lineno
                ]
                + [
                    (
                        origin,
                        value,
                        {
                            branch: side
                            for branch, side in conditions.items()
                            if branch not in repeated
                        },
                        True,
                    )
                    for origin, value, conditions, _ in candidates
                    if origin in repeated
                ]
                + [
                    (origin, value, conditions, carried)
                    for origin, value, conditions, carried in candidates
                    if origin not in repeated
                ]
            )
        for origin, value, conditions, carried in candidates:
            if (
                local
                and not carried
                and (origin.lineno, origin.col_offset) >= (node.lineno, node.col_offset)
            ):
                continue
            uncovered: list[tuple[dict[ast.If, bool], bool]] = []
            for path, first_iteration in remaining:
                if (carried and first_iteration) or any(
                    branch in path and path[branch] != side for branch, side in conditions.items()
                ):
                    uncovered.append((path, first_iteration))
                    continue
                bindings.append(value)
                path = path.copy()
                for branch, side in conditions.items():
                    if branch not in path:
                        uncovered.append((path | {branch: not side}, first_iteration))
                        path[branch] = side
            remaining = uncovered
            if not remaining:
                break
        return bindings

    def resolve(self, node: ast.expr, seen: set[ast.AST]) -> list[tuple[str, set[int]]]:
        if node in seen:
            return []
        seen = seen | {node}
        lines = set(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        if isinstance(node, ast.Attribute):
            return [
                (f"{name}.{node.attr}", origins | lines)
                for name, origins in self.resolve(node.value, seen)
            ]
        if not isinstance(node, ast.Name):
            return []
        scope = self.scope_of(node)
        while node.id not in self.bindings.get(scope, {}):
            if scope is self.tree:
                return []
            scope = self.scope_of(scope)
            while isinstance(scope, ast.ClassDef):
                scope = self.scope_of(scope)
        resolved: list[tuple[str, set[int]]] = []
        for binding in self.reaching_bindings(node, scope):
            if isinstance(binding, ast.alias):
                parent = self.parents[binding]
                name = (
                    f"{parent.module}.{binding.name}"
                    if isinstance(parent, ast.ImportFrom)
                    else binding.name
                )
                origins = (
                    set()
                    if binding in self.existing_imports
                    else set(range(binding.lineno, (binding.end_lineno or binding.lineno) + 1))
                )
                resolved.append((name, lines | origins))
            elif isinstance(binding, ast.expr):
                resolved.extend(
                    (name, origins | lines) for name, origins in self.resolve(binding, seen)
                )
        return resolved


if __name__ == "__main__":
    errors = HttpxCheck.violations(sys.stdin)
    for error in errors:
        print(error, file=sys.stderr)
    sys.exit(bool(errors))
