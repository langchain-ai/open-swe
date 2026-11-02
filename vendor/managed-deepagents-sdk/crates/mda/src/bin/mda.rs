//! `mda` — the Managed Deep Agents CLI.
//!
//! Compiles a code-first Deep Agent repository into a managed LangGraph app
//! that can be deployed to LangSmith.

fn main() -> std::process::ExitCode {
    mda::run()
}
