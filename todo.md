# TODO

Open design gaps from the Layer 2 design session (see
`docs/arch/peripheral-simulation-backend-architecture.md`).

## Layer 2 wiring
- [ ] Config Manager: pure-Python, parses SoC + Mock List YAML -> `TestContext`
- [ ] Test Engine: drive `robot.run()` in-process (not CLI) for live-object injection
- [ ] Runtime registry: zero-arg keyword libs read live instances from it
- [ ] Keyword libs must call only abstract peripheral interfaces, never concrete ABIs

## Multi-peripheral concurrency
- [ ] No locking around shared `TargetGateway` for concurrent peripherals
- [ ] No pump/scheduling model for servicing peripherals concurrently
- [ ] Config Manager must validate MMB addresses don't collide across peripherals

## SoC generality
- [ ] Capabilities not wired to RF skip/tags yet (`target/capabilities.py`)
- [ ] Timeouts should be config-driven per SoC/backend, not hardcoded in scripts

## Modularity
- [ ] CDC/ABC split is convention-only, unenforced — add an import-linter/arch test
t