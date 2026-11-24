def pytest_configure(config):
    config.addinivalue_line("markers", "slow: runs perl.wasm many times; skipped by -m 'not slow'")
