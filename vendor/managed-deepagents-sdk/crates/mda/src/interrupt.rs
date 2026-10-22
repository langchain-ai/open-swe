//! The user's request to stop.
//!
//! `ctrlc::set_handler` accepts one handler per process, so the flag is
//! installed once and shared. Commands poll it and return a normal value rather
//! than exiting from the handler, which is what lets the dispatcher in `main`
//! turn a Ctrl-C into an exit code and an analytics event.
//!
//! This is only the request. What a child process does about it belongs to
//! [`crate::child`], which owns the child and therefore knows the answer without
//! having to work it out from an exit status.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, OnceLock};
use std::thread::sleep;
use std::time::{Duration, Instant};

use anyhow::Result;

/// How often an interruptible sleep rechecks the flag.
const POLL_INTERVAL: Duration = Duration::from_millis(50);

static INTERRUPT: OnceLock<Result<Interrupt, String>> = OnceLock::new();

/// A handle on this process's Ctrl-C flag.
#[derive(Clone)]
pub(crate) struct Interrupt(Arc<AtomicBool>);

impl Interrupt {
    /// Install the handler on the first call, then hand out the same flag.
    pub(crate) fn install() -> Result<Self> {
        INTERRUPT
            .get_or_init(|| {
                let requested = Arc::new(AtomicBool::new(false));
                let handler = Arc::clone(&requested);
                ctrlc::set_handler(move || {
                    handler.store(true, Ordering::SeqCst);
                })
                .map_err(|err| format!("failed to install Ctrl-C handler: {err}"))?;
                Ok(Self(requested))
            })
            .clone()
            .map_err(anyhow::Error::msg)
    }

    /// Whether the user has asked the command to stop.
    pub(crate) fn is_requested(&self) -> bool {
        self.0.load(Ordering::SeqCst)
    }

    /// Sleep up to `total`, returning as soon as Ctrl-C arrives.
    pub(crate) fn sleep_until_interrupted(&self, total: Duration) {
        let deadline = Instant::now() + total;
        while !self.is_requested() {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return;
            }
            sleep(remaining.min(POLL_INTERVAL));
        }
    }

    #[cfg(test)]
    pub(crate) fn pending() -> Self {
        Self(Arc::new(AtomicBool::new(false)))
    }

    #[cfg(test)]
    pub(crate) fn requested() -> Self {
        Self(Arc::new(AtomicBool::new(true)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn requested_interrupt_does_not_sleep() {
        let started = Instant::now();

        Interrupt::requested().sleep_until_interrupted(Duration::from_secs(30));

        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn pending_interrupt_sleeps_for_the_requested_duration() {
        let total = POLL_INTERVAL * 3;
        let started = Instant::now();

        Interrupt::pending().sleep_until_interrupted(total);

        assert!(started.elapsed() >= total);
    }
}
