//! Environment variable seeding for scaffolded `.env` files.

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
/// is left empty for the user to fill in. The key `model` authenticates with is
/// written uncommented and empty, so the one line to fill in is obvious; every
/// other provider key stays commented out.
pub(super) fn env_file(langsmith_key: Option<&str>, model: &str) -> String {
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

    let model_key = match dependencies::deploy_model_api_key_requirement(model) {
        ModelApiKeyRequirement::DeploymentSecret(key) => Some(key),
        _ => None,
    };
    if let Some(key) = model_key {
        out.push_str(&format!(
            "\n# API key for your agent's model ({model}). `mda dev`, `mda chat`, and\n\
             # `mda deploy` need it; it is forwarded to your deployment.\n\
             {key}=\n\
             \n\
             # Other model providers. Uncomment the one(s) your agent switches to.\n"
        ));
    } else {
        out.push_str(
            "\n# Model provider API keys. Uncomment the provider(s) your agent uses;\n\
             # they are forwarded to your deployment so the model can authenticate.\n",
        );
    }
    for (key, provider) in PROVIDER_API_KEYS {
        if model_key != Some(*key) {
            out.push_str(&format!("# {key}=  # {provider}\n"));
        }
    }

    out
}
