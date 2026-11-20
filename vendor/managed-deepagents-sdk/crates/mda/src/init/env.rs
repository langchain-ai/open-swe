//! Environment variable seeding for scaffolded `.env` files.

use std::collections::BTreeMap;

use crate::dependencies::{self, ModelApiKeyRequirement};

/// Commented-out model provider API keys to document in the generated `.env`.
///
/// These are forwarded to your deployment as secrets, so the agent's model can
/// authenticate. Users uncomment the providers they actually use.
pub(super) const PROVIDER_API_KEYS: &[(&str, &str)] = &[
    ("OPENAI_API_KEY", "OpenAI"),
    ("ANTHROPIC_API_KEY", "Anthropic"),
    ("GOOGLE_API_KEY", "Google Gemini"),
    ("GROQ_API_KEY", "Groq"),
    ("MISTRAL_API_KEY", "Mistral"),
];

/// Build the contents of the scaffolded `.env` file.
///
/// When `langsmith_key` is `Some`, the resolved key is written verbatim so a
/// key already present in the shell environment is reused; otherwise the line
/// is left empty for the user to fill in. The keys `model` and
/// `subagent_models` authenticate with are written uncommented and empty, so
/// the lines to fill in are obvious; every other provider key stays commented
/// out.
pub(super) fn env_file(
    langsmith_key: Option<&str>,
    model: &str,
    subagent_models: &[&str],
) -> String {
    let mut out = String::from(
        "# Environment variables for your Managed Deep Agent.\n\
         #\n\
         # `mda` loads this file on deploy and forwards non-reserved variables to\n\
         # your deployment as secrets. Do NOT commit real secrets to version control.\n\
         \n\
         # LangSmith API key — required to deploy. Create one under\n\
         # Settings > API Keys in LangSmith (https://smith.langchain.com).\n",
    );
    match langsmith_key {
        Some(key) => out.push_str(&format!("LANGSMITH_API_KEY={key}\n")),
        None => out.push_str("LANGSMITH_API_KEY=\n"),
    }

    let model_key = deployment_key(model);
    let mut written: Vec<&str> = model_key.into_iter().collect();
    // One line per key, under the first subagent model that needs it.
    let mut subagent_keys = Vec::new();
    for subagent_model in subagent_models {
        if let Some(key) = deployment_key(subagent_model) {
            if !written.contains(&key) {
                written.push(key);
                subagent_keys.push((key, *subagent_model));
            }
        }
    }
    if let Some(key) = model_key {
        out.push_str(&format!(
            "\n# API key for your agent's model ({model}). `mda dev`, `mda chat`, and\n\
             # `mda deploy` need it; it is forwarded to your deployment.\n\
             {key}=\n"
        ));
    }
    for (key, subagent_model) in &subagent_keys {
        out.push_str(&format!(
            "\n# API key for a subagent's model ({subagent_model}). It is forwarded to\n\
             # your deployment.\n\
             {key}=\n"
        ));
    }
    if written.is_empty() {
        out.push_str(
            "\n# Model provider API keys. Uncomment the provider(s) your agent uses;\n\
             # they are forwarded to your deployment so the model can authenticate.\n",
        );
    } else {
        out.push_str("\n# Other model providers. Uncomment the one(s) your agent switches to.\n");
    }
    for (key, provider) in PROVIDER_API_KEYS {
        if !written.contains(key) {
            out.push_str(&format!("# {key}=  # {provider}\n"));
        }
    }

    out
}

/// The key `model` needs as a deployment secret, if any.
fn deployment_key(model: &str) -> Option<&'static str> {
    match dependencies::deploy_model_api_key_requirement(model) {
        ModelApiKeyRequirement::DeploymentSecret(key) => Some(key),
        _ => None,
    }
}

/// Prefix of the `.env` keys that hold sandbox env vars. `mda deploy` sends
/// `.env` as the deployment's own environment, so an unprefixed `PATH` or
/// `DATABASE_URI` would be dropped as reserved (and the server's value sent
/// to the sandbox), and a sandbox-only `HOME` would change the server's.
const SANDBOX_ENV_PREFIX: &str = "SANDBOX_ENV_";

/// The `.env` key holding the sandbox env var `name`.
pub(crate) fn sandbox_env_key(name: &str) -> String {
    format!("{SANDBOX_ENV_PREFIX}{name}")
}

/// The `.env` lines for the env vars `sandbox/` reads, or nothing. Values are
/// written here rather than into `sandbox/`, which gets committed.
pub(super) fn sandbox_env_vars(vars: &BTreeMap<String, String>) -> String {
    if vars.is_empty() {
        return String::new();
    }
    let mut out = String::from(
        "\n# Set for every command in the sandbox under the name after `SANDBOX_ENV_`.\n",
    );
    for (name, value) in vars {
        out.push_str(&format!(
            "{}={}\n",
            sandbox_env_key(name),
            dotenv_value(value)
        ));
    }
    out
}

/// `value` quoted for `.env`: single quotes keep it literal; a value holding a
/// single quote or a line break uses double quotes with escapes instead.
fn dotenv_value(value: &str) -> String {
    if !value.contains(['\'', '\n', '\r']) {
        return format!("'{value}'");
    }
    let escaped = value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        // dotenvy fills in `$NAME` inside double quotes.
        .replace('$', "\\$")
        .replace('\n', "\\n")
        .replace('\r', "\\r");
    format!("\"{escaped}\"")
}

#[cfg(test)]
mod sandbox_env_tests {
    use std::collections::BTreeMap;

    use super::sandbox_env_vars;

    #[test]
    fn sandbox_env_vars_read_back_unchanged() {
        let vars = BTreeMap::from([
            ("PLAIN".to_string(), "debug".to_string()),
            ("SPACES".to_string(), "a b #c $d".to_string()),
            ("QUOTE".to_string(), "it's \"x\"\\y".to_string()),
            ("LINES".to_string(), "one\ntwo".to_string()),
            ("DOLLAR".to_string(), "it's $HOME".to_string()),
        ]);
        let text = sandbox_env_vars(&vars);
        let parsed: BTreeMap<String, String> = dotenvy::from_read_iter(text.as_bytes())
            .map(Result::unwrap)
            .collect();
        let expected: BTreeMap<String, String> = vars
            .iter()
            .map(|(name, value)| (format!("SANDBOX_ENV_{name}"), value.clone()))
            .collect();
        assert_eq!(parsed, expected);
        assert_eq!(sandbox_env_vars(&BTreeMap::new()), "");
    }
}
