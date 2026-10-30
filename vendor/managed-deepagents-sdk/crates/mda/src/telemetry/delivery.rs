//! Bounded delivery at exit: telemetry must never hold up the command.

use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

/// A unit of work handed to [`run_within`].
type Job = Box<dyn FnOnce() + Send>;

/// Run `jobs` concurrently and return once all finish or `deadline` passes,
/// whichever is first. Unfinished jobs keep running detached and end with
/// the process.
pub fn run_within(deadline: Duration, jobs: Vec<Job>) {
    let until = Instant::now() + deadline;
    let (finished, done) = mpsc::channel();
    let count = jobs.len();
    for job in jobs {
        let finished = finished.clone();
        thread::spawn(move || {
            job();
            let _ = finished.send(());
        });
    }
    for _ in 0..count {
        let left = until.saturating_duration_since(Instant::now());
        if done.recv_timeout(left).is_err() {
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn returns_when_every_job_finishes() {
        let started = Instant::now();

        run_within(
            Duration::from_secs(5),
            vec![Box::new(|| {}), Box::new(|| {})],
        );

        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn a_stuck_job_does_not_outlast_the_deadline() {
        let started = Instant::now();

        run_within(
            Duration::from_millis(200),
            vec![Box::new(|| thread::sleep(Duration::from_secs(5)))],
        );

        assert!(started.elapsed() < Duration::from_secs(1));
    }
}
