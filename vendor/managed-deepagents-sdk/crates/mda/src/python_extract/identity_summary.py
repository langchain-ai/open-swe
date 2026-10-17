# Runs after python_extract/prelude.py, which binds `tree` to the parsed source.
# Prints JSON `{auth, auth_omitted, scope, declares_memory}` for the identity binding (null
# fields when unknown). `declares_memory` reports the retired `scope.memory`
# axis, which deploy turns into an actionable migration error.
import json

DEFINE = "define_identity"
aliases = {DEFINE}
namespaces = set()

for node in tree.body:
    if isinstance(node, ast.ImportFrom):
        for alias in node.names:
            if alias.name == DEFINE:
                aliases.add(alias.asname or alias.name)
    elif isinstance(node, ast.Import):
        for alias in node.names:
            mod = alias.name
            if mod == "managed_deepagents" or mod.endswith(".managed_deepagents"):
                namespaces.add(alias.asname or mod.split(".")[-1])

# Managed identity keys downstream credentials on the caller. Durable memory is
# declared in memory.py and is deliberately absent here.
MANAGED_SCOPE = {"credentials": "user"}


def const_str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def is_define_identity(node):
    if isinstance(node, ast.Name) and node.id in aliases:
        return True
    if (
        isinstance(node, ast.Attribute)
        and node.attr == DEFINE
        and isinstance(node.value, ast.Name)
        and node.value.id in namespaces
    ):
        return True
    return False


def auth_kind(node):
    if (value := const_str(node)) is not None:
        return "backend" if value == "backend" else None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "langsmith_api_key"
    ):
        return "langsmith_api_key"
    if isinstance(node, (ast.Call, ast.List, ast.Tuple, ast.Dict)):
        return "validated_token"
    return None


def declares_scope_memory(scope_node):
    """True when `scope` is a dict literal carrying the retired `memory` axis."""
    if not isinstance(scope_node, ast.Dict):
        return False
    return any(const_str(key) == "memory" for key in scope_node.keys if key is not None)


def from_call(call):
    if not is_define_identity(call.func):
        return None, False, None, False
    kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
    auth_omitted = "auth" not in kwargs and not any(kw.arg is None for kw in call.keywords)
    auth = auth_kind(kwargs["auth"]) if "auth" in kwargs else None
    return auth, auth_omitted, MANAGED_SCOPE.copy(), declares_scope_memory(kwargs.get("scope"))


auth = None
auth_omitted = False
scope = None
declares_memory = False
for node in ast.iter_child_nodes(tree):
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    else:
        continue
    names = {t.id for t in targets if isinstance(t, ast.Name)}
    if "identity" in names and isinstance(value, ast.Call):
        # Skip unrelated calls assigned to `identity` (e.g. helpers) so a later
        # `define_identity(...)` still wins.
        if not is_define_identity(value.func):
            continue
        auth, auth_omitted, scope, declares_memory = from_call(value)
        break

print(
    json.dumps(
        {
            "auth": auth,
            "auth_omitted": auth_omitted,
            "scope": scope,
            "declares_memory": declares_memory,
        },
        separators=(",", ":"),
    )
)
