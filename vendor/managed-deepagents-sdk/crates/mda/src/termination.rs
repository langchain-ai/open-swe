//! Termination signals: SIGTERM from an agent's or CI's timeout, SIGHUP from a
//! closed terminal.
//!
//! Ctrl-C is a request each command answers in its own time (see
//! [`crate::interrupt`]). These are not: the main thread may be blocked on a
//! prompt or a network read and never look at a flag again. So a watcher thread
//! takes down every process group the CLI owns (see [`crate::child`]), sends
//! the run's telemetry as cancelled, and exits with the conventional
//! `128 + signal` status. SIGKILL cannot be caught, so a
//! caller that skips straight to it still leaves the children behind.
//!
//! Killing the children wakes the main thread too — it sees its server exit and
//! would report that as the command's failure. [`hand_over_if_terminating`]
//! lets it step aside so the status and output are the signal's.

use std::sync::atomic::{AtomicBool, Ordering};

use anyhow::Result;

/// Set once a termination signal arrives, before any child is touched.
static TERMINATING: AtomicBool = AtomicBool::new(false);

/// Block forever if a termination signal is being handled, so the watcher's
/// exit decides the status instead of whatever the command made of its
/// children dying. A no-op otherwise.
pub fn hand_over_if_terminating() {
    while TERMINATING.load(Ordering::SeqCst) {
        std::thread::park();
    }
}

/// Start the watcher. Call once, early in `main`.
#[cfg(unix)]
pub fn install() -> Result<()> {
    use anyhow::Context;
    use signal_hook::consts::{SIGHUP, SIGTERM};
    use signal_hook::iterator::Signals;

    let mut signals =
        Signals::new([SIGTERM, SIGHUP]).context("failed to install the termination handler")?;
    std::thread::Builder::new()
        .name("termination".to_string())
        .spawn(move || {
            if let Some(signal) = signals.forever().next() {
                TERMINATING.store(true, Ordering::SeqCst);
                crate::child::terminate_registered();
                crate::telemetry::deliver_at_exit(crate::command::CommandCompletion::Cancelled);
                std::process::exit(128 + signal);
            }
        })
        .context("failed to start the termination handler")?;
    Ok(())
}

/// Windows ends console processes without a signal to intercept.
#[cfg(not(unix))]
pub fn install() -> Result<()> {
    Ok(())
}
