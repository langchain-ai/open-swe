# Runs after python_extract/prelude.py, which binds `tree` to the parsed source.
# Prints JSON `{kind, detail}` for the root `memory` declaration, where kind is
# "absent" (no `define_memory` binding), "agent", "user", "agent_user", "none",
# "duplicate_agent" / "duplicate_user" (a layer type listed twice), "shorthand"
# (the retired string form), "reserved" (a per-layer option MDA recognizes but
# does not accept yet), "unknown" (a layer outside the contract), or "dynamic"
# (not a literal at all).
#
# Deploy seeds durable memory from this answer, so an unresolvable value is
# reported rather than defaulted: guessing would either seed a tree the author
# did not ask for or drop the one they did.
import json

DEFINE = "define_memory"
aliases = {DEFINE}
namespaces = set()
layer_aliases = {"MemoryLayer"}

for node in tree.body:
    if isinstance(node, ast.ImportFrom):
        for alias in node.names:
            if alias.name == "MemoryLayer":
                layer_aliases.add(alias.asname or alias.name)
            if alias.name == DEFINE:
                aliases.add(alias.asname or alias.name)
    elif isinstance(node, ast.Import):
        for alias in node.names:
            mod = alias.name
            if mod == "managed_deepagents" or mod.endswith(".managed_deepagents"):
                namespaces.add(alias.asname or mod.split(".")[-1])


def const_str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def is_define_memory(node):
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


def layer_type_value(layer_type, has_id):
    if layer_type == "agent":
        # The deployment owns the agent layer, so there is no id to assert.
        if has_id:
            return {"kind": "unknown", "detail": "an `id` on the agent layer"}
        return {"kind": "agent", "detail": None}
    if layer_type == "user":
        return {"kind": "user", "detail": None}
    return {"kind": "unknown", "detail": f"type {layer_type!r}"}


def layer_entry(node):
    """Resolve one layer dict (`{"type": "user", "id": "..."}`).

    `type` and `id` must be static. The runtime validates and calls a user layer's
    optional `allow` callback; extraction does not evaluate or serialize it.
    """
    if not isinstance(node, ast.Dict):
        text = const_str(node)
        if text is not None:
            return {"kind": "shorthand", "detail": f'"{text}"'}
        return {"kind": "dynamic", "detail": "a dynamic list entry"}
    layer_type = None
    has_id = False
    for key_node, value_node in zip(node.keys, node.values):
        if key_node is None:
            return {"kind": "dynamic", "detail": "a ** entry inside a layer"}
        key = const_str(key_node)
        if key is None:
            return {"kind": "reserved", "detail": "a computed key"}
        if key == "type":
            layer_type = const_str(value_node)
            if layer_type is None:
                return {"kind": "dynamic", "detail": "a computed layer `type`"}
        elif key == "id":
            has_id = True
            if const_str(value_node) is None:
                return {"kind": "dynamic", "detail": "a computed layer `id`"}
        elif key != "allow":
            return {"kind": "reserved", "detail": f"the key `{key}`"}
    if layer_type is None:
        return {"kind": "unknown", "detail": "a layer with no `type`"}
    return layer_type_value(layer_type, has_id)


def reduce_entries(entries):
    """Reduce a list of layer entries to the canonical mounted set."""
    agent = False
    user = False
    for entry in entries:
        if entry["kind"] == "agent":
            if agent:
                return {"kind": "duplicate_agent", "detail": None}
            agent = True
        elif entry["kind"] == "user":
            if user:
                return {"kind": "duplicate_user", "detail": None}
            user = True
        else:
            # A duplicate, shorthand, reserved, unknown, or dynamic entry decides
            # the whole list.
            return entry
    if agent and user:
        return {"kind": "agent_user", "detail": None}
    if agent:
        return {"kind": "agent", "detail": None}
    if user:
        return {"kind": "user", "detail": None}
    return {"kind": "none", "detail": None}


def layers_value(node):
    if node is None:
        return {"kind": "none", "detail": None}
    if isinstance(node, ast.Dict):
        return layer_entry(node)
    if isinstance(node, (ast.List, ast.Tuple)):
        return reduce_entries([layer_entry(element) for element in node.elts])
    text = const_str(node)
    if text is not None:
        return {"kind": "shorthand", "detail": f'"{text}"'}
    return {"kind": "dynamic", "detail": "a computed value"}


def named_layer(node, name):
    if not isinstance(node, ast.Call):
        return {"kind": "dynamic", "detail": f"{name} must use MemoryLayer(...)"}
    func = node.func
    recognized = (
        isinstance(func, ast.Name) and func.id in layer_aliases
        or isinstance(func, ast.Attribute) and func.attr == "MemoryLayer"
        and isinstance(func.value, ast.Name) and func.value.id in namespaces
    )
    if not recognized or node.args or any(kw.arg is None for kw in node.keywords):
        return {"kind": "dynamic", "detail": f"{name} must use MemoryLayer(allow=...)"}
    if any(kw.arg != "allow" for kw in node.keywords):
        return {"kind": "unknown", "detail": "MemoryLayer only accepts allow"}
    return {"kind": name, "detail": None}


def from_call(call):
    kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
    # A positional argument is not part of the keyword-only surface; the runtime
    # rejects it, and reporting it keeps the build from guessing at scope.
    if call.args:
        return {"kind": "dynamic", "detail": "a positional argument"}
    # A `**mapping` entry can supply `scope` without it appearing here at all, so
    # the value is not readable: deploy seeds from this answer and must not guess.
    if any(kw.arg is None for kw in call.keywords):
        return {"kind": "dynamic", "detail": "a ** entry that can set scope"}
    named = [name for name in ("agent", "user") if name in kwargs]
    if named:
        if "scope" in kwargs:
            return {"kind": "unknown", "detail": "cannot mix scope with agent or user"}
        if any(key not in {"agent", "user"} for key in kwargs):
            return {"kind": "unknown", "detail": "unsupported memory option"}
        return reduce_entries([named_layer(kwargs[name], name) for name in named])
    # Named so the retired shorthand reports the new shape rather than reading as
    # "no memory declared".
    if "scope" not in kwargs and "layers" in kwargs:
        return {"kind": "shorthand", "detail": "`scope`"}
    return layers_value(kwargs.get("scope"))


result = {"kind": "absent", "detail": None}

for node in ast.iter_child_nodes(tree):
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    else:
        continue
    names = {target.id for target in targets if isinstance(target, ast.Name)}
    if "memory" not in names or not isinstance(value, ast.Call):
        continue
    # Skip unrelated calls assigned to `memory` so a later `define_memory(...)`
    # still wins.
    if not is_define_memory(value.func):
        continue
    result = from_call(value)
    break

print(json.dumps(result, separators=(",", ":")))
