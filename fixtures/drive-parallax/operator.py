"""Simulated operator: drives the local Parallax checkout from `guide` and `--json` output alone.

No skill, no prior knowledge of flags. It reads the workflow map, builds each command from the
flags the guide lists, asks a stand-in human each reference-gate question, and stops at the
storyboard review boundary. It never runs a generation command; the one `produce` probe is
expected to be refused.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable

import yaml

REPO = Path(__file__).resolve().parents[2]
LOCAL_PARALLAX = ["uv", "run", "--frozen", "--project", str(REPO), "parallax"]


class Operator:
    def __init__(self, folder: Path, human: Callable[[dict], dict], env: dict[str, str], parallax: list[str] = LOCAL_PARALLAX):
        self.folder, self.human, self.env, self.parallax = folder, human, env, parallax
        self.transcript: list[dict] = []

    def run(self, argv: list[str]) -> subprocess.CompletedProcess:
        result = subprocess.run([*self.parallax, *argv], cwd=self.folder, env=self.env, capture_output=True, text=True)
        self.transcript.append({"argv": argv, "rc": result.returncode, "stderr": result.stderr[-400:]})
        return result

    def build(self, command: dict, known: dict[str, str]) -> list[str]:
        """argv for a guide command: its name, then every flag the guide says to pass."""
        argv = command["command"].split()[1:]
        listed = {f["name"].split("/")[0]: f for f in command["flags"]}
        for name in command["pass"]:
            assert name in listed, f"guide says to pass {name} but does not list it for {command['command']}"
            if listed[name]["is_flag"]:
                argv.append(name)
            else:
                assert name in known, f"operator has no value for {name} ({command['command']})"
                argv += [name, known[name]]
        return argv

    def drive(self) -> dict:
        assert "guide" in self.run(["--help"]).stdout, "`parallax --help` must advertise guide"
        guide = json.loads(self.run(["guide"]).stdout)
        stages = {s["id"]: s for s in guide["stages"]}
        brief = self.folder / "brief.yaml"
        known = {"--folder": str(self.folder), "--brief": str(brief)}

        brief.write_text(yaml.safe_dump(self.author_brief(self.folder / "script.md"), sort_keys=False))
        validate = next(c for c in stages["brief"]["commands"] if c["command"] == "parallax validate")
        assert self.run(self.build(validate, known)).returncode == 0

        storyboard = stages["storyboard"]["commands"][0]
        argv = self.build(storyboard, known)
        manifest = json.loads(self.run(argv).stdout)
        first_status = manifest["status"]

        produce = next(c for c in stages["produce"]["commands"] if c["command"] == "parallax produce")
        refused = None
        if manifest["gates"]:  # only probe while a gate is open, so a refusal is guaranteed and nothing can spend
            probe = self.run(self.build(produce, known))
            refused = probe.returncode != 0 and "reference gate" in probe.stderr

        answers = []
        resolve = stages["reference_gates"]["commands"][0]
        assert resolve["command"] == "parallax reference resolve"
        while manifest["gates"]:
            gate = manifest["gates"][0]
            answer = self.human(gate)
            choice = next(c for c in gate["choices"] if c["id"] == answer["choice"])
            tokens = [answer["fill"].get(t, t) for t in shlex.split(choice["command"])]
            assert tokens[0] == "parallax", choice["command"]
            assert not any(t.startswith("<") and t.endswith(">") for t in tokens), f"unfilled placeholder in {tokens}"
            result = self.run(tokens[1:])
            assert result.returncode == 0, result.stderr
            answers.append({"gate": gate["id"], "choice": choice["id"]})
            manifest = json.loads(self.run(argv).stdout)
        return {"first_status": first_status, "final_status": manifest["status"], "answers": answers,
                "produce_refused_while_gates_open": refused, "provider_calls": manifest["provider_calls"],
                "package_dir": manifest["package_dir"], "stopped_at": "storyboard_review"}

    @staticmethod
    def author_brief(script: Path) -> dict:
        headline, body = "", []
        for block in script.read_text().split("\n\n"):
            block = block.strip()
            if block.startswith("Headline:"):
                headline = block.removeprefix("Headline:").strip()
            elif block and not block.startswith("Body:"):
                body.append(block)
        return {
            "goal": "Short social ad from the supplied script",
            "headline": headline,
            "script": {"scenes": [
                {"index": i, "vo_text": text, "prompt": f"Visual for: {text}"} for i, text in enumerate(body)
            ]},
        }
