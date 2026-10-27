//! Delivering the run's telemetry once, however the process ends: main
//! returning, a termination signal, a second Ctrl-C, or a panic.
//!
//! The exits other than main's call [`std::process::exit`] or abort, which
//! would drop the OTel buffer and the `command_completed` event unsent. They
//! call [`deliver_at_exit`] first instead; only the first call sends, so a
//! signal racing main's own exit delivers once.

use std::sync::{Mutex, PoisonError};
use std::time::Duration;

use crate::command::CommandCompletion;
use crate::error::ErrorCategory;

use super::delivery::run_within;
use super::event::EventEnvelope;
use super::facts::facts;
use super::otel_logger::{self, OtelLogger};
use super::posthog::PosthogTransport;
use super::stage;
use super::transport::TelemetryTransport;

/// How long exit may wait for telemetry to be delivered.
const EXIT_DELIVERY_DEADLINE: Duration = Duration::from_secs(1);

type Delivery = Box<dyn FnOnce(CommandCompletion) + Send>;

static PENDING: Mutex<Option<Delivery>> = Mutex::new(None);

/// Hold the run's sinks until it ends. Call once, after they are installed.
pub fn hold_for_exit(logger: Option<OtelLogger>, posthog: Option<PosthogTransport>) {
    hold(
        &PENDING,
        Box::new(move |completion| {
            stage::close(completion);
            otel_logger::record_completion(completion);
            let event =
                facts().and_then(|facts| EventEnvelope::command_completed(facts, completion));
            let mut jobs: Vec<Box<dyn FnOnce() + Send>> = Vec::new();
            if let Some(logger) = logger {
                jobs.push(Box::new(move || logger.shutdown()));
            }
            if let (Some(posthog), Some(event)) = (posthog, event) {
                jobs.push(Box::new(move || posthog.record(&event)));
            }
            run_within(EXIT_DELIVERY_DEADLINE, jobs);
        }),
    );
}

/// Record how the run ended and send its telemetry, waiting at most
/// [`EXIT_DELIVERY_DEADLINE`]. Does nothing after the first call, or when
/// nothing is held because telemetry is off.
pub fn deliver_at_exit(completion: CommandCompletion) {
    deliver(&PENDING, completion);
}

/// Report a panic as an error and a failed run before the process aborts.
/// The previous hook runs first, so the panic message still reaches the
/// terminal before the delivery wait.
pub fn report_panics() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        previous(info);
        let message = panic_message(info);
        // On its own thread, bounded by the deadline: the panic may have left a
        // lock held that delivery needs, and the abort must not wait on it.
        run_within(
            EXIT_DELIVERY_DEADLINE,
            vec![Box::new(move || {
                otel_logger::panic(&message);
                deliver_at_exit(CommandCompletion::Failed(ErrorCategory::Unclassified));
            })],
        );
    }));
}

fn hold(slot: &Mutex<Option<Delivery>>, delivery: Delivery) {
    *slot.lock().unwrap_or_else(PoisonError::into_inner) = Some(delivery);
}

fn deliver(slot: &Mutex<Option<Delivery>>, completion: CommandCompletion) {
    let delivery = slot.lock().unwrap_or_else(PoisonError::into_inner).take();
    if let Some(delivery) = delivery {
        delivery(completion);
    }
}

/// `panicked at <file>:<line>:<column>: <message>`, as the default hook prints it.
fn panic_message(info: &std::panic::PanicHookInfo<'_>) -> String {
    let payload = info.payload();
    let message = payload
        .downcast_ref::<&str>()
        .copied()
        .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
        .unwrap_or("Box<dyn Any>");
    match info.location() {
        Some(location) => format!("panicked at {location}: {message}"),
        None => format!("panicked: {message}"),
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use super::*;

    #[test]
    fn only_the_first_exit_delivers() {
        let slot = Mutex::new(None);
        let delivered = Arc::new(Mutex::new(Vec::new()));
        let sink = Arc::clone(&delivered);
        hold(
            &slot,
            Box::new(move |completion| sink.lock().unwrap().push(completion)),
        );

        deliver(&slot, CommandCompletion::Cancelled);
        deliver(&slot, CommandCompletion::Succeeded);

        assert_eq!(*delivered.lock().unwrap(), [CommandCompletion::Cancelled]);
    }

    #[test]
    fn nothing_held_delivers_nothing() {
        let slot = Mutex::new(None);

        deliver(&slot, CommandCompletion::Succeeded);

        assert!(slot.lock().unwrap().is_none());
    }
}
