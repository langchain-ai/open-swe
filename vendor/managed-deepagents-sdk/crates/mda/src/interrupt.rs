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
    ///
    /// The first Ctrl-C only sets the flag. A second one means the command did
    /// not stop, such as during a long request, so it takes the children down
    /// and exits at once.
    pub(crate) fn install() -> Result<Self> {
        INTERRUPT
            .get_or_init(|| {
                let requested = Arc::new(AtomicBool::new(false));
                let handler = Arc::clone(&requested);
                ctrlc::set_handler(move || on_signal(&handler, force_exit))
                    .map_err(|err| format!("failed to install Ctrl-C handler: {err}"))?;
                Ok(Self(requested))
            })
            .clone()
            .map_err(anyhow::Error::msg)
    }

    /// The flag, once something has installed it. Waits use this rather than
    /// [`Self::install`] so that, where nothing did, Ctrl-C still ends the
    /// process the default way.
    pub(crate) fn current() -> Option<Self> {
        INTERRUPT.get()?.as_ref().ok().cloned()
    }

    /// Whether the user has asked the command to stop.
    pub(crate) fn is_requested(&self) -> bool {
        self.0.load(Ordering::SeqCst)
    }

    /// An `Interrupted` error once the user has asked the command to stop, so
    /// a command can stop between stages and report a cancellation.
    pub(crate) fn check(&self) -> std::io::Result<()> {
        if self.is_requested() {
            return Err(std::io::ErrorKind::Interrupted.into());
        }
        Ok(())
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

/// Sleep up to `total` between polls of a long wait, and fail with
/// `Interrupted` if the user asked to stop. Without an installed handler it is
/// a plain sleep, since Ctrl-C then ends the process by itself.
pub(crate) fn pause(total: Duration) -> std::io::Result<()> {
    let Some(interrupt) = Interrupt::current() else {
        sleep(total);
        return Ok(());
    };
    interrupt.sleep_until_interrupted(total);
    interrupt.check()
}

/// Set the flag on the first signal, and `force` on any after it.
fn on_signal(requested: &AtomicBool, force: impl FnOnce()) {
    if requested.swap(true, Ordering::SeqCst) {
        force();
    }
}

/// Take down every child group, as a termination signal would, and exit
/// with the conventional `128 + SIGINT`.
fn force_exit() {
    #[cfg(unix)]
    crate::child::terminate_registered();
    // Windows has no group walk yet, so its children are not stopped here.
    crate::telemetry::deliver_at_exit(crate::command::CommandCompletion::Cancelled);
    std::process::exit(130);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_second_signal_forces_an_exit() {
        let requested = AtomicBool::new(false);
        let mut forced = 0;

        on_signal(&requested, || forced += 1);
        assert!(requested.load(Ordering::SeqCst));
        assert_eq!(forced, 0);

        on_signal(&requested, || forced += 1);
        assert_eq!(forced, 1);
    }

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

    #[test]
    fn check_fails_only_once_requested() {
        assert!(Interrupt::pending().check().is_ok());
        assert_eq!(
            Interrupt::requested().check().unwrap_err().kind(),
            std::io::ErrorKind::Interrupted
        );
    }
}
