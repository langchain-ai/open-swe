# Runs after python_extract/prelude.py, which binds `tree` to the parsed source.
# Prints `mcp` when the module-level `connector` binding is `define_mcp(...)`
# or the deprecated `connectors.mcp(...)` (or an import alias / namespace
# form), or nothing when it cannot be resolved statically.

DEFINE = "define_mcp"
CONNECTORS = "connectors"
define_aliases = {DEFINE}
connector_aliases = {CONNECTORS}
namespaces = set()
bindings = {}

for node in tree.body:
    if isinstance(node, ast.ImportFrom):
        for alias in node.names:
            if alias.name == DEFINE:
                define_aliases.add(alias.asname or alias.name)
            if alias.name == CONNECTORS:
                connector_aliases.add(alias.asname or alias.name)
    elif isinstance(node, ast.Import):
        for alias in node.names:
            mod = alias.name
            if mod == "managed_deepagents" or mod.endswith(".managed_deepagents"):
                namespaces.add(alias.asname or mod.split(".")[-1])


def bind_assign(targets, value):
    for target in targets:
        if isinstance(target, ast.Name):
            bindings[target.id] = value


for node in tree.body:
    if isinstance(node, ast.Assign):
        bind_assign(node.targets, node.value)
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        bind_assign([node.target], node.value)


def factory_name(func):
    if isinstance(func, ast.Name) and func.id in define_aliases:
        return "mcp"
    if (
        isinstance(func, ast.Attribute)
        and func.attr == DEFINE
        and isinstance(func.value, ast.Name)
        and func.value.id in namespaces
    ):
        return "mcp"
    if isinstance(func, ast.Attribute) and func.attr == "mcp":
        obj = func.value
        if isinstance(obj, ast.Name) and obj.id in connector_aliases:
            return "mcp"
        if (
            isinstance(obj, ast.Attribute)
            and obj.attr == CONNECTORS
            and isinstance(obj.value, ast.Name)
            and obj.value.id in namespaces
        ):
            return "mcp"
    return None


def resolve(node, depth=0):
    if depth > 8:
        return None
    if isinstance(node, ast.Name) and node.id in bindings:
        return resolve(bindings[node.id], depth + 1)
    return node


value = bindings.get("connector")
value = resolve(value)
if isinstance(value, ast.Call):
    name = factory_name(value.func)
    if name is not None:
        print(name)
