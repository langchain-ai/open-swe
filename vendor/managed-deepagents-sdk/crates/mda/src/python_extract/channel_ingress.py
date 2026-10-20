# Runs after python_extract/prelude.py, which binds `tree` to the parsed source.
# Prints INGRESS when the module-level `channel` binding is a first-party
# `channels.slack(...)` call, which mounts Events ingress.

CHANNELS = "channels"
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


def is_slack_factory(func):
    if not isinstance(func, ast.Attribute) or func.attr != "slack":
        return False
    obj = func.value
    if isinstance(obj, ast.Name) and obj.id in aliases:
        return True
    return (
        isinstance(obj, ast.Attribute)
        and obj.attr == CHANNELS
        and isinstance(obj.value, ast.Name)
        and obj.value.id in namespaces
    )


def resolve(node, depth=0):
    if depth > 8:
        return None
    if isinstance(node, ast.Name) and node.id in bindings:
        return resolve(bindings[node.id], depth + 1)
    return node


def call_declares_ingress(call):
    return is_slack_factory(call.func)


def value_declares_ingress(value, depth=0):
    if depth > 8:
        return True
    value = resolve(value, depth)
    if value is None:
        return True
    if isinstance(value, ast.Call):
        return call_declares_ingress(value)
    return isinstance(value, ast.Name)


for node in ast.iter_child_nodes(tree):
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    else:
        continue
    names = {target.id for target in targets if isinstance(target, ast.Name)}
    if "channel" in names and value_declares_ingress(value):
        print("INGRESS")
        break
