//! One mapper per Fleet feature, run in this order so the report is stable.
//!
//! A mapper that cannot carry its feature over yet still detects it and lists
//! it as not exported, so export never silently drops part of an agent.

mod channels;
mod files;
mod instructions;
mod memory;
mod middleware;
mod model;
mod notices;
mod sandbox;
mod schedules;
mod skills;
mod subagents;
mod tools;

use super::Mapper;

pub(super) use tools::managed_tools_urls;

pub(super) const ALL: &[Mapper] = &[
    instructions::map,
    model::map,
    sandbox::map,
    // Before tools, which fills in each subagent's MCP and approvals.
    subagents::map,
    tools::map,
    skills::map,
    // After every mapper that claims files except the catch-all.
    memory::map,
    // After every mapper that claims files.
    files::map,
    schedules::map,
    channels::map,
    middleware::map,
    notices::map,
];
