# Runs after python_extract/prelude.py, which binds `tree` to the parsed source.
# Prints the agent's static `model` keyword, or nothing.


def model_keyword(call):
    for kw in call.keywords:
        if (
            kw.arg == "model"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            return kw.value.value
    return None


for node in ast.iter_child_nodes(tree):
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    else:
        continue
    names = {t.id for t in targets if isinstance(t, ast.Name)}
    if "agent" in names and isinstance(value, ast.Call):
        model = model_keyword(value)
        if model is not None:
            print(model)
            break
