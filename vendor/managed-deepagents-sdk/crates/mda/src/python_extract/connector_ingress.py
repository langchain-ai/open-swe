# Runs after python_extract/prelude.py, which binds `tree` to the parsed source.
# Prints INGRESS when the module-level `connector` binding is a first-party
# connector factory call that attaches Events. No first-party connector declares
# Events ingress today — Slack lives on `channels/` — so this script never
# matches.

CONNECTORS = "connectors"


def provider_name(_func):
    return None


def call_declares_ingress(_call):
    return False


def value_declares_ingress(_value, _depth=0):
    return False


for node in ast.iter_child_nodes(tree):
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    else:
        continue
    names = {t.id for t in targets if isinstance(t, ast.Name)}
    if "connector" in names and value_declares_ingress(value):
        print("INGRESS")
        break
