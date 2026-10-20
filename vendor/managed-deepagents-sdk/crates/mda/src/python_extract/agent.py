# Runs after python_extract/prelude.py, which binds `tree` to the parsed source.
# Prints one JSON object with graph identity and static build metadata for the
# agent binding. `declares_disable_memory` reports the retired `disable_memory`
# keyword at all (any value, static or not), which the build turns into a
# migration error. `async_subagents` lists the names of subagent dicts marked
# `"async": True`; one whose name or flag isn't a literal is reported DYNAMIC.
# `unreadable_subagents` counts subagent entries MDA cannot read statically.
import json

DEFINE_DEEP_AGENT_UNSUPPORTED_KEYS = frozenset(
    {
        "backend",
        "store",
        "checkpointer",
        "instructions",
        "mcp",
        "memory",
        "sandbox",
        "skills",
        "system_prompt",
        "state_schema",
    }
)


def is_define_deep_agent_call(call):
    func = call.func
    return isinstance(func, ast.Name) and func.id == "define_deep_agent"


def name_keyword(call):
    for kw in call.keywords:
        if kw.arg != "name":
            continue
        if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return {"kind": "STATIC", "value": kw.value.value}
        return {"kind": "DYNAMIC"}
    return {"kind": "ABSENT"}


def model_keyword(call):
    for kw in call.keywords:
        if (
            kw.arg == "model"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            return kw.value.value
    return None


def declares_disable_memory(call):
    return any(kw.arg == "disable_memory" for kw in call.keywords)


def literal_bindings(statements):
    # Module- or factory-level names bound to dict, list, or tuple literals, or to
    # a subagent type call.
    bindings = {}
    for node in statements:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, (ast.Dict, ast.List, ast.Tuple)) or is_subagent_type_call(value):
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = value
    return bindings


def dict_entry(spec, key):
    for spec_key, value in zip(spec.keys, spec.values):
        if isinstance(spec_key, ast.Constant) and spec_key.value == key:
            return value
    return None


def async_subagent_name(spec):
    flag = dict_entry(spec, "async")
    if flag is None or (isinstance(flag, ast.Constant) and flag.value is False):
        return None
    name = dict_entry(spec, "name")
    if (
        isinstance(flag, ast.Constant)
        and flag.value is True
        and isinstance(name, ast.Constant)
        and isinstance(name.value, str)
    ):
        return {"kind": "STATIC", "value": name.value}
    return {"kind": "DYNAMIC"}


# deepagents' subagent types. A call such as `SubAgent(name=...)` can't set
# `"async"` (a keyword), so it is never an async subagent.
SUBAGENT_TYPES = frozenset({"SubAgent", "CompiledSubAgent", "AsyncSubAgent"})


def is_subagent_type_call(node):
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    return name in SUBAGENT_TYPES


def async_subagents(call, bindings):
    """Return (names, unreadable): async subagent names, and how many elements
    MDA could not read (the runtime rejects any of those that is async)."""
    names, unreadable = [], 0
    for kw in call.keywords:
        if kw.arg != "subagents":
            continue
        value = kw.value
        if isinstance(value, ast.Constant) and value.value is None:
            continue
        if isinstance(value, ast.Name):
            value = bindings.get(value.id)
        if not isinstance(value, (ast.List, ast.Tuple)):
            return names, 1
        for element in value.elts:
            if isinstance(element, ast.Name):
                element = bindings.get(element.id)
            if is_subagent_type_call(element):
                continue
            if not isinstance(element, ast.Dict):
                unreadable += 1
                continue
            name = async_subagent_name(element)
            if name is not None:
                names.append(name)
    return names, unreadable


def exported_factory(tree):
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    exported = functions.get("agent")
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "agent" for target in targets
        ):
            exported = functions.get(value.id) if isinstance(value, ast.Name) else (
                value if isinstance(value, ast.Lambda) else None
            )
    if exported is None:
        return None
    return exported


def factory_definition_call(function):
    if isinstance(function, ast.Lambda):
        call = function.body
        return call if isinstance(call, ast.Call) and is_define_deep_agent_call(call) else None
    if not function.body or not isinstance(function.body[-1], ast.Return):
        return None
    if not all(
        isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr))
        for node in function.body[:-1]
    ):
        return None
    call = function.body[-1].value
    if not isinstance(call, ast.Call) or not is_define_deep_agent_call(call):
        return None
    return call


result = {
    "name": {"kind": "ABSENT"},
    "model": None,
    "is_factory": False,
    "declares_disable_memory": False,
    "unsupported_keys": [],
    "async_subagents": [],
    "unreadable_subagents": 0,
}
module_bindings = literal_bindings(tree.body)

for node in ast.iter_child_nodes(tree):
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    else:
        continue
    names = {target.id for target in targets if isinstance(target, ast.Name)}
    if "agent" not in names or not isinstance(value, ast.Call):
        continue
    if is_define_deep_agent_call(value):
        result["name"] = name_keyword(value)
        result["declares_disable_memory"] = declares_disable_memory(value)
        result["unsupported_keys"] = [
            kw.arg
            for kw in value.keywords
            if kw.arg in DEFINE_DEEP_AGENT_UNSUPPORTED_KEYS
        ]
        result["async_subagents"], result["unreadable_subagents"] = async_subagents(
            value, module_bindings
        )
        # First static model wins (historical model extractor behavior).
        if result["model"] is None:
            result["model"] = model_keyword(value)
        break
    # model extraction historically accepts any CallExpression on `agent`.
    if result["model"] is None:
        result["model"] = model_keyword(value)

factory = exported_factory(tree)
if result["name"]["kind"] == "ABSENT" and factory is not None:
    call = factory_definition_call(factory)
    result["name"] = name_keyword(call) if call is not None else {"kind": "DYNAMIC"}
    if call is not None:
        body = [] if isinstance(factory, ast.Lambda) else factory.body
        bindings = {**module_bindings, **literal_bindings(body)}
        result["async_subagents"], result["unreadable_subagents"] = async_subagents(
            call, bindings
        )
    result["model"] = None
    result["is_factory"] = True

print(json.dumps(result, separators=(",", ":")))
