//! Central registry of the dependencies the CLI manages.
//!
//! An author's generated manifest only ever lists the single dependency they
//! actually reason about: `managed-deepagents`, pinned to the installed runtime
//! package version so the authoring SDK and the CLI that compiles it always move
//! in lockstep.
//! Everything else the managed runtime needs is *inferred* and injected into the
//! build manifest at compile time, never written into the author's source:
//!
//! - `deepagents` and the LangChain / LangGraph stack ([`managed_dependencies`]),
//!   and
//! - the chat-model provider integration package implied by the agent's
//!   configured `model` ([`provider_dependency_for_model`]), ported from
//!   LangChain's `initChatModel` provider inference.
//!
//! # TypeScript LangChain dependency ranges
//!
//! The managed LangGraph runtime image validates the resolved
//! `@langchain/core`, `@langchain/langgraph`, and
//! `@langchain/langgraph-checkpoint` versions against the API server's
//! dependencies. The graph also loads `@langchain/langgraph` from the **deploy
//! `npm ci` tree**, not from the Host image, so Topic / `__pregel_tasks`
//! checkpoint shape must match what Host's Python checkpointer accepts
//! (`@langchain/langgraph@1.4.9+` emits a flat values list). Use caret ranges
//! from known-compatible floors for the TypeScript LangChain stack, then record
//! each compilation's concrete resolution in the generated lockfile. Bump the
//! floors together when the managed image advances.

use crate::project::Language;
use crate::version;

/// A single managed dependency and the version range written into a manifest.
#[derive(Debug, Clone, Copy)]
pub struct Dependency {
    /// Package name as published to npm / PyPI.
    pub name: &'static str,
    /// Version as it should appear in the manifest. May be an exact pin
    /// (`1.4.9`) or carry a range operator (`^0.0.2`, `>=0.7.3`).
    pub version: &'static str,
}

/// User-facing dependency seeded into newly scaffolded manifests.
///
/// Intentionally just the single package an author works with directly; the rest
/// of the stack — including `deepagents` — is injected into the build by
/// [`managed_dependencies`].
pub const USER_RUNTIME_PACKAGE: &str = "managed-deepagents";

/// npm dependencies the managed runtime requires, injected into the build's
/// `package.json` at compile time rather than written into the author's source.
const TYPESCRIPT_MANAGED: &[Dependency] = &[
    Dependency {
        name: "@langchain/core",
        version: "^1.2.17",
    },
    Dependency {
        name: "@langchain/langgraph",
        version: "^1.4.18",
    },
    Dependency {
        name: "@langchain/langgraph-checkpoint",
        version: "^1.1.3",
    },
    Dependency {
        name: "@langchain/langgraph-sdk",
        version: "^1.12.0",
    },
    Dependency {
        name: "deepagents",
        version: "^1.15.1",
    },
    // Serves MDA managed HTTP routes (channels / connectors) from the
    // generated `_mda_http.ts` module.
    Dependency {
        name: "hono",
        version: "^4.12.27",
    },
    // Verifies `validated_token` ingress: JWKS local verification in the
    // generated custom-auth handler.
    Dependency {
        name: "jose",
        version: "^6.2.3",
    },
    Dependency {
        name: "langchain",
        version: "^1.5.12",
    },
    // The built-in MCP connector ships with the managed runtime, so its adapter
    // rides along unconditionally rather than being inferred from `connectors/`.
    TYPESCRIPT_CONNECTORS,
    // LangSmith sandbox client (`langsmith/sandbox`) used by the managed
    // sandbox manager at runtime.
    Dependency {
        name: "langsmith",
        version: "^0.8.9",
    },
    // Deterministic UUIDv5 thread IDs for Trigger-backed channels.
    Dependency {
        name: "uuid",
        version: "^13.0.2",
    },
    Dependency {
        name: "zod",
        version: "^4.4.3",
    },
];

/// PyPI dependencies the managed runtime requires, injected into the build's
/// `pyproject.toml` at compile time.
const PYTHON_MANAGED: &[Dependency] = &[
    Dependency {
        name: "deepagents",
        version: ">=0.7.30,<1",
    },
    Dependency {
        name: "langchain[mcp]",
        version: ">=1.4.6,<2",
    },
    Dependency {
        name: "langgraph",
        version: ">=1.2.15,<2",
    },
    // 1.1.1 lets an invalid answer to an interrupt inside a tool (such as a
    // subagent) fail the resume instead of reaching the model as a tool error.
    // It needs langgraph 1.2.15, which marks those errors.
    Dependency {
        name: "langgraph-prebuilt",
        version: ">=1.1.1,<2",
    },
    Dependency {
        name: "langgraph-checkpoint",
        version: ">=4.2.0",
    },
    // Provides `langgraph_sdk.Auth`, which the generated `_mda_auth.py` uses to
    // build the managed custom-auth handler. Pulled transitively by `langgraph`,
    // but injected explicitly so identity-enabled deploys have it deterministically
    // (mirrors the TypeScript `@langchain/langgraph-sdk` injection).
    Dependency {
        name: "langgraph-sdk",
        version: ">=0.4.6,<0.5",
    },
    // Verifies `validated_token` ingress: JWKS local verification and MDA-signed
    // OAuth `state` JWTs in the generated custom-auth handler. `[crypto]` pulls the
    // `cryptography` backend needed for RS256/ES256 signatures.
    Dependency {
        name: "pyjwt[crypto]",
        version: ">=2.8.0",
    },
    // The built-in MCP connector ships with the managed runtime, so its adapter
    // rides along unconditionally rather than being inferred from `connectors/`.
    PYTHON_CONNECTORS,
    Dependency {
        name: "mcp",
        version: ">=2.2.0,<3",
    },
    // LangSmith SDK used by deepagents' LangSmith sandbox backend at runtime.
    Dependency {
        name: "langsmith",
        version: ">=0.10.17",
    },
];

/// PyPI package that drives a LangSmith browser from the deployment, for
/// projects that declare one in `browser.py`. Pinned: the browser
/// loads the extension from the same Stagehand release (`browser.py`'s
/// `STAGEHAND_VERSION`).
pub const PYTHON_BROWSER: Dependency = Dependency {
    name: "stagehand",
    version: "==4.3.0",
};

/// PyPI package behind that browser's `run`, `snapshot` and `screenshot`
/// tools: Stagehand's facade. Pinned like the package's typing group: it is a
/// first release, and `browser.py` is written against how it closes and saves.
pub const PYTHON_BROWSER_TOOLS: Dependency = Dependency {
    name: "stagehand-middleware",
    version: "==0.1.0",
};

/// npm package that drives a LangSmith browser from the deployment, for
/// projects that declare one in `browser.ts`. Pinned to the release that
/// `managed-deepagents` lists as a peer dependency, so a deployment
/// runs the Stagehand the package is tested against.
pub const TYPESCRIPT_BROWSER: Dependency = Dependency {
    name: "@browserbasehq/stagehand",
    version: "4.3.0",
};

/// npm package behind that browser's `run`, `snapshot` and `screenshot`
/// tools: Stagehand's facade, the npm twin of [`PYTHON_BROWSER_TOOLS`]. Pinned
/// to the release that `managed-deepagents` lists as a peer dependency.
pub const TYPESCRIPT_BROWSER_TOOLS: Dependency = Dependency {
    name: "stagehand-middleware",
    version: "0.1.1",
};

/// npm package that provides the local LangGraph in-memory dev server.
///
/// The caret range keeps local development on compatible LangGraph CLI updates;
/// the compile-generated lockfile records the concrete resolved version.
pub const TYPESCRIPT_DEV_SERVER_CLI: Dependency = Dependency {
    name: "@langchain/langgraph-cli",
    version: "^1.5.1",
};

/// Python package that provides the local LangGraph in-memory dev server and
/// the `langgraph build` used by `mda deploy --push-to`.
pub const PYTHON_DEV_SERVER_CLI: Dependency = Dependency {
    name: "langgraph-cli[inmem]",
    version: ">=0.4.36",
};

/// Python LangGraph API server behind `mda dev`. 0.16 keeps each interrupt's
/// `response_schema` in thread state, so a conversation reopened with
/// `mda chat --thread` still knows which decisions a tool approval allows.
/// Deploys pin their own image (`LANGGRAPH_API_VERSION`).
pub const PYTHON_DEV_SERVER_API: Dependency = Dependency {
    name: "langgraph-api",
    version: ">=0.16.0",
};

/// npm dependencies only needed by `mda dev`.
const TYPESCRIPT_LOCAL_DEV: &[Dependency] = &[TYPESCRIPT_DEV_SERVER_CLI];

/// PyPI dependencies only needed by `mda dev`.
const PYTHON_LOCAL_DEV: &[Dependency] = &[PYTHON_DEV_SERVER_CLI, PYTHON_DEV_SERVER_API];

/// The managed LangChain / LangGraph dependencies for `language`, injected into
/// the build manifest at compile time.
pub fn managed_dependencies(language: Language) -> &'static [Dependency] {
    match language {
        Language::TypeScript => TYPESCRIPT_MANAGED,
        Language::Python => PYTHON_MANAGED,
    }
}

/// Extra dependencies for projects that declare a `browser`.
pub fn browser_dependencies(language: Language) -> &'static [Dependency] {
    match language {
        Language::TypeScript => &[TYPESCRIPT_BROWSER, TYPESCRIPT_BROWSER_TOOLS],
        Language::Python => &[PYTHON_BROWSER, PYTHON_BROWSER_TOOLS],
    }
}

/// Extra dependencies needed only for local `mda dev`.
pub fn local_dev_dependencies(language: Language) -> &'static [Dependency] {
    match language {
        Language::TypeScript => TYPESCRIPT_LOCAL_DEV,
        Language::Python => PYTHON_LOCAL_DEV,
    }
}

/// npm package backing the built-in MCP connector. Its peer ranges accept the
/// managed Core and LangGraph ranges. Part of the always-injected managed stack
/// — the CLI no longer infers connector dependencies from `connectors/`.
const TYPESCRIPT_CONNECTORS: Dependency = Dependency {
    name: "@langchain/mcp-adapters",
    version: "^2.0.0",
};

/// PyPI package backing the built-in MCP connector, injected with a `>=` floor
/// to match the Python manifest convention. Part of the always-injected managed
/// stack — the CLI no longer infers connector dependencies from `connectors/`.
const PYTHON_CONNECTORS: Dependency = Dependency {
    name: "fastmcp",
    version: ">=4.0.10,<5",
};

/// A chat-model provider and the integration packages that back it in each
/// ecosystem.
///
/// Ported from LangChain's `MODEL_PROVIDER_CONFIG` (`initChatModel`), mapping a
/// provider key to the package that exports its chat-model class.
struct ModelProvider {
    /// Accepted provider keys (model-string prefixes). Covers both the JS-style
    /// (`google-vertexai`) and Python-style (`google_vertexai`) spellings, plus
    /// aliases, so a model string authored in either ecosystem resolves.
    keys: &'static [&'static str],
    /// npm integration package + pinned version for a TypeScript build.
    npm: Dependency,
    /// PyPI integration package + floor version for a Python build.
    pypi: Dependency,
    /// Environment variable that must carry this provider's API key at runtime,
    /// or `None` when the provider authenticates by some other means (e.g.
    /// Bedrock's AWS credentials, Vertex AI's application-default credentials)
    /// or needs no key at all (a local Ollama server). Used by
    /// [`required_api_key_env_for_model`] to pre-flight credentials before a
    /// deploy.
    api_key_env: Option<&'static str>,
}

/// Provider → integration package registry.
///
/// Versions track the current published releases. npm packages use caret ranges
/// and compilation records the concrete resolution in its lockfile; PyPI
/// packages use `>=` floors (matching the Python manifest convention).
const MODEL_PROVIDERS: &[ModelProvider] = &[
    ModelProvider {
        keys: &["openai", "azure_openai", "azure-openai"],
        npm: Dependency {
            name: "@langchain/openai",
            version: "^1.5.5",
        },
        pypi: Dependency {
            name: "langchain-openai",
            version: ">=1.4.2",
        },
        // Azure deployments authenticate with AZURE_OPENAI_API_KEY, but they
        // are always addressed via the explicit `azure_openai:` prefix; the
        // bare/`openai:` path that this covers is the OpenAI platform key.
        api_key_env: Some("OPENAI_API_KEY"),
    },
    ModelProvider {
        keys: &["langsmith"],
        npm: Dependency {
            name: "@langchain/openai",
            version: "^1.5.5",
        },
        pypi: Dependency {
            name: "langchain-openai",
            version: ">=1.4.2",
        },
        api_key_env: None,
    },
    ModelProvider {
        keys: &["anthropic"],
        npm: Dependency {
            name: "@langchain/anthropic",
            version: "^1.5.1",
        },
        pypi: Dependency {
            name: "langchain-anthropic",
            version: ">=1.5.4",
        },
        api_key_env: Some("ANTHROPIC_API_KEY"),
    },
    ModelProvider {
        keys: &["cohere"],
        npm: Dependency {
            name: "@langchain/cohere",
            version: "^1.1.0",
        },
        pypi: Dependency {
            name: "langchain-cohere",
            version: ">=0.6.0",
        },
        api_key_env: Some("COHERE_API_KEY"),
    },
    ModelProvider {
        // Google's Vertex AI surface. `google` and the Python-only
        // `google_anthropic_vertex` resolve here too, matching `initChatModel`.
        keys: &[
            "google-vertexai",
            "google_vertexai",
            "google",
            "google-vertexai-web",
            "google_anthropic_vertex",
        ],
        npm: Dependency {
            name: "@langchain/google-vertexai",
            version: "^2.2.0",
        },
        pypi: Dependency {
            name: "langchain-google-vertexai",
            version: ">=3.2.4",
        },
        // Vertex AI authenticates with Google application-default credentials,
        // not a single API-key env var, so there is nothing to pre-flight.
        api_key_env: None,
    },
    ModelProvider {
        keys: &["google-genai", "google_genai"],
        npm: Dependency {
            name: "@langchain/google-genai",
            version: "^2.2.0",
        },
        pypi: Dependency {
            name: "langchain-google-genai",
            version: ">=4.3.2",
        },
        api_key_env: Some("GOOGLE_API_KEY"),
    },
    ModelProvider {
        keys: &["ollama"],
        npm: Dependency {
            name: "@langchain/ollama",
            version: "^1.3.0",
        },
        pypi: Dependency {
            name: "langchain-ollama",
            version: ">=1.1.0",
        },
        // A local Ollama server needs no API key.
        api_key_env: None,
    },
    ModelProvider {
        keys: &["mistralai", "mistral"],
        npm: Dependency {
            name: "@langchain/mistralai",
            version: "^1.2.0",
        },
        pypi: Dependency {
            name: "langchain-mistralai",
            version: ">=1.1.6",
        },
        api_key_env: Some("MISTRAL_API_KEY"),
    },
    ModelProvider {
        keys: &["groq"],
        npm: Dependency {
            name: "@langchain/groq",
            version: "^1.3.1",
        },
        pypi: Dependency {
            name: "langchain-groq",
            version: ">=1.1.3",
        },
        api_key_env: Some("GROQ_API_KEY"),
    },
    ModelProvider {
        keys: &["bedrock", "aws", "bedrock_converse"],
        npm: Dependency {
            name: "@langchain/aws",
            version: "^1.4.2",
        },
        pypi: Dependency {
            name: "langchain-aws",
            version: ">=1.7.0",
        },
        // Bedrock authenticates with AWS credentials (access key/secret/region),
        // not a single API-key env var, so there is nothing to pre-flight.
        api_key_env: None,
    },
    ModelProvider {
        keys: &["deepseek"],
        npm: Dependency {
            name: "@langchain/deepseek",
            version: "^1.1.3",
        },
        pypi: Dependency {
            name: "langchain-deepseek",
            version: ">=1.1.0",
        },
        api_key_env: Some("DEEPSEEK_API_KEY"),
    },
    ModelProvider {
        keys: &["xai"],
        npm: Dependency {
            name: "@langchain/xai",
            version: "^1.4.3",
        },
        pypi: Dependency {
            name: "langchain-xai",
            version: ">=1.3.0",
        },
        api_key_env: Some("XAI_API_KEY"),
    },
    ModelProvider {
        keys: &["perplexity"],
        npm: Dependency {
            name: "@langchain/perplexity",
            version: "^0.3.0",
        },
        pypi: Dependency {
            name: "langchain-perplexity",
            version: ">=1.4.0",
        },
        api_key_env: Some("PPLX_API_KEY"),
    },
    ModelProvider {
        keys: &["fireworks"],
        npm: Dependency {
            name: "@langchain/fireworks",
            version: "^0.2.3",
        },
        pypi: Dependency {
            name: "langchain-fireworks",
            version: ">=1.5.2",
        },
        api_key_env: Some("FIREWORKS_API_KEY"),
    },
    ModelProvider {
        keys: &["together"],
        npm: Dependency {
            name: "@langchain/together-ai",
            version: "^0.2.3",
        },
        pypi: Dependency {
            name: "langchain-together",
            version: ">=0.4.0",
        },
        api_key_env: Some("TOGETHER_AI_API_KEY"),
    },
];

/// Resolve the chat-model provider integration package implied by `model` for
/// `language`, or `None` when no provider can be determined.
///
/// The provider is taken from an explicit `provider:model` prefix when it names
/// a known provider, otherwise inferred from the bare model name (e.g. `gpt-*`
/// → OpenAI). When neither yields a known provider — including dynamically
/// constructed models the CLI cannot read — nothing is injected and the author
/// remains responsible for the integration package.
pub fn provider_dependency_for_model(model: &str, language: Language) -> Option<Dependency> {
    let provider = provider_for_model(model)?;
    Some(match language {
        Language::TypeScript => provider.npm,
        Language::Python => provider.pypi,
    })
}

/// The environment variable that must carry an API key for `model`'s provider
/// at runtime, or `None` when no key can be pre-flighted.
///
/// Returns `None` when the provider cannot be determined from the model string
/// (e.g. a dynamically constructed model the CLI cannot read) or when the
/// provider does not authenticate via a single API-key env var (Bedrock's AWS
/// credentials, Vertex AI's application-default credentials, a local Ollama
/// server). Callers treat `None` as "nothing to verify".
pub fn required_api_key_env_for_model(model: &str) -> Option<&'static str> {
    provider_for_model(model).and_then(|provider| provider.api_key_env)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ModelApiKeyRequirement {
    NoPreflight,
    DeploymentSecret(&'static str),
    WorkspaceProviderSecret(&'static str),
    UnresolvedLangsmithProvider,
}

const KEYLESS_LANGSMITH_MODELS: &[&str] = &["langsmith:moonshotai/kimi-k3"];

struct LangsmithGatewayProviderSecret {
    provider: &'static str,
    secret_name: &'static str,
}

const LANGSMITH_GATEWAY_PROVIDER_SECRETS: &[LangsmithGatewayProviderSecret] = &[
    LangsmithGatewayProviderSecret {
        provider: "anthropic",
        secret_name: "ANTHROPIC_API_KEY",
    },
    LangsmithGatewayProviderSecret {
        provider: "baseten",
        secret_name: "BASETEN_API_KEY",
    },
    LangsmithGatewayProviderSecret {
        provider: "bedrock",
        secret_name: "AWS_BEARER_TOKEN_BEDROCK",
    },
    LangsmithGatewayProviderSecret {
        provider: "fireworks",
        secret_name: "FIREWORKS_API_KEY",
    },
    LangsmithGatewayProviderSecret {
        provider: "gemini",
        secret_name: "GOOGLE_API_KEY",
    },
    LangsmithGatewayProviderSecret {
        provider: "openai",
        secret_name: "OPENAI_API_KEY",
    },
    LangsmithGatewayProviderSecret {
        provider: "vertex",
        secret_name: "VERTEX_SERVICE_ACCOUNT_JSON",
    },
];

/// Resolve the API-key preflight for a deploy model.
///
/// A Gateway model must either match a keyless identifier exactly or resolve to
/// a workspace Provider Secret. An unmapped provider is rejected instead of
/// silently skipping the preflight.
pub(crate) fn deploy_model_api_key_requirement(model: &str) -> ModelApiKeyRequirement {
    if KEYLESS_LANGSMITH_MODELS.contains(&model) {
        return ModelApiKeyRequirement::NoPreflight;
    }

    let Some(gateway_model) = model.strip_prefix("langsmith:") else {
        return required_api_key_env_for_model(model).map_or(
            ModelApiKeyRequirement::NoPreflight,
            ModelApiKeyRequirement::DeploymentSecret,
        );
    };

    let Some((provider_key, model_name)) = gateway_model.split_once('/') else {
        return ModelApiKeyRequirement::UnresolvedLangsmithProvider;
    };
    if provider_key.is_empty() || model_name.is_empty() {
        return ModelApiKeyRequirement::UnresolvedLangsmithProvider;
    }

    LANGSMITH_GATEWAY_PROVIDER_SECRETS
        .iter()
        .find(|provider| provider.provider == provider_key)
        .map(|provider| provider.secret_name)
        .map_or(
            ModelApiKeyRequirement::UnresolvedLangsmithProvider,
            ModelApiKeyRequirement::WorkspaceProviderSecret,
        )
}

/// Every distinct API-key environment variable declared by the managed provider
/// registry. Used by `mda evals` Harbor jobs so supported models keep working
/// when their documented key is set (including providers not listed in the
/// scaffolded `.env` comments).
pub fn provider_api_key_envs() -> Vec<&'static str> {
    let mut names: Vec<&'static str> = MODEL_PROVIDERS
        .iter()
        .filter_map(|provider| provider.api_key_env)
        .collect();
    names.sort_unstable();
    names.dedup();
    names
}

/// Match `model` to a [`ModelProvider`], preferring an explicit, known
/// `provider:` prefix and falling back to name-based inference.
fn provider_for_model(model: &str) -> Option<&'static ModelProvider> {
    if let Some((prefix, _)) = model.split_once(':') {
        if let Some(provider) = MODEL_PROVIDERS.iter().find(|p| p.keys.contains(&prefix)) {
            return Some(provider);
        }
    }
    let key = infer_provider_from_model_name(model)?;
    MODEL_PROVIDERS.iter().find(|p| p.keys.contains(&key))
}

/// Infer a provider key from a bare model name's prefix.
///
/// Ported from LangChain's `_inferModelProvider`, extended with the additional
/// prefixes the Python `init_chat_model` recognizes (`deepseek`, `grok`).
fn infer_provider_from_model_name(model: &str) -> Option<&'static str> {
    if model.starts_with("gpt-3")
        || model.starts_with("gpt-4")
        || model.starts_with("gpt-5")
        || model.starts_with("o1")
        || model.starts_with("o3")
        || model.starts_with("o4")
    {
        Some("openai")
    } else if model.starts_with("claude") {
        Some("anthropic")
    } else if model.starts_with("command") {
        Some("cohere")
    } else if model.starts_with("accounts/fireworks") {
        Some("fireworks")
    } else if model.starts_with("gemini") {
        Some("google-vertexai")
    } else if model.starts_with("amazon.") {
        Some("bedrock")
    } else if model.starts_with("mistral") {
        Some("mistralai")
    } else if model.starts_with("deepseek") {
        Some("deepseek")
    } else if model.starts_with("grok") {
        Some("xai")
    } else if model.starts_with("sonar") || model.starts_with("pplx") {
        Some("perplexity")
    } else {
        None
    }
}

/// Render the user-facing TypeScript dependency (the runtime package every
/// project gets) as a `"name": "version"` JSON member indented by `indent`
/// spaces.
pub fn package_json_entries(indent: usize) -> String {
    package_json_entries_for_version(indent, &version::package_version())
}

/// Render the user-facing Python dependency as a quoted TOML array element
/// (e.g. `"managed-deepagents==0.7.3"`) indented by `indent` spaces.
pub fn pyproject_entries(indent: usize) -> String {
    pyproject_entries_for_version(indent, &version::package_version())
}

fn package_json_entries_for_version(indent: usize, package_version: &str) -> String {
    let pad = " ".repeat(indent);
    format!("{pad}\"{USER_RUNTIME_PACKAGE}\": \"{package_version}\"")
}

fn pyproject_entries_for_version(indent: usize, package_version: &str) -> String {
    let pad = " ".repeat(indent);
    format!("{pad}\"{USER_RUNTIME_PACKAGE}=={package_version}\"")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn browser_dependency_matches_the_runtime_stagehand_version() {
        let runtime = include_str!("../../../packages/pypi/src/managed_deepagents/browser.py");
        let version = runtime
            .lines()
            .find_map(|line| line.strip_prefix("STAGEHAND_VERSION = \""))
            .and_then(|rest| rest.strip_suffix('"'))
            .unwrap();
        assert_eq!(PYTHON_BROWSER.version, format!("=={version}"));
    }

    #[test]
    fn typescript_browser_dependency_matches_the_npm_packages_pin() {
        let package: serde_json::Value =
            serde_json::from_str(include_str!("../../../packages/npm/package.json")).unwrap();
        for dep in [TYPESCRIPT_BROWSER, TYPESCRIPT_BROWSER_TOOLS] {
            assert_eq!(package["peerDependencies"][dep.name], dep.version);
        }
        // The browser loads its extension from the same Stagehand release.
        let runtime = include_str!("../../../packages/npm/src/runtime/browser-langsmith.ts");
        let pin = format!("STAGEHAND_VERSION = \"{}\"", TYPESCRIPT_BROWSER.version);
        assert!(runtime.contains(&pin), "browser-langsmith.ts lacks {pin}");
        // Python deployments run the same Stagehand.
        assert_eq!(
            PYTHON_BROWSER.version,
            format!("=={}", TYPESCRIPT_BROWSER.version)
        );
    }

    #[test]
    fn browser_tools_dependency_matches_the_packages_typing_pin() {
        // The package's tests and type checks run against the release deployments get.
        let pyproject = include_str!("../../../packages/pypi/pyproject.toml");
        let pin = format!(
            "\"{}{}\"",
            PYTHON_BROWSER_TOOLS.name, PYTHON_BROWSER_TOOLS.version
        );
        assert!(
            pyproject.contains(&pin),
            "pyproject.toml does not pin {pin}"
        );
    }

    #[test]
    fn every_dependency_has_a_name_and_version() {
        assert!(!USER_RUNTIME_PACKAGE.is_empty());
        let managed = TYPESCRIPT_MANAGED
            .iter()
            .chain(PYTHON_MANAGED.iter())
            .chain(PYTHON_LOCAL_DEV.iter());
        for dep in managed {
            assert!(!dep.name.is_empty(), "dependency name must not be empty");
            assert!(
                !dep.version.is_empty(),
                "{} must declare a version",
                dep.name
            );
        }
        for provider in MODEL_PROVIDERS {
            assert!(!provider.keys.is_empty(), "provider must declare keys");
            assert!(!provider.npm.name.is_empty());
            assert!(!provider.pypi.name.is_empty());
        }
    }

    #[test]
    fn user_manifest_only_lists_managed_deepagents() {
        assert_eq!(
            package_json_entries_for_version(0, "1.2.3"),
            "\"managed-deepagents\": \"1.2.3\""
        );
        assert_eq!(
            pyproject_entries_for_version(0, "1.2.3"),
            "\"managed-deepagents==1.2.3\""
        );
    }

    #[test]
    fn user_managed_deepagents_is_pinned_to_the_runtime_package_version() {
        assert_eq!(
            package_json_entries_for_version(4, "0.0.3-dev.22"),
            "    \"managed-deepagents\": \"0.0.3-dev.22\""
        );
        assert_eq!(
            pyproject_entries_for_version(4, "0.0.3-dev.22"),
            "    \"managed-deepagents==0.0.3-dev.22\""
        );
    }

    #[test]
    fn deepagents_is_injected_by_the_managed_stack() {
        assert!(TYPESCRIPT_MANAGED.iter().any(|d| d.name == "deepagents"));
        assert!(PYTHON_MANAGED.iter().any(|d| d.name == "deepagents"));
    }

    #[test]
    fn langgraph_sdk_is_injected_for_the_managed_auth_handler() {
        // The generated `_mda_auth.py` builds `langgraph_sdk.Auth`, and the
        // TypeScript stack injects `@langchain/langgraph-sdk`, so the Python
        // manifest must inject `langgraph-sdk` explicitly for parity.
        assert!(PYTHON_MANAGED.iter().any(|d| d.name == "langgraph-sdk"));
        assert!(PYTHON_MANAGED
            .iter()
            .any(|d| d.name == "langgraph-prebuilt"));
        assert!(TYPESCRIPT_MANAGED
            .iter()
            .any(|d| d.name == "@langchain/langgraph-sdk"));
    }

    #[test]
    fn dev_server_cli_is_injected_for_local_dev() {
        assert!(local_dev_dependencies(Language::Python)
            .iter()
            .any(|d| d.name == "langgraph-cli[inmem]"));
        // Thread state keeps interrupt `response_schema` from langgraph-api 0.16.
        assert!(local_dev_dependencies(Language::Python)
            .iter()
            .any(|d| d.name == "langgraph-api" && d.version == ">=0.16.0"));
        assert!(local_dev_dependencies(Language::TypeScript)
            .iter()
            .any(|d| d.name == "@langchain/langgraph-cli"));
    }

    #[test]
    fn typescript_dev_server_cli_uses_a_compatible_range() {
        assert_eq!(TYPESCRIPT_DEV_SERVER_CLI.version, "^1.5.1");
    }

    #[test]
    fn langgraph_stack_uses_server_compatible_floors() {
        // Host JS graphs load these from the deploy npm tree. The generated
        // lockfile records a concrete compatible resolution for these floors.
        let langgraph = TYPESCRIPT_MANAGED
            .iter()
            .find(|dep| dep.name == "@langchain/langgraph")
            .expect("langgraph dependency must be present");
        let checkpoint = TYPESCRIPT_MANAGED
            .iter()
            .find(|dep| dep.name == "@langchain/langgraph-checkpoint")
            .expect("checkpoint dependency must be present");
        let sdk = TYPESCRIPT_MANAGED
            .iter()
            .find(|dep| dep.name == "@langchain/langgraph-sdk")
            .expect("langgraph-sdk dependency must be present");
        assert_eq!(langgraph.version, "^1.4.18");
        assert_eq!(checkpoint.version, "^1.1.3");
        assert_eq!(sdk.version, "^1.12.0");
    }

    #[test]
    fn package_json_entries_are_indented() {
        let rendered = package_json_entries(4);
        assert_eq!(
            rendered,
            format!(
                "    \"managed-deepagents\": \"{}\"",
                version::package_version()
            )
        );
    }

    #[test]
    fn pyproject_entries_are_indented() {
        let rendered = pyproject_entries(4);
        assert_eq!(
            rendered,
            format!("    \"managed-deepagents=={}\"", version::package_version())
        );
    }

    #[test]
    fn package_json_entries_compose_into_valid_json() {
        let manifest = format!(
            "{{\n  \"dependencies\": {{\n{}\n  }}\n}}",
            package_json_entries(4)
        );
        let value: serde_json::Value = serde_json::from_str(&manifest).unwrap();
        assert_eq!(
            value["dependencies"]["managed-deepagents"],
            version::package_version()
        );
    }

    #[test]
    fn provider_inferred_from_explicit_prefix() {
        let dep = provider_dependency_for_model("openai:gpt-5.5", Language::TypeScript).unwrap();
        assert_eq!(dep.name, "@langchain/openai");
        let dep = provider_dependency_for_model("anthropic:claude-x", Language::Python).unwrap();
        assert_eq!(dep.name, "langchain-anthropic");
    }

    #[test]
    fn langsmith_provider_uses_openai_integration_packages() {
        let dependencies = [
            provider_dependency_for_model("langsmith:moonshotai/kimi-k3", Language::TypeScript)
                .map(|dependency| dependency.name),
            provider_dependency_for_model("langsmith:moonshotai/kimi-k3", Language::Python)
                .map(|dependency| dependency.name),
        ];

        assert_eq!(
            dependencies,
            [Some("@langchain/openai"), Some("langchain-openai")]
        );
    }

    #[test]
    fn provider_inferred_from_bare_model_name() {
        assert_eq!(
            provider_dependency_for_model("gpt-5.5", Language::TypeScript)
                .unwrap()
                .name,
            "@langchain/openai"
        );
        assert_eq!(
            provider_dependency_for_model("claude-sonnet-4-5", Language::Python)
                .unwrap()
                .name,
            "langchain-anthropic"
        );
        assert_eq!(
            provider_dependency_for_model("gemini-2.0-flash", Language::TypeScript)
                .unwrap()
                .name,
            "@langchain/google-vertexai"
        );
    }

    #[test]
    fn provider_aliases_resolve_to_the_same_package() {
        // Python-style underscore spelling resolves identically to the
        // hyphenated JS spelling.
        assert_eq!(
            provider_dependency_for_model("google_genai:gemini-2.0", Language::Python)
                .unwrap()
                .name,
            "langchain-google-genai"
        );
        assert_eq!(
            provider_dependency_for_model("mistral:mistral-large", Language::TypeScript)
                .unwrap()
                .name,
            "@langchain/mistralai"
        );
    }

    #[test]
    fn managed_stack_includes_builtin_connector_adapter() {
        assert!(managed_dependencies(Language::TypeScript)
            .iter()
            .any(|dep| dep.name == "@langchain/mcp-adapters" && dep.version == "^2.0.0"));
        let python = managed_dependencies(Language::Python);
        assert!(python
            .iter()
            .any(|dep| dep.name == "langchain[mcp]" && dep.version == ">=1.4.6,<2"));
        assert!(python
            .iter()
            .any(|dep| dep.name == "fastmcp" && dep.version == ">=4.0.10,<5"));
        assert!(python
            .iter()
            .any(|dep| dep.name == "mcp" && dep.version == ">=2.2.0,<3"));
        assert!(!python
            .iter()
            .any(|dep| dep.name == "langchain-mcp-adapters"));
    }

    #[test]
    fn managed_typescript_stack_includes_uuid_for_trigger_thread_ids() {
        assert!(managed_dependencies(Language::TypeScript)
            .iter()
            .any(|dep| dep.name == "uuid" && dep.version == "^13.0.2"));
    }

    #[test]
    fn unknown_provider_yields_no_dependency() {
        assert!(provider_dependency_for_model("acme-llm-2", Language::TypeScript).is_none());
        // An unknown prefix is not stripped, and the full string doesn't infer.
        assert!(provider_dependency_for_model("acme:super-model", Language::Python).is_none());
    }

    #[test]
    fn required_api_key_env_resolves_for_keyed_providers() {
        assert_eq!(
            required_api_key_env_for_model("openai:gpt-5.5"),
            Some("OPENAI_API_KEY")
        );
        assert_eq!(
            required_api_key_env_for_model("gpt-5.5"),
            Some("OPENAI_API_KEY")
        );
        assert_eq!(
            required_api_key_env_for_model("claude-sonnet-4-5"),
            Some("ANTHROPIC_API_KEY")
        );
        assert_eq!(
            required_api_key_env_for_model("groq:llama-3.1"),
            Some("GROQ_API_KEY")
        );
        assert_eq!(
            required_api_key_env_for_model("mistral:mistral-large"),
            Some("MISTRAL_API_KEY")
        );
    }

    #[test]
    fn deploy_model_api_key_preflight_skips_the_exact_keyless_model() {
        assert_eq!(
            deploy_model_api_key_requirement("langsmith:moonshotai/kimi-k3"),
            ModelApiKeyRequirement::NoPreflight
        );
    }

    #[test]
    fn deploy_model_api_key_preflight_rejects_unqualified_gateway_model() {
        assert_eq!(
            deploy_model_api_key_requirement("langsmith:kimi-k3"),
            ModelApiKeyRequirement::UnresolvedLangsmithProvider
        );
    }

    #[test]
    fn deploy_model_api_key_preflight_rejects_empty_gateway_model() {
        assert_eq!(
            deploy_model_api_key_requirement("langsmith:openai/"),
            ModelApiKeyRequirement::UnresolvedLangsmithProvider
        );
    }

    #[test]
    fn deploy_model_api_key_preflight_rejects_keyless_model_case_variants() {
        assert_eq!(
            deploy_model_api_key_requirement("langsmith:moonshotai/Kimi-K3"),
            ModelApiKeyRequirement::UnresolvedLangsmithProvider
        );
    }

    #[test]
    fn deploy_model_api_key_preflight_rejects_other_models_from_the_keyless_provider() {
        assert_eq!(
            deploy_model_api_key_requirement("langsmith:moonshotai/other-model"),
            ModelApiKeyRequirement::UnresolvedLangsmithProvider
        );
    }

    #[test]
    fn deploy_model_api_key_preflight_resolves_slash_gateway_provider() {
        assert_eq!(
            deploy_model_api_key_requirement("langsmith:openai/gpt-5.5"),
            ModelApiKeyRequirement::WorkspaceProviderSecret("OPENAI_API_KEY")
        );
    }

    #[test]
    fn direct_models_require_deployment_provider_keys() {
        for (model, key) in [
            ("openai:gpt-5.5", "OPENAI_API_KEY"),
            ("anthropic:claude-sonnet-4-5", "ANTHROPIC_API_KEY"),
            ("cohere:command-r", "COHERE_API_KEY"),
        ] {
            assert_eq!(
                deploy_model_api_key_requirement(model),
                ModelApiKeyRequirement::DeploymentSecret(key)
            );
        }
    }

    #[test]
    fn deploy_model_api_key_preflight_resolves_baseten_gateway_provider() {
        assert_eq!(
            deploy_model_api_key_requirement("langsmith:baseten/zai-org/GLM-5.2"),
            ModelApiKeyRequirement::WorkspaceProviderSecret("BASETEN_API_KEY")
        );
    }

    #[test]
    fn deploy_model_api_key_preflight_resolves_gemini_gateway_provider() {
        assert_eq!(
            deploy_model_api_key_requirement("langsmith:gemini/gemini-3.6-flash"),
            ModelApiKeyRequirement::WorkspaceProviderSecret("GOOGLE_API_KEY")
        );
    }

    #[test]
    fn deploy_model_api_key_preflight_resolves_vertex_gateway_provider() {
        assert_eq!(
            deploy_model_api_key_requirement("langsmith:vertex/gemini-3.1-pro"),
            ModelApiKeyRequirement::WorkspaceProviderSecret("VERTEX_SERVICE_ACCOUNT_JSON")
        );
    }

    #[test]
    fn deploy_model_api_key_preflight_rejects_colon_gateway_provider() {
        assert_eq!(
            deploy_model_api_key_requirement("langsmith:anthropic:claude-sonnet-4-5"),
            ModelApiKeyRequirement::UnresolvedLangsmithProvider
        );
    }

    #[test]
    fn deploy_model_api_key_preflight_rejects_unknown_gateway_provider() {
        assert_eq!(
            deploy_model_api_key_requirement("langsmith:acme/model"),
            ModelApiKeyRequirement::UnresolvedLangsmithProvider
        );
    }

    #[test]
    fn deploy_model_api_key_preflight_resolves_bedrock_gateway_provider() {
        assert_eq!(
            deploy_model_api_key_requirement("langsmith:bedrock/amazon.nova"),
            ModelApiKeyRequirement::WorkspaceProviderSecret("AWS_BEARER_TOKEN_BEDROCK")
        );
    }

    #[test]
    fn provider_api_key_envs_covers_registry_keys() {
        let names = provider_api_key_envs();
        for expected in [
            "ANTHROPIC_API_KEY",
            "COHERE_API_KEY",
            "DEEPSEEK_API_KEY",
            "FIREWORKS_API_KEY",
            "GOOGLE_API_KEY",
            "GROQ_API_KEY",
            "MISTRAL_API_KEY",
            "OPENAI_API_KEY",
            "PPLX_API_KEY",
            "TOGETHER_AI_API_KEY",
            "XAI_API_KEY",
        ] {
            assert!(names.contains(&expected), "missing {expected} in {names:?}");
        }
    }

    #[test]
    fn required_api_key_env_is_none_for_credential_or_keyless_providers() {
        // Bedrock (AWS credentials), Vertex AI (ADC), and Ollama (local) have
        // no single API-key env var to pre-flight.
        assert_eq!(required_api_key_env_for_model("amazon.titan-text"), None);
        assert_eq!(required_api_key_env_for_model("gemini-2.0-flash"), None);
        assert_eq!(required_api_key_env_for_model("ollama:llama3"), None);
    }

    #[test]
    fn required_api_key_env_is_none_for_unknown_provider() {
        assert_eq!(required_api_key_env_for_model("acme-llm-2"), None);
        assert_eq!(required_api_key_env_for_model("acme:super-model"), None);
    }
}
