//! Shared control-flow types for CLI command execution.

use crate::error::Failure;

/// How a CLI command ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommandCompletion {
    /// The command finished its requested work.
    Succeeded,
    /// The caller cancelled before the command performed its guarded work.
    Cancelled,
    Failed(Failure),
}
