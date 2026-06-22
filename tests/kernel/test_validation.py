from kernel.registry import Registry
from kernel.turncommit import TurnCommit
from kernel.validation import validate_commit, build_repair_request
from kernel.contextsystem import ValidationError
from tests.kernel.fakes import FakeNoteSystem


def _reg():
    return Registry().register(FakeNoteSystem())


def test_valid_commit_has_no_errors():
    tc = TurnCommit.from_dict({"narration": "x", "notes": [{"text": "ok"}]})
    assert validate_commit(_reg(), tc, world={}) == []


def test_missing_field_surfaces_owner_error():
    tc = TurnCommit.from_dict({"notes": [{"text": ""}, {"text": "good"}]})
    errs = validate_commit(_reg(), tc, world={})
    assert len(errs) == 1 and errs[0].code == "missing" and errs[0].field == "[0].text"


def test_unknown_section_is_an_error():
    tc = TurnCommit.from_dict({"weather": {"rain": True}})
    errs = validate_commit(_reg(), tc, world={})
    assert len(errs) == 1 and errs[0].code == "unknown_section" and errs[0].section == "weather"


def test_build_repair_request_renders_hints():
    errs = [ValidationError("notes", "[0].text", "missing", "每条 note 需要非空 text")]
    msg = build_repair_request(errs)
    assert "notes" in msg and "[0].text" in msg and "需要非空 text" in msg
