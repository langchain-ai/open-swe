//! Managed Deep Agents CLI library.
//!
//! Everything lives here so the `mda` binary is a thin `main` over [`run`].
//! Modules are crate-private so `dead_code = "deny"` can flag unused items;
//! only what the integration tests in `tests/` need is public.

pub(crate) mod agent_config;
pub(crate) mod channel;
pub(crate) mod chat;
pub(crate) mod child;
mod cli;
pub use cli::run;
pub(crate) mod cli_output;
pub(crate) use cli_output::styled_help_template;
pub mod coding_agent;
pub(crate) mod command;
pub(crate) mod compile;
pub(crate) mod connection;
pub(crate) mod context_layout;
pub(crate) mod delete;
pub(crate) mod dependencies;
pub(crate) mod deploy;
pub(crate) mod dev;
pub(crate) mod error;
pub(crate) mod eval;
pub(crate) mod github;
pub(crate) mod http;
pub(crate) mod init;
pub(crate) mod install;
pub(crate) mod interrupt;
pub(crate) mod langsmith;
pub(crate) mod local_context_hub;
pub(crate) mod logs;
pub(crate) mod naming;
pub(crate) mod path_util;
pub(crate) mod project;
pub(crate) mod python_extract;
pub(crate) mod secret_input;
pub(crate) mod state;
pub(crate) mod telemetry;
pub(crate) mod termination;
#[cfg(test)]
pub(crate) mod test_support;
pub(crate) mod text_module;
pub(crate) mod toolchain;
pub(crate) mod typescript;
pub(crate) mod version;
