# Runs after python_extract/prelude.py, which binds `tree` to the parsed source.
# Prints one JSON object with graph identity and static build metadata for the
# agent binding. `declares_disable_memory` reports the retired `disable_memory`
# keyword at all (any value, static or not), which the build turns into a
# migration error.
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


def factory_definition_name(function):
    if isinstance(function, ast.Lambda):
        call = function.body
        return name_keyword(call) if isinstance(call, ast.Call) and is_define_deep_agent_call(call) else {"kind": "DYNAMIC"}
    if not function.body or not isinstance(function.body[-1], ast.Return):
        return {"kind": "DYNAMIC"}
    if not all(
        isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr))
        for node in function.body[:-1]
    ):
        return {"kind": "DYNAMIC"}
    call = function.body[-1].value
    if not isinstance(call, ast.Call):
        return {"kind": "DYNAMIC"}
    if not is_define_deep_agent_call(call):
        return {"kind": "DYNAMIC"}
    return name_keyword(call)


result = {
    "name": {"kind": "ABSENT"},
    "model": None,
    "is_factory": False,
    "declares_disable_memory": False,
    "unsupported_keys": [],
}

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
        # First static model wins (historical model extractor behavior).
        if result["model"] is None:
            result["model"] = model_keyword(value)
        break
    # model extraction historically accepts any CallExpression on `agent`.
    if result["model"] is None:
        result["model"] = model_keyword(value)

factory = exported_factory(tree)
if result["name"]["kind"] == "ABSENT" and factory is not None:
    result["name"] = factory_definition_name(factory)
    result["model"] = None
    result["is_factory"] = True

print(json.dumps(result, separators=(",", ":")))
