# Runs after python_extract/prelude.py, which binds `tree` to the parsed source.
# Prints `INGRESS <provider>` when the module-level `channel` binding is a
# first-party `channels.slack(...)`, `channels.github(...)` or
# `channels.linear(...)` call, which mounts Events ingress, and a bare `INGRESS`
# when the binding cannot be resolved.

CHANNELS = "channels"
INGRESS_PROVIDERS = {"slack", "github", "linear"}
aliases = {CHANNELS}
namespaces = set()
bindings = {}

for node in tree.body:
    if isinstance(node, ast.ImportFrom):
        for alias in node.names:
            if alias.name == CHANNELS:
                aliases.add(alias.asname or alias.name)
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


def ingress_factory_provider(func):
    if not isinstance(func, ast.Attribute) or func.attr not in INGRESS_PROVIDERS:
        return None
    obj = func.value
    if isinstance(obj, ast.Name) and obj.id in aliases:
        return func.attr
    if (
        isinstance(obj, ast.Attribute)
        and obj.attr == CHANNELS
        and isinstance(obj.value, ast.Name)
        and obj.value.id in namespaces
    ):
        return func.attr
    return None


def resolve(node, depth=0):
    if depth > 8:
        return None
    if isinstance(node, ast.Name) and node.id in bindings:
        return resolve(bindings[node.id], depth + 1)
    return node


UNRESOLVED = ""


def value_ingress(value, depth=0):
    """Return the ingress provider, UNRESOLVED, or None for non-ingress values."""
    if depth > 8:
        return UNRESOLVED
    value = resolve(value, depth)
    if value is None:
        return UNRESOLVED
    if isinstance(value, ast.Call):
        return ingress_factory_provider(value.func)
    if isinstance(value, ast.Name):
        return UNRESOLVED
    return None


for node in ast.iter_child_nodes(tree):
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    else:
        continue
    names = {target.id for target in targets if isinstance(target, ast.Name)}
    if "channel" not in names:
        continue
    provider = value_ingress(value)
    if provider is not None:
        print(f"INGRESS {provider}".rstrip())
        break
