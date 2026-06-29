#!/usr/bin/env python3
"""Dialogue simulator: generates realistic human/AI conversations, feeds
structured summaries through evaluate_core (with full dedup), and copies
results to fakes.jsonl for dashboard viewing under the "Fake Data" toggle.

Direct mode calls evaluate_core() in-process (full dedup via log_core).
Pipeline mode routes each summary through `mnemoq --evaluate-file`.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

# --- Sibling imports (scripts/ is not a package) ---
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_fakes import COMMON_SLOTS, DOMAIN_POOLS, build_ctx  # noqa: E402

from agent_memory.engine.evaluate import (  # noqa: E402
    detect_bug_fixed,
    detect_decision,
    detect_explicit_remember,
    detect_human_correction,
    detect_workaround,
    evaluate_core,
)

# --- Scenario templates ---

OUTCOMES = ["correction", "preference", "bug_fixed", "decision", "workaround", "none"]
OUTCOME_WEIGHTS = [20, 15, 20, 15, 10, 20]

SCENARIO_TEMPLATES = {
    "correction": {
        "human": [
            "No, don't use {anti_pattern} — always use {pattern} for {component}",
            "Stop, {anti_pattern} is wrong for {component}. Use {pattern} instead",
            "Wait, that's {anti_pattern}. You should always {operation} with {component}",
        ],
        "agent": [
            "Understood. I'll switch to {pattern} for {component}.",
            "Got it — replacing {anti_pattern} with {pattern} on {component}.",
        ],
    },
    "preference": {
        "human": [
            "Remember to always {operation} for {component}",
            "Never use {anti_pattern} with {component}",
            "Don't forget to {operation} when working on {component}",
        ],
        "agent": [
            "Noted. I'll always {operation} for {component} from now on.",
            "Understood — avoiding {anti_pattern} on {component}.",
        ],
    },
    "bug_fixed": {
        "human": [
            "The {component} is throwing {error_type} when {operation}",
            "I'm seeing {error_type} from {component} during {operation}",
        ],
        "agent": [
            "Fixed. The {error_type} in {component} was caused by {root_cause}.",
            "Resolved — {component} now handles {error_type} during {operation}.",
        ],
    },
    "decision": {
        "human": [
            "Let's use {pattern} for {component} going forward",
            "We should {operation} with {pattern} on {component}",
        ],
        "agent": [
            "Agreed. Implementing {pattern} for {component}.",
            "Sounds good — {pattern} it is for {component}.",
        ],
    },
    "workaround": {
        "human": [
            "Just pin {component} for now, we'll fix {root_cause} later",
            "Let's workaround {root_cause} on {component} temporarily",
        ],
        "agent": [
            "OK, applying the workaround on {component} for now.",
            "Done — {component} is pinned as a temporary measure.",
        ],
    },
    "none": {
        "human": [
            "Can you check {component}?",
            "Looks good, proceed with {operation}",
            "What does {component} do during {operation}?",
            "Approved — continue with {operation} on {component}",
        ],
        "agent": [
            "Sure, checking {component} now.",
            "Proceeding with {operation}.",
            "{component} handles {operation} as expected.",
        ],
    },
}

DETECTORS = [
    detect_human_correction,
    detect_explicit_remember,
    detect_bug_fixed,
    detect_decision,
    detect_workaround,
]


# --- Slot filling (same approach as generate_fakes._fill_templates) ---

def _make_slot_filler(domain, vocab, rng, components):
    slots = {}

    def fill(name):
        if name not in slots:
            if name == "component":
                slots[name] = rng.choice(components)
            elif name == "domain":
                slots[name] = domain
            elif name in vocab:
                slots[name] = rng.choice(vocab[name])
            elif name in COMMON_SLOTS:
                slots[name] = rng.choice(COMMON_SLOTS[name])
            else:
                slots[name] = f"{{{name}}}"
        return slots[name]

    return fill, slots


def _fill_template(template, fill):
    return re.sub(r"\{(\w+)\}", lambda m: fill(m.group(1)), template)


def _build_summary_fields(outcome, slots):
    fields = {}
    if outcome == "correction":
        fields["corrected_action"] = f"use {slots['pattern']} for {slots['component']}"
        fields["rejected_action"] = slots["anti_pattern"]
        fields["text"] = f"corrected {slots['component']} to use {slots['pattern']}"
    elif outcome == "preference":
        fields["text"] = f"always {slots['operation']} for {slots['component']}"
    elif outcome == "bug_fixed":
        fields["error_text"] = f"{slots['error_type']} when {slots['operation']}"
        fields["text"] = f"fixed {slots['error_type']} in {slots['component']}"
    elif outcome == "decision":
        fields["text"] = f"use {slots['pattern']} for {slots['component']} going forward"
    elif outcome == "workaround":
        fields["text"] = f"pin {slots['component']} as workaround for {slots['root_cause']}"
    return fields


# --- Subagents ---

class HumanSubagent:
    def __init__(self, rng, domain):
        self.rng = rng
        self.domain = domain

    def generate_prompt(self, step):
        outcome = self.rng.choices(OUTCOMES, weights=OUTCOME_WEIGHTS)[0]
        pool = DOMAIN_POOLS[self.domain]
        vocab = pool["vocab"]
        components = self.rng.sample(pool["components"], k=self.rng.randint(1, min(2, len(pool["components"]))))
        files_touched = self.rng.sample(pool["files_touched"], k=self.rng.randint(1, min(2, len(pool["files_touched"]))))

        fill, slots = _make_slot_filler(self.domain, vocab, self.rng, components)
        human_tmpl = self.rng.choice(SCENARIO_TEMPLATES[outcome]["human"])
        human_text = _fill_template(human_tmpl, fill)

        # Pre-fill all slots that _build_summary_fields might need
        for name in ("component", "pattern", "anti_pattern", "operation", "error_type", "root_cause"):
            fill(name)

        prompt = {
            "outcome": outcome,
            "human_text": human_text,
            "components": components,
            "files_touched": files_touched,
        }
        prompt.update(_build_summary_fields(outcome, slots))
        return prompt


class AgentSubagent:
    def __init__(self, rng, domain):
        self.rng = rng
        self.domain = domain

    def respond(self, human_prompt):
        pool = DOMAIN_POOLS[self.domain]
        vocab = pool["vocab"]
        outcome = human_prompt["outcome"]
        fill, _ = _make_slot_filler(self.domain, vocab, self.rng, human_prompt["components"])
        agent_tmpl = self.rng.choice(SCENARIO_TEMPLATES[outcome]["agent"])
        agent_text = _fill_template(agent_tmpl, fill)
        return {"response_text": agent_text}


# --- Dialogue runner ---

class DialogueRunner:
    def __init__(self, turns, domain, seed, ctx, paths):
        self.turns = turns
        self.domain = domain
        self.rng = random.Random(seed)
        self.ctx = ctx
        self.paths = paths

    def _build_summary(self, step, human_prompt, agent_response):
        summary = {
            "step": step,
            "prompt_type": "human",
            "outcome": human_prompt["outcome"],
            "components": human_prompt["components"],
            "files_touched": human_prompt["files_touched"],
        }
        for key in ("text", "corrected_action", "rejected_action", "error_text"):
            if key in human_prompt:
                summary[key] = human_prompt[key]
        return summary

    def _dry_run_evaluate(self, summary):
        signals = []
        for detector in DETECTORS:
            result = detector(summary, self.ctx)
            if result is not None:
                signals.append(result)
        signals.sort(key=lambda x: x[0], reverse=True)
        max_per_turn = self.ctx.get("evaluate_max_per_turn", 3)
        signals = signals[:max_per_turn]
        return {
            "exit_code": 0,
            "status": "ok",
            "signals_detected": len(signals),
            "auto_logged": [],
            "suggestions": [{"confidence": c, "candidate": cand} for c, cand in signals],
            "skipped_invalid": [],
        }

    def _write_transcript_turn(self, path, step, domain, human_prompt, agent_response, summary, result):
        record = {
            "step": step,
            "domain": domain,
            "human": human_prompt["human_text"],
            "agent": agent_response["response_text"],
            "summary": summary,
            "evaluate_result": {
                "signals_detected": result.get("signals_detected", 0),
                "auto_logged": result.get("auto_logged", []),
                "suggestions": result.get("suggestions", []),
            },
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _copy_to_fakes(self):
        shutil.copy2(self.paths.learnings_path, os.path.join(self.paths.memory_dir, "fakes.jsonl"))

    def run(self, mode="direct", to_fakes=False, clean=False, dry_run=False, transcript_path=None):
        if clean and not dry_run and os.path.exists(self.paths.learnings_path):
            os.remove(self.paths.learnings_path)

        if transcript_path and os.path.exists(transcript_path):
            os.remove(transcript_path)

        total_signals = 0
        total_auto_logged = 0
        total_suggestions = 0
        pipeline_successes = 0
        pipeline_failures = 0

        for step in range(1, self.turns + 1):
            domain = self.domain or self.rng.choice(sorted(DOMAIN_POOLS))
            human = HumanSubagent(self.rng, domain)
            agent = AgentSubagent(self.rng, domain)

            human_prompt = human.generate_prompt(step)
            agent_response = agent.respond(human_prompt)
            summary = self._build_summary(step, human_prompt, agent_response)

            if mode == "direct":
                if dry_run:
                    result = self._dry_run_evaluate(summary)
                else:
                    result = evaluate_core(summary, self.paths, self.ctx)
            elif mode == "pipeline":
                if dry_run:
                    result = self._dry_run_evaluate(summary)
                else:
                    result = self._run_pipeline_turn(summary)
                    if result.get("exit_code") == 0:
                        pipeline_successes += 1
                    else:
                        pipeline_failures += 1

            total_signals += result.get("signals_detected", 0)
            total_auto_logged += len(result.get("auto_logged", []))
            total_suggestions += len(result.get("suggestions", []))

            if transcript_path:
                self._write_transcript_turn(transcript_path, step, domain, human_prompt, agent_response, summary, result)

        if mode == "direct" and to_fakes and not dry_run:
            self._copy_to_fakes()

        print(f"Dialogue: {self.turns} turns")
        print(f"Signals detected: {total_signals}")
        print(f"Auto-logged: {total_auto_logged}")
        print(f"Suggestions: {total_suggestions}")
        if mode == "pipeline" and not dry_run:
            print(f"Pipeline: {pipeline_successes} succeeded, {pipeline_failures} failed")

    def _run_pipeline_turn(self, summary):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False)
            tmp_path = f.name
        try:
            cmd = [sys.executable, "-m", "agent_memory.cli", "--evaluate-file", tmp_path]
            cwd = self.paths.repo_root
            if not os.path.isdir(os.path.join(cwd, "agent_memory")) and not os.path.isdir(os.path.join(cwd, "src", "agent_memory")):
                cwd = os.getcwd()
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
            if result.returncode == 0:
                signals = self._parse_pipeline_stdout(result.stdout)
                return {"exit_code": 0, "status": "ok", **signals}
            else:
                print(f"FAILURE: {result.stderr.strip()[:200]}", file=sys.stderr)
                return {"exit_code": 1, "status": "error", "signals_detected": 0, "auto_logged": [], "suggestions": [], "skipped_invalid": []}
        finally:
            os.unlink(tmp_path)

    @staticmethod
    def _parse_pipeline_stdout(stdout):
        """Extract signal counts from mnemoq --evaluate-file verbose output."""
        signals = {"signals_detected": 0, "auto_logged": [], "suggestions": [], "skipped_invalid": []}
        for line in stdout.splitlines():
            if line.startswith("Signals detected:"):
                try:
                    signals["signals_detected"] = int(line.split(":")[1].strip())
                except (ValueError, IndexError):
                    pass
            elif line.startswith("Auto-logged:"):
                try:
                    count = int(line.split(":")[1].strip())
                    signals["auto_logged"] = [{"status": "added"}] * count
                except (ValueError, IndexError):
                    pass
            elif line.startswith("Suggested:"):
                try:
                    count = int(line.split(":")[1].strip())
                    signals["suggestions"] = [{"confidence": 0.0, "candidate": {}}] * count
                except (ValueError, IndexError):
                    pass
        return signals


# --- CLI ---

def build_parser():
    parser = argparse.ArgumentParser(
        description="Simulate human/AI dialogues and feed summaries through evaluate_core.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/sim_dialogue.py --turns 20 --direct --to-fakes --clean --confirm
  python scripts/sim_dialogue.py --turns 50 --pipeline --confirm
  python scripts/sim_dialogue.py --turns 10 --direct --dry-run --seed 42
  python scripts/sim_dialogue.py --turns 30 --direct --domain ui --seed 42 --to-fakes
        """,
    )
    parser.add_argument("--turns", type=int, default=20, help="Number of dialogue turns to simulate")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--direct", action="store_true", help="In-process mode: calls evaluate_core() directly")
    mode.add_argument("--pipeline", action="store_true", help="Subprocess mode: pipes each summary through mnemoq --evaluate-file")
    parser.add_argument("--to-fakes", action="store_true", help="After run, copy learnings.jsonl -> fakes.jsonl (direct mode only)")
    parser.add_argument("--clean", action="store_true", help="Delete learnings.jsonl before running (requires --confirm)")
    parser.add_argument("--transcript", type=str, help="Path to write human-readable dialogue transcript JSONL")
    parser.add_argument("--domain", type=str, help="Restrict to one domain (ui, data, tooling, etc.)")
    parser.add_argument("--seed", type=int, help="Random seed for reproducible dialogue")
    parser.add_argument("--memory-dir", type=str, help="Memory directory (passed to mnemoq)")
    parser.add_argument("--confirm", action="store_true", help="Required for --pipeline without --dry-run AND for --clean in any mode")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without writing anything")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.direct and not args.pipeline:
        parser.error("must specify --direct or --pipeline")
    if args.to_fakes and not args.direct:
        parser.error("--to-fakes requires --direct")
    if args.clean and not args.confirm:
        parser.error("--clean requires --confirm (safety guard against destroying real learnings.jsonl)")
    if args.pipeline and not args.dry_run and not args.confirm:
        parser.error("--pipeline writes to learnings.jsonl — use --confirm to proceed or --dry-run to preview")

    if args.turns <= 0:
        parser.error("--turns must be positive")

    if args.domain and args.domain not in DOMAIN_POOLS:
        parser.error(f"--domain must be one of: {', '.join(sorted(DOMAIN_POOLS))}")

    try:
        ctx, paths = build_ctx(args.memory_dir)
    except (ValueError, TypeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if not ctx.get("evaluate_enabled", True):
        print("ERROR: evaluate_enabled is false in config — simulator would produce nothing.", file=sys.stderr)
        print("Set evaluate_enabled: true in memory/config.json to proceed.", file=sys.stderr)
        sys.exit(1)

    mode = "direct" if args.direct else "pipeline"
    runner = DialogueRunner(
        turns=args.turns,
        domain=args.domain,
        seed=args.seed,
        ctx=ctx,
        paths=paths,
    )
    runner.run(
        mode=mode,
        to_fakes=args.to_fakes,
        clean=args.clean,
        dry_run=args.dry_run,
        transcript_path=args.transcript,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
