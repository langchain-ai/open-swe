//! Environment variable seeding for scaffolded `.env` files.

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
/// is left empty for the user to fill in. Provider keys are always commented
/// out.
pub(super) fn env_file(langsmith_key: Option<&str>) -> String {
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

    out.push_str(
        "\n# Model provider API keys. Uncomment the provider(s) your agent uses;\n\
         # they are forwarded to your deployment so the model can authenticate.\n",
    );
    for (key, provider) in PROVIDER_API_KEYS {
        out.push_str(&format!("# {key}=  # {provider}\n"));
    }

    out
}
