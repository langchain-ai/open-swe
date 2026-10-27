//! Shared static extraction of object-literal configs from authored modules.
//!
//! Schedules need deploy-time JSON from TypeScript/Python source. Only literals,
//! arrays, objects, and references to top-level literal constants are accepted
//! so compile stays predictable and side-effect free. Connector requirements
//! come from live plugins (`requirementsFromConnectors` /
//! `requirements_from_connectors`), not this extractor.

use std::collections::HashMap;
use std::path::Path;

use anyhow::{anyhow, bail, Context, Result};
use oxc::allocator::Allocator;
use oxc::ast::ast::{
    Argument, ArrayExpression, ArrayExpressionElement, Expression, ObjectExpression,
    ObjectPropertyKind, Statement,
};
use serde_json::{Map, Number, Value};

use crate::child;
use crate::error::child_failed;
use crate::toolchain;
use crate::typescript::{parse_typescript, statement_variable_declaration};

/// Extract the object-literal argument of `exportName = helper(... { ... })`.
pub(super) fn extract_typescript_object_config(
    source: &str,
    rel: &Path,
    export_name: &str,
    helper_name: &str,
) -> Result<Value> {
    extract_typescript_config(source, rel, export_name, helper_name, None)
}

/// Extract only selected keys from an authored object literal.
///
/// Unselected values may be dynamic because callers do not need them at
/// compile time. A call without arguments yields an empty object.
pub(super) fn extract_typescript_selected_object_config(
    source: &str,
    rel: &Path,
    export_name: &str,
    helper_name: &str,
    selected_keys: &[&str],
) -> Result<Value> {
    extract_typescript_config(source, rel, export_name, helper_name, Some(selected_keys))
}

fn extract_typescript_config(
    source: &str,
    rel: &Path,
    export_name: &str,
    helper_name: &str,
    selected_keys: Option<&[&str]>,
) -> Result<Value> {
    let allocator = Allocator::default();
    let file_name = rel.to_string_lossy();
    let parsed = parse_typescript(&allocator, source, &file_name)?;

    let bindings = collect_json_bindings(&parsed.program.body, export_name);
    for statement in &parsed.program.body {
        let Some(declaration) = statement_variable_declaration(statement) else {
            continue;
        };
        for declarator in &declaration.declarations {
            let is_export = declarator
                .id
                .get_identifier_name()
                .is_some_and(|name| name == export_name);
            if !is_export {
                continue;
            }
            let Some(Expression::CallExpression(call)) = declarator.init.as_ref() else {
                bail!(
                    "{} must export `{export_name} = {helper_name}(...)`",
                    rel.display()
                );
            };
            for argument in &call.arguments {
                if let Argument::ObjectExpression(object) = argument {
                    let extracted = match selected_keys {
                        Some(keys) => json_selected_object_expression(object, &bindings, keys),
                        None => json_object_expression(object, &bindings),
                    };
                    return extracted.with_context(|| {
                        format!(
                            "{export_name} {} must be statically serializable",
                            rel.display()
                        )
                    });
                }
            }
            if selected_keys.is_some() && call.arguments.is_empty() {
                return Ok(Value::Object(Map::new()));
            }
            bail!(
                "{} {helper_name}(...) must receive an object literal",
                rel.display()
            );
        }
    }

    bail!(
        "{} must export a named `{export_name}` declaration",
        rel.display()
    )
}

/// Extract keyword args of `export_name = helper_name(...)` via a Python AST helper.
pub(super) fn extract_python_keyword_config(
    source: &str,
    rel: &Path,
    export_name: &str,
    helper_name: &str,
) -> Result<Value> {
    extract_python_config(source, rel, export_name, helper_name, None)
}

/// Extract only selected keyword arguments from an authored Python call.
///
/// Dynamic values on unrelated runtime options are deliberately ignored.
pub(super) fn extract_python_selected_keyword_config(
    source: &str,
    rel: &Path,
    export_name: &str,
    helper_name: &str,
    selected_keys: &[&str],
) -> Result<Value> {
    extract_python_config(source, rel, export_name, helper_name, Some(selected_keys))
}

fn extract_python_config(
    source: &str,
    rel: &Path,
    export_name: &str,
    helper_name: &str,
    selected_keys: Option<&[&str]>,
) -> Result<Value> {
    let selected_json = selected_keys
        .map(serde_json::to_string)
        .transpose()
        .context("failed to serialize selected static config keys")?
        .unwrap_or_default();
    let output = child::output_with_stdin(
        toolchain::python3()
            .arg("-c")
            .arg(PYTHON_KEYWORD_EXTRACTOR)
            .arg(export_name)
            .arg(selected_json),
        source.as_bytes(),
    )
    .with_context(|| {
        format!(
            "failed to run {} for static config extraction",
            toolchain::python3_program()
        )
    })?;
    if !output.status.success() {
        return Err(child_failed(
            &output,
            format!("failed to parse {}", rel.display()),
        ));
    }

    let stdout = String::from_utf8(output.stdout).context("static extractor returned UTF-8")?;
    let value: Value = serde_json::from_str(stdout.trim()).with_context(|| {
        format!(
            "{} must declare a statically serializable `{export_name} = {helper_name}(...)`",
            rel.display()
        )
    })?;
    if let Some(error) = value.get("syntax_error") {
        let line = error.get("line").and_then(Value::as_u64).unwrap_or(1);
        let column = error.get("column").and_then(Value::as_u64).unwrap_or(1);
        let message = error
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("invalid syntax");
        bail!(
            "failed to parse {}:{line}:{column}: {message}",
            rel.display()
        );
    }
    if let Some(error) = value.get("error").and_then(Value::as_str) {
        bail!("{} {export_name} is not static: {error}", rel.display());
    }
    value.get("config").cloned().ok_or_else(|| {
        anyhow!(
            "{} must declare a module-level `{export_name}` value",
            rel.display()
        )
    })
}

fn collect_json_bindings(body: &[Statement<'_>], skip_export: &str) -> HashMap<String, Value> {
    let mut bindings = HashMap::new();
    for statement in body {
        let Some(declaration) = statement_variable_declaration(statement) else {
            continue;
        };
        for declarator in &declaration.declarations {
            let Some(name) = declarator.id.get_identifier_name() else {
                continue;
            };
            if name == skip_export {
                continue;
            }
            if let Some(init) = declarator.init.as_ref() {
                if let Ok(value) = json_expression(init, &bindings) {
                    bindings.insert(name.to_string(), value);
                }
            }
        }
    }
    bindings
}

fn json_expression(
    expression: &Expression<'_>,
    bindings: &HashMap<String, Value>,
) -> Result<Value> {
    match expression {
        Expression::StringLiteral(literal) => Ok(Value::String(literal.value.to_string())),
        Expression::TemplateLiteral(literal) => literal
            .single_quasi()
            .map(|value| Value::String(value.to_string()))
            .ok_or_else(|| anyhow!("template literals with substitutions are not static")),
        Expression::BooleanLiteral(literal) => Ok(Value::Bool(literal.value)),
        Expression::NullLiteral(_) => Ok(Value::Null),
        Expression::NumericLiteral(literal) => Number::from_f64(literal.value)
            .map(Value::Number)
            .ok_or_else(|| anyhow!("invalid numeric literal")),
        Expression::Identifier(reference) => bindings
            .get(&*reference.name)
            .cloned()
            .ok_or_else(|| anyhow!("unresolved identifier {}", reference.name)),
        Expression::ObjectExpression(object) => json_object_expression(object, bindings),
        Expression::ArrayExpression(array) => json_array_expression(array, bindings),
        _ => bail!("dynamic expressions are not supported"),
    }
}

fn json_object_expression(
    object: &ObjectExpression<'_>,
    bindings: &HashMap<String, Value>,
) -> Result<Value> {
    let mut map = Map::new();
    for property in &object.properties {
        let ObjectPropertyKind::ObjectProperty(property) = property else {
            bail!("spread properties are not supported");
        };
        let key = property
            .key
            .static_name()
            .map(|name| name.into_owned())
            .ok_or_else(|| anyhow!("computed property keys are not supported"))?;
        let value = json_expression(&property.value, bindings)
            .with_context(|| format!("property `{key}` is not static"))?;
        map.insert(key, value);
    }
    Ok(Value::Object(map))
}

fn json_selected_object_expression(
    object: &ObjectExpression<'_>,
    bindings: &HashMap<String, Value>,
    selected_keys: &[&str],
) -> Result<Value> {
    let mut map = Map::new();
    for property in &object.properties {
        let ObjectPropertyKind::ObjectProperty(property) = property else {
            bail!("spread properties may contain deploy-time options");
        };
        let key = property
            .key
            .static_name()
            .map(|name| name.into_owned())
            .ok_or_else(|| anyhow!("computed property keys may contain deploy-time options"))?;
        if !selected_keys.contains(&key.as_str()) {
            continue;
        }
        let value = json_expression(&property.value, bindings)
            .with_context(|| format!("property `{key}` is not static"))?;
        map.insert(key, value);
    }
    Ok(Value::Object(map))
}

fn json_array_expression(
    array: &ArrayExpression<'_>,
    bindings: &HashMap<String, Value>,
) -> Result<Value> {
    let mut values = Vec::with_capacity(array.elements.len());
    for element in &array.elements {
        values.push(json_array_element(element, bindings)?);
    }
    Ok(Value::Array(values))
}

fn json_array_element(
    element: &ArrayExpressionElement<'_>,
    bindings: &HashMap<String, Value>,
) -> Result<Value> {
    match element {
        ArrayExpressionElement::SpreadElement(_) => bail!("spread elements are not supported"),
        ArrayExpressionElement::Elision(_) => bail!("array holes are not supported"),
        // Every other variant inherits an `Expression` variant.
        _ => element
            .as_expression()
            .ok_or_else(|| anyhow!("dynamic array elements are not supported"))
            .and_then(|expression| json_expression(expression, bindings)),
    }
}

const PYTHON_KEYWORD_EXTRACTOR: &str = r#"
import ast, json, sys

export_name = sys.argv[1]
selected = set(json.loads(sys.argv[2])) if len(sys.argv) > 2 and sys.argv[2] else None

try:
    tree = ast.parse(sys.stdin.read())
except SyntaxError as exc:
    # Kept separate from "error": a file that does not parse says nothing about
    # whether the config it declares is static.
    print(json.dumps({"syntax_error": {
        "line": exc.lineno or 1,
        "column": exc.offset or 1,
        "message": exc.msg,
    }}))
    sys.exit(0)

bindings = {}


def static_value(node):
    if isinstance(node, ast.Name) and node.id in bindings:
        return bindings[node.id]
    return ast.literal_eval(node)


def target_names(node):
    if isinstance(node, ast.Assign):
        return [target.id for target in node.targets if isinstance(target, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


for node in ast.iter_child_nodes(tree):
    names = target_names(node)
    if not names or export_name in names:
        continue
    value = getattr(node, "value", None)
    if value is None:
        continue
    try:
        literal = static_value(value)
    except Exception:
        continue
    for name in names:
        bindings[name] = literal


for node in ast.iter_child_nodes(tree):
    if export_name not in target_names(node):
        continue
    call = getattr(node, "value", None)
    if not isinstance(call, ast.Call):
        print(json.dumps({"error": f"{export_name} must be assigned from a call"}))
        sys.exit(0)
    config = {}
    try:
        for keyword in call.keywords:
            if keyword.arg is None:
                raise ValueError("**kwargs are not supported")
            if selected is not None and keyword.arg not in selected:
                continue
            config[keyword.arg] = static_value(keyword.value)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(0)
    print(json.dumps({"config": config}))
    sys.exit(0)

print(json.dumps({}))
"#;

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn extract(list: &str) -> Result<Value> {
        let source = format!(
            "const X = 'x';\nexport const schedule = defineSchedule({{ list: {list} }});\n"
        );
        extract_typescript_object_config(
            &source,
            Path::new("schedule.ts"),
            "schedule",
            "defineSchedule",
        )
    }

    #[test]
    fn array_elements_extract_like_expressions() {
        let value = extract("['s', `t`, true, null, 1, X, { k: 1 }, [2]]").unwrap();

        assert_eq!(
            value,
            json!({ "list": ["s", "t", true, null, 1.0, "x", { "k": 1.0 }, [2.0]] })
        );
    }

    #[test]
    fn unsupported_array_elements_fail() {
        for (list, message) in [
            ("[...xs]", "spread elements are not supported"),
            ("[1, , 2]", "array holes are not supported"),
            ("[f()]", "dynamic expressions are not supported"),
            ("[Y]", "unresolved identifier Y"),
        ] {
            let error = format!("{:#}", extract(list).unwrap_err());
            assert!(error.contains(message), "{list}: {error}");
        }
    }
}
