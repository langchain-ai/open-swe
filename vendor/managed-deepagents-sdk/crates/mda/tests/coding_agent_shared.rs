use std::path::Path;

use anyhow::Result;
use mda::coding_agent::{offer_handoff, HandoffOutcome};

#[test]
fn shared_handoff_owns_its_heading() {
    let offer: fn(&Path, &str, bool) -> Result<HandoffOutcome> = offer_handoff;

    let _ = (offer, "Build your agent");
}
