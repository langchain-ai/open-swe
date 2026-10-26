//! Managed Deep Agents CLI library.
//!
//! Exposes the compilation pipeline (project discovery and build emission) so
//! it can be driven by the `mda` binary and exercised by integration tests.

pub(crate) mod agent_config;
pub mod channel;
pub mod chat;
pub(crate) mod child;
pub(crate) mod cli_output;
pub use cli_output::{help_heading, styled_help_template};
pub mod coding_agent;
pub mod command;
pub mod compile;
pub mod connection;
pub(crate) mod context_layout;
pub mod delete;
pub mod dependencies;
pub mod deploy;
pub mod dev;
pub mod error;
pub mod eval;
pub(crate) mod github;
pub(crate) mod http;
pub mod init;
pub mod install;
pub(crate) mod interrupt;
pub mod langsmith;
pub(crate) mod local_context_hub;
pub mod logs;
pub(crate) mod naming;
pub(crate) mod path_util;
pub mod project;
pub(crate) mod python_extract;
pub mod secret_input;
pub(crate) mod state;
pub mod telemetry;
pub mod termination;
#[cfg(test)]
pub(crate) mod test_support;
pub(crate) mod text_module;
pub(crate) mod toolchain;
pub(crate) mod typescript;
pub mod version;
