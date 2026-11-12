"""Caller-scoped memory with MDA's default private-conversation policy."""

from managed_deepagents import MemoryLayer, define_memory

memory = define_memory(user=MemoryLayer())
