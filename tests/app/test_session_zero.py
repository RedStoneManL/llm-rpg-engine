from app.session_zero import run_session_zero


def _run(spec, lines):
    out = []
    result = run_session_zero(spec, inputs=iter(lines), out=out.append, interactive=True)
    return result, "\n".join(out)


def test_asks_until_required_filled():
    # genre then name provided
    spec, _ = _run({}, ["日式西幻", "凛"])
    assert spec["world_premise"]["genre"] == "日式西幻"
    assert spec["protagonist"]["name"] == "凛"


def test_already_satisfied_required_not_reasked():
    # genre already present -> only the missing protagonist is asked (one prompt).
    out = []
    result = run_session_zero(
        {"world_premise": {"genre": "x"}},
        inputs=iter(["凛"]), out=out.append, interactive=True)
    assert result["protagonist"]["name"] == "凛"
    assert result["world_premise"]["genre"] == "x"
    prompts = [line for line in out if line.endswith("：")]
    assert len(prompts) == 1     # genre satisfied -> not re-asked


def test_delegate_token_leaves_part_absent():
    spec, _ = _run({"protagonist": {"name": "凛"}}, ["/auto"])  # genre delegated
    assert "world_premise" not in spec or "genre" not in spec.get("world_premise", {})


def test_non_interactive_returns_unchanged():
    base = {"protagonist": {"name": "凛"}}
    result = run_session_zero(base, inputs=iter([]), out=lambda *_: None, interactive=False)
    assert result == base
