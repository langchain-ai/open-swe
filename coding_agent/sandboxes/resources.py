"""VM sizing for a new sandbox, as provider create kwargs."""

from typing import TypedDict


class SandboxResources(TypedDict, total=False):
    mem_bytes: int
    vcpus: int
    fs_capacity_bytes: int
