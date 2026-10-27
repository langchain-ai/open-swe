//! Child processes this CLI owns.
//!
//! An owned child runs in its own process group, so a terminal Ctrl-C reaches
//! only us: every stop is one we sent, and no caller has to work out from an exit
//! status whether the user or the child ended it.
//!
//! The flip side is that a signal sent to us alone — SIGTERM from an agent's
//! timeout, SIGHUP from a closed terminal — never reaches them either. Every
//! group is therefore registered while it lives, so [`terminate_registered`]
//! can take them all down from a signal thread that holds no `OwnedChild`.
//!
//! [`OwnedChild::wait`] takes the interrupt as a parameter rather than reading
//! the process-global flag so the tests below can fabricate one and run without
//! installing a real signal handler.

use std::collections::BTreeSet;
use std::io::{self, Read, Write};
use std::process::{Child, Command, ExitStatus, Output, Stdio};
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::thread::{self, sleep, JoinHandle};
use std::time::{Duration, Instant};

use anyhow::{bail, Result};

use crate::interrupt::Interrupt;

/// How often a running child is polled.
const POLL_INTERVAL: Duration = Duration::from_millis(100);

/// How often a captured child is polled. Short, because most of these finish
/// in milliseconds and some run once per source file.
const CAPTURE_POLL_INTERVAL: Duration = Duration::from_millis(10);

/// How long a child gets to act on the forwarded interrupt before we escalate to
/// a kill.
///
/// A reloading server's workers do not always take the interrupt, and this is the
/// delay the user sees at the prompt when they do not. Measured clean shutdowns
/// land near 300ms, so this is generous without being felt.
#[cfg_attr(not(unix), allow(dead_code))]
const STOP_GRACE_PERIOD: Duration = Duration::from_secs(1);

/// How an owned child ended.
pub(crate) enum Outcome {
    /// It ended on its own, so this status is its own verdict.
    Exited(ExitStatus),
    /// We asked it to stop and it did.
    Stopped,
}

/// Process groups this CLI started and has not reaped, by leader pid.
#[derive(Default)]
struct Registry {
    groups: BTreeSet<u32>,
    /// Termination has taken its snapshot; no group may join after it.
    closed: bool,
}

type SharedRegistry = Mutex<Registry>;

static LIVE_GROUPS: SharedRegistry = Mutex::new(Registry {
    groups: BTreeSet::new(),
    closed: false,
});

fn lock(registry: &SharedRegistry) -> MutexGuard<'_, Registry> {
    // A set of integers and a flag cannot be left half-updated, so a poisoned
    // lock is still a correct one.
    registry.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Keeps a spawned child's group in the registry until it is dropped.
///
/// Drop it before reaping the child: once reaped, the pid is free for reuse.
pub(crate) struct GroupRegistration {
    registry: &'static SharedRegistry,
    group: u32,
}

impl GroupRegistration {
    pub(crate) fn new(child: &Child) -> Self {
        Self::new_in(&LIVE_GROUPS, child)
    }

    /// Register `child`'s group, or kill it on the spot if termination already
    /// took its snapshot — the main thread keeps running through the grace
    /// period, and a group it starts then would otherwise outlive us.
    fn new_in(registry: &'static SharedRegistry, child: &Child) -> Self {
        let group = child.id();
        let mut live = lock(registry);
        if live.closed {
            drop(live);
            kill_late_group(group);
        } else {
            live.groups.insert(group);
        }
        Self { registry, group }
    }
}

impl Drop for GroupRegistration {
    fn drop(&mut self) {
        lock(self.registry).groups.remove(&self.group);
    }
}

#[cfg(unix)]
fn kill_late_group(group: u32) {
    signal_group_id(group, "-KILL");
}

/// Only the unix termination watcher closes the registry.
#[cfg(not(unix))]
fn kill_late_group(_group: u32) {}

/// Close `registry` to new groups and return the ones it holds, in one step
/// under its lock, so no group can register between the snapshot and the close.
#[cfg(unix)]
fn close(registry: &SharedRegistry) -> Vec<u32> {
    let mut live = lock(registry);
    live.closed = true;
    live.groups.iter().copied().collect()
}

#[cfg(test)]
fn registered_groups() -> Vec<u32> {
    lock(&LIVE_GROUPS).groups.iter().copied().collect()
}

/// Take down every process group this CLI still owns, and any it starts from
/// here on.
///
/// For a termination signal, which can arrive while the main thread is blocked
/// on a prompt or a request and will never look at the interrupt flag.
#[cfg(unix)]
pub(crate) fn terminate_registered() {
    terminate_groups(&close(&LIVE_GROUPS));
}

/// Interrupt `groups` together, give them one shared grace period, then kill
/// whatever is left — the same escalation as [`stop_tree`], without the
/// `Child` handles to reap.
#[cfg(unix)]
fn terminate_groups(groups: &[u32]) {
    for group in groups {
        signal_group_id(*group, "-INT");
    }
    let deadline = Instant::now() + STOP_GRACE_PERIOD;
    while Instant::now() < deadline && groups.iter().any(|group| group_exists(*group)) {
        sleep(POLL_INTERVAL);
    }
    for group in groups {
        signal_group_id(*group, "-KILL");
    }
}

/// A child process whose lifetime this CLI controls.
pub(crate) struct OwnedChild {
    child: Child,
    registration: Option<GroupRegistration>,
}

impl OwnedChild {
    /// Spawn `command` into its own process group.
    pub(crate) fn spawn(command: &mut Command) -> io::Result<Self> {
        isolate(command);
        let child = command.spawn()?;
        Ok(Self {
            registration: Some(GroupRegistration::new(&child)),
            child,
        })
    }

    /// Whether the child is over, without blocking. Takes its group down once it
    /// is, whether it exited on its own or we stopped it.
    pub(crate) fn poll(&mut self, interrupt: &Interrupt) -> io::Result<Option<Outcome>> {
        if self.child.try_wait()?.is_none() && !interrupt.is_requested() {
            return Ok(None);
        }
        Ok(Some(match self.stop()? {
            Some(status) => Outcome::Exited(status),
            None => Outcome::Stopped,
        }))
    }

    /// Wait for the child, stopping it if the user interrupts first.
    pub(crate) fn wait(&mut self, interrupt: &Interrupt) -> io::Result<Outcome> {
        loop {
            if let Some(outcome) = self.poll(interrupt)? {
                return Ok(outcome);
            }
            interrupt.sleep_until_interrupted(POLL_INTERVAL);
        }
    }

    /// Stop the child and everything it spawned, then reap it.
    ///
    /// Returns the child's own status when it had already exited, so a failure
    /// racing the Ctrl-C is not reported as a stop we chose. The group still goes
    /// down in that case, because the leader exiting does not empty it.
    pub(crate) fn stop(&mut self) -> io::Result<Option<ExitStatus>> {
        let exited = self.child.try_wait()?;
        stop_tree(&mut self.child)?;
        self.registration = None;
        self.child.wait()?;
        Ok(exited)
    }
}

/// Run `command` to completion as an owned child and collect what it wrote.
///
/// Ctrl-C stops its group and returns an `Interrupted` error, which the command
/// reports as a cancellation.
pub(crate) fn output(command: &mut Command) -> Result<Output> {
    capture(command, None, None, &Interrupt::install()?)
}

/// [`output`], writing `stdin` to the child first.
pub(crate) fn output_with_stdin(command: &mut Command, stdin: &[u8]) -> Result<Output> {
    capture(command, Some(stdin), None, &Interrupt::install()?)
}

/// [`output`], stopping the child's group if it runs past `timeout`.
pub(crate) fn output_with_timeout(command: &mut Command, timeout: Duration) -> Result<Output> {
    capture(command, None, Some(timeout), &Interrupt::install()?)
}

fn capture(
    command: &mut Command,
    stdin: Option<&[u8]>,
    timeout: Option<Duration>,
    interrupt: &Interrupt,
) -> Result<Output> {
    command
        .stdin(if stdin.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut owned = OwnedChild::spawn(command)?;
    // On its own thread so a child that writes before it reads cannot
    // deadlock against us. A failed write is not reported: the child's status
    // and stderr say why it stopped reading.
    if let (Some(mut pipe), Some(bytes)) = (owned.child.stdin.take(), stdin.map(<[u8]>::to_vec)) {
        thread::spawn(move || {
            let _ = pipe.write_all(&bytes);
        });
    }
    // Read while the child runs: a full pipe would otherwise block it. Both
    // ends close once its group is down, which every outcome below ensures.
    let stdout = read_to_end(owned.child.stdout.take());
    let stderr = read_to_end(owned.child.stderr.take());
    let deadline = timeout.map(|timeout| Instant::now() + timeout);
    let outcome = loop {
        if let Some(outcome) = owned.poll(interrupt)? {
            break Some(outcome);
        }
        if deadline.is_some_and(|deadline| Instant::now() >= deadline) {
            owned.stop()?;
            break None;
        }
        interrupt.sleep_until_interrupted(CAPTURE_POLL_INTERVAL);
    };
    let stdout = stdout.join().unwrap_or_default();
    let stderr = stderr.join().unwrap_or_default();
    match outcome {
        Some(Outcome::Exited(status)) => Ok(Output {
            status,
            stdout,
            stderr,
        }),
        Some(Outcome::Stopped) => Err(io::Error::from(io::ErrorKind::Interrupted).into()),
        None => bail!(
            "subprocess timed out after {}s",
            timeout.unwrap_or_default().as_secs()
        ),
    }
}

/// A child's stderr, passed through to ours as it arrives, with its last part
/// kept for the error if the child fails.
#[derive(Default)]
pub(crate) struct StderrTail(Option<JoinHandle<Vec<u8>>>);

impl StderrTail {
    /// Take `owned`'s piped stderr, keeping its last `keep` bytes.
    pub(crate) fn tee(owned: &mut OwnedChild, keep: usize) -> Self {
        Self(owned.child.stderr.take().map(|mut pipe| {
            thread::spawn(move || {
                let mut tail = std::collections::VecDeque::with_capacity(keep);
                let mut chunk = [0; 8 << 10];
                while let Ok(read @ 1..) = pipe.read(&mut chunk) {
                    let _ = io::stderr().write_all(&chunk[..read]);
                    tail.extend(&chunk[..read]);
                    tail.drain(..tail.len().saturating_sub(keep));
                }
                tail.into()
            })
        }))
    }

    /// The kept bytes. Call once the child's group is down, so the pipe has
    /// closed.
    pub(crate) fn finish(self) -> Vec<u8> {
        self.0
            .and_then(|reader| reader.join().ok())
            .unwrap_or_default()
    }
}

fn read_to_end(pipe: Option<impl Read + Send + 'static>) -> JoinHandle<Vec<u8>> {
    thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(mut pipe) = pipe {
            // A read error ends the capture early; what was read still helps.
            let _ = pipe.read_to_end(&mut buf);
        }
        buf
    })
}

/// Ask the child's whole group to stop, then insist.
///
/// The child leads its own group, so its pid doubles as the group id. The
/// interrupt is forwarded because the terminal no longer delivers it, and a
/// server killed outright never flushes.
///
/// The kill is unconditional because the leader exiting does not mean the group
/// is empty: a reloading server's worker can ignore the interrupt and outlive the
/// supervisor that took it, and it would then keep serving the old build.
#[cfg(unix)]
pub(crate) fn stop_tree(child: &mut Child) -> io::Result<()> {
    signal_group(child, "-INT");
    let _ = wait_for_exit(child, STOP_GRACE_PERIOD)?;
    signal_group(child, "-KILL");
    Ok(())
}

#[cfg(unix)]
fn signal_group(child: &Child, signal: &str) {
    signal_group_id(child.id(), signal);
}

#[cfg(unix)]
fn signal_group_id(group: u32, signal: &str) {
    let group = format!("-{group}");
    // The escalating KILL routinely finds a group the INT already emptied, and
    // `kill` reports that on stderr. The caller chose to insist, so the notice
    // is ours, not the user's — it would otherwise land in the middle of
    // whatever the command was printing.
    let _ = Command::new("/bin/kill")
        .args([signal, "--", &group])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

/// Whether any process, zombies included, is still in `group`.
#[cfg(unix)]
fn group_exists(group: u32) -> bool {
    Command::new("/bin/kill")
        .args(["-0", "--", &format!("-{group}")])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

/// Windows has no signal to forward, so the tree goes down in one step.
#[cfg(windows)]
pub(crate) fn stop_tree(child: &mut Child) -> io::Result<()> {
    Command::new("taskkill")
        .args(["/PID", &child.id().to_string(), "/T", "/F"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()?;
    Ok(())
}

#[cfg(unix)]
pub(crate) fn isolate(command: &mut Command) {
    use std::os::unix::process::CommandExt;

    command.process_group(0);
}

#[cfg(windows)]
pub(crate) fn isolate(command: &mut Command) {
    use std::os::windows::process::CommandExt;

    const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
    command.creation_flags(CREATE_NEW_PROCESS_GROUP);
}

/// Poll `child` for up to `timeout`, reaping it if it exits in that window.
#[cfg_attr(not(unix), allow(dead_code))]
fn wait_for_exit(child: &mut Child, timeout: Duration) -> io::Result<Option<ExitStatus>> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(Some(status));
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Ok(None);
        }
        sleep(remaining.min(POLL_INTERVAL));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A child that ignores SIGINT and holds a grandchild open, which is the
    /// shape `uv run` wrapping a server has. Killing the child alone would leave
    /// the grandchild behind.
    #[cfg(unix)]
    fn stubborn_tree() -> OwnedChild {
        OwnedChild::spawn(Command::new("sh").args(["-c", r#"trap "" INT; sleep 30 & wait"#]))
            .expect("failed to spawn the test tree")
    }

    /// A worker that ignores SIGINT under a leader that exits at once. Trusting
    /// the leader's exit would leave it serving the old build.
    #[cfg(unix)]
    fn lingering_worker_tree() -> OwnedChild {
        OwnedChild::spawn(Command::new("sh").args(["-c", r#"sh -c 'trap "" INT; sleep 30' &"#]))
            .expect("failed to spawn the test tree")
    }

    #[cfg(unix)]
    fn descendants_of(child: &OwnedChild) -> usize {
        let group = child.child.id().to_string();
        let out = Command::new("ps")
            .args(["-eo", "pgid="])
            .output()
            .expect("ps failed");
        String::from_utf8_lossy(&out.stdout)
            .split_whitespace()
            .filter(|pgid| *pgid == group)
            .count()
    }

    #[cfg(unix)]
    #[test]
    fn stop_takes_down_the_whole_tree() {
        let mut child = stubborn_tree();
        sleep(Duration::from_millis(300));
        assert!(
            descendants_of(&child) >= 2,
            "expected a child and a grandchild"
        );

        assert!(child.stop().unwrap().is_none(), "it was still running");

        assert_eq!(descendants_of(&child), 0);
    }

    /// Through `poll` with no interrupt, which is how `dev` sees a server that
    /// exited on its own. Reporting that exit without taking the group down
    /// leaves the workers holding the port after `mda dev` is gone.
    #[cfg(unix)]
    #[test]
    fn poll_takes_down_a_worker_that_outlives_its_leader() {
        let mut child = lingering_worker_tree();
        sleep(Duration::from_millis(300));
        assert!(descendants_of(&child) >= 1, "expected a lingering worker");

        let outcome = child.poll(&Interrupt::pending()).unwrap();

        assert!(matches!(outcome, Some(Outcome::Exited(_))));
        assert_eq!(descendants_of(&child), 0);
    }

    /// Members of the group still running. A killed leader stays a zombie until
    /// its owner reaps it, which is not the same as still running.
    #[cfg(unix)]
    fn running_members_of(group: u32) -> usize {
        let out = Command::new("ps")
            .args(["-eo", "pgid=,stat="])
            .output()
            .expect("ps failed");
        String::from_utf8_lossy(&out.stdout)
            .lines()
            .filter_map(|line| {
                let mut fields = line.split_whitespace();
                Some((fields.next()?.to_string(), fields.next()?.to_string()))
            })
            .filter(|(pgid, stat)| *pgid == group.to_string() && !stat.starts_with('Z'))
            .count()
    }

    /// What a termination signal runs while the main thread may be blocked:
    /// every group this process owns goes down without its `OwnedChild`.
    #[cfg(unix)]
    #[test]
    fn terminating_takes_down_every_registered_tree() {
        let mut first = stubborn_tree();
        let mut second = stubborn_tree();
        sleep(Duration::from_millis(300));
        let groups = [first.child.id(), second.child.id()];
        let registered = registered_groups();
        assert!(
            groups.iter().all(|group| registered.contains(group)),
            "spawned groups should be registered: {registered:?}"
        );

        terminate_groups(&groups);

        let deadline = Instant::now() + Duration::from_secs(3);
        while groups.iter().any(|group| running_members_of(*group) > 0) && Instant::now() < deadline
        {
            sleep(POLL_INTERVAL);
        }
        for group in groups {
            assert_eq!(running_members_of(group), 0, "group {group} survived");
        }

        first.stop().unwrap();
        second.stop().unwrap();
        let registered = registered_groups();
        assert!(
            groups.iter().all(|group| !registered.contains(group)),
            "stopped groups should be unregistered: {registered:?}"
        );
    }

    /// Termination snapshots the registry once, then spends a grace period on
    /// it. A child the main thread starts in that window — a `mda dev` restart,
    /// the next toolchain step — must not escape the snapshot and outlive us.
    #[cfg(unix)]
    #[test]
    fn a_group_started_after_termination_began_is_killed() {
        let registry: &'static SharedRegistry = Box::leak(Box::default());
        assert!(close(registry).is_empty());

        let mut command = Command::new("sh");
        command.args(["-c", r#"trap "" INT TERM; sleep 30 & wait"#]);
        isolate(&mut command);
        let mut late = command.spawn().expect("failed to spawn the test tree");
        let group = late.id();

        let registration = GroupRegistration::new_in(registry, &late);

        let deadline = Instant::now() + Duration::from_secs(3);
        while running_members_of(group) > 0 && Instant::now() < deadline {
            sleep(POLL_INTERVAL);
        }
        assert_eq!(running_members_of(group), 0, "the late group survived");
        assert!(lock(registry).groups.is_empty(), "a closed registry grew");

        drop(registration);
        let _ = late.wait();
    }

    #[cfg(unix)]
    #[test]
    fn an_interrupt_stops_a_running_child() {
        let mut child = stubborn_tree();
        let started = Instant::now();

        let outcome = child.wait(&Interrupt::requested()).unwrap();

        assert!(matches!(outcome, Outcome::Stopped));
        assert!(started.elapsed() < STOP_GRACE_PERIOD + Duration::from_secs(3));
    }

    /// Nothing asked it to stop, so the status is its own and reaches the caller.
    #[cfg(unix)]
    #[test]
    fn an_uninterrupted_child_reports_its_own_status() {
        let mut child = OwnedChild::spawn(Command::new("sh").args(["-c", "exit 3"]))
            .expect("failed to spawn the test child");

        let outcome = child.wait(&Interrupt::pending()).unwrap();

        match outcome {
            Outcome::Exited(status) => assert_eq!(status.code(), Some(3)),
            Outcome::Stopped => panic!("nothing stopped it"),
        }
    }

    /// So a crash racing a Ctrl-C is not reported as a clean stop.
    #[cfg(unix)]
    #[test]
    fn stopping_an_already_exited_child_returns_its_status() {
        let mut child = OwnedChild::spawn(Command::new("sh").args(["-c", "exit 3"]))
            .expect("failed to spawn the test child");
        wait_for_exit(&mut child.child, STOP_GRACE_PERIOD)
            .unwrap()
            .expect("the test child should have exited");

        let status = child.stop().unwrap().expect("it exited on its own");

        assert_eq!(status.code(), Some(3));
    }

    /// The same guarantee through `poll`, which is what an interrupted command
    /// actually calls.
    #[cfg(unix)]
    #[test]
    fn an_exited_child_keeps_its_status_over_an_interrupt() {
        let mut child = OwnedChild::spawn(Command::new("sh").args(["-c", "exit 3"]))
            .expect("failed to spawn the test child");
        wait_for_exit(&mut child.child, STOP_GRACE_PERIOD)
            .unwrap()
            .expect("the test child should have exited");

        let outcome = child.poll(&Interrupt::requested()).unwrap();

        match outcome {
            Some(Outcome::Exited(status)) => assert_eq!(status.code(), Some(3)),
            _ => panic!("expected the child's own status"),
        }
    }

    /// A leader that ignores SIGINT and keeps a grandchild alive — the shape of
    /// a hung `npm` wrapper. Killing only the leader would leave the grandchild
    /// holding stdout/stderr and hang the pipe-reader joins.
    #[cfg(unix)]
    #[test]
    fn a_capture_timeout_kills_a_tree_that_holds_pipes() {
        let mut command = Command::new("sh");
        command.args(["-c", r#"trap "" INT; sleep 30 & wait"#]);
        let started = Instant::now();
        let err = capture(
            &mut command,
            None,
            Some(Duration::from_secs(1)),
            &Interrupt::pending(),
        )
        .unwrap_err();
        assert!(
            err.to_string().contains("timed out"),
            "unexpected error: {err:#}"
        );
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "timeout path hung joining pipe readers ({:?})",
            started.elapsed()
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_capture_returns_what_the_child_wrote_and_read() {
        let mut command = Command::new("sh");
        command.args(["-c", "cat; echo oops >&2; exit 3"]);

        let output = capture(&mut command, Some(b"in"), None, &Interrupt::pending()).unwrap();

        assert_eq!(output.status.code(), Some(3));
        assert_eq!(output.stdout, b"in");
        assert_eq!(output.stderr, b"oops\n");
    }

    #[cfg(unix)]
    #[test]
    fn a_stderr_tail_keeps_the_end() {
        let mut command = Command::new("sh");
        command
            .args([
                "-c",
                "{ printf start; printf '%0100d' 0; printf end; } >&2; exit 1",
            ])
            .stderr(Stdio::piped());
        let mut owned = OwnedChild::spawn(&mut command).unwrap();
        let tail = StderrTail::tee(&mut owned, 10);

        owned.wait(&Interrupt::pending()).unwrap();

        assert_eq!(tail.finish(), b"0000000end");
    }

    #[cfg(unix)]
    #[test]
    fn an_interrupted_capture_is_a_cancellation() {
        let mut command = Command::new("sleep");
        command.arg("30");
        let started = Instant::now();

        let err = capture(&mut command, None, None, &Interrupt::requested()).unwrap_err();

        assert!(crate::error::is_cancellation(&err), "{err:#}");
        assert!(started.elapsed() < Duration::from_secs(5));
    }
}
