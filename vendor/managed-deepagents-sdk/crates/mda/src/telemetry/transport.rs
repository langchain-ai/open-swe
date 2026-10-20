use anyhow::Result;

use super::event::EventEnvelope;

/// Sends analytics events to a specific backend.
pub trait TelemetryTransport {
    /// Send one event, returning an error when it was not accepted.
    fn send(&self, event: &EventEnvelope) -> Result<()>;

    /// Send an event without allowing transport failures to affect the CLI.
    fn record(&self, event: &EventEnvelope) {
        let _ = self.send(event);
    }
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;
    use std::rc::Rc;

    use anyhow::bail;

    use super::*;
    use crate::telemetry::event::fixture_event;

    struct MockTransport {
        calls: Rc<Cell<usize>>,
        fails: bool,
    }

    impl TelemetryTransport for MockTransport {
        fn send(&self, _event: &EventEnvelope) -> Result<()> {
            self.calls.set(self.calls.get() + 1);
            if self.fails {
                bail!("mock transport failure");
            }
            Ok(())
        }
    }

    #[test]
    fn record_calls_the_transport_once() {
        let calls = Rc::new(Cell::new(0));
        let transport = MockTransport {
            calls: Rc::clone(&calls),
            fails: false,
        };

        transport.record(&fixture_event());

        assert_eq!(calls.get(), 1);
    }

    #[test]
    fn record_swallows_transport_failures() {
        let calls = Rc::new(Cell::new(0));
        let transport = MockTransport {
            calls: Rc::clone(&calls),
            fails: true,
        };

        transport.record(&fixture_event());

        assert_eq!(calls.get(), 1);
    }
}
