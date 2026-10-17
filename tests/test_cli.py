import json

import main as cli
from conftest import make_fake_llm
from software_team.graph import build_team_graph


def test_cli_runs_and_saves_outputs(tmp_path, monkeypatch, capsys):
    llm = make_fake_llm(["product_manager", "developer", "reviewer", "FINISH"])
    graph = build_team_graph(llm_factory=lambda name: llm, supervisor_mode="llm")
    monkeypatch.setattr(cli, "build_team_graph", lambda: graph)

    exit_code = cli.main(["Add a password reset feature", "--output-dir", str(tmp_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[supervisor] -> product_manager" in out
    assert "Reviewer verdict: approved" in out

    run_folder = next(tmp_path.iterdir())
    for name in ("spec.md", "code.md", "review.md", "run.json"):
        assert (run_folder / name).exists()
    info = json.loads((run_folder / "run.json").read_text(encoding="utf-8"))
    assert info["verdict"] == "approved"
    assert len(info["history"]) == 7


def test_cli_reports_missing_api_key(monkeypatch, capsys):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("software_team.llm.get_settings", lambda: _no_key_settings())
    assert cli.main(["anything"]) == 1
    assert "GROQ_API_KEY" in capsys.readouterr().out


def _no_key_settings():
    from software_team.config import Settings

    return Settings(None, "m", 0.2, "llm", {})
