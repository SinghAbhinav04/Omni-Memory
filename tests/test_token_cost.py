"""Token-cost guards: the two big recurring costs must stay contained —
(1) SessionEnd capture must NOT spawn `claude -p` on autopilot, and
(2) the per-prompt inject block must de-dupe instead of repeating every turn."""
from omni_memory import llm, session_memory as sm
import omni_memory.cli as cli


def test_autocapture_skips_agent_cli(monkeypatch):
    # inside Claude Code (provider == "agent"), unattended capture must stay free
    monkeypatch.setattr(llm, "provider", lambda: "agent")
    monkeypatch.delenv("OMNI_HEADLESS_LLM", raising=False)
    assert llm.autocapture_ok() is False
    monkeypatch.setenv("OMNI_HEADLESS_LLM", "1")
    assert llm.autocapture_ok() is True             # explicit opt-in
    monkeypatch.delenv("OMNI_HEADLESS_LLM", raising=False)
    monkeypatch.setattr(llm, "provider", lambda: "gemini")
    assert llm.autocapture_ok() is True             # a real API key is a genuine headless setup
    monkeypatch.setattr(llm, "provider", lambda: None)
    assert llm.autocapture_ok() is False


def test_capture_uses_heuristic_under_agent(store, repo, monkeypatch):
    monkeypatch.setattr(llm, "provider", lambda: "agent")
    called = {"n": 0}
    monkeypatch.setattr(llm, "extract_memories",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [])
    # a raw transcript (not JSON) → would previously hit the model; now heuristic
    sm.capture_from_json(store, repo, "we decided to use Redis for the rate limiter.",
                         source="session")
    assert called["n"] == 0                          # never called `claude -p`


def test_inject_dedupes_identical_block(store):
    block = "=== VERIFIED PROJECT MEMORY ===\n[abc] fact: one\n"
    assert cli._should_inject(store, block) is True   # first time → inject
    assert cli._should_inject(store, block) is False  # unchanged → suppress
    assert cli._should_inject(store, block + "[xyz] fact: two\n") is True  # changed → inject


def test_inject_periodic_refresh(store):
    block = "stable block"
    cli._should_inject(store, block)                  # seed
    fired = sum(1 for _ in range(cli._INJECT_REFRESH_EVERY) if cli._should_inject(store, block))
    assert fired >= 1                                 # re-emits at least once within the window
