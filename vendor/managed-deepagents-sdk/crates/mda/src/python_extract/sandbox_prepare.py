# Runs after python_extract/prelude.py, which binds `tree` to the parsed source.
# Prints "prepare" when the `sandbox = define_sandbox(...)` declaration passes a
# `prepare` callback. Only presence matters: the callback runs at bake time.
for node in ast.iter_child_nodes(tree):
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    else:
        continue
    if "sandbox" not in {target.id for target in targets if isinstance(target, ast.Name)}:
        continue
    if isinstance(value, ast.Call) and any(kw.arg == "prepare" for kw in value.keywords):
        print("prepare")
    break
