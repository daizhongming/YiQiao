import mem0


def test_root_exports_only_self_hosted_memory_classes():
    assert mem0.__all__ == ["AsyncMemory", "Memory"]
    assert mem0.AsyncMemory is not None
    assert mem0.Memory is not None
    assert not hasattr(mem0, "AsyncMemoryClient")
    assert not hasattr(mem0, "MemoryClient")
