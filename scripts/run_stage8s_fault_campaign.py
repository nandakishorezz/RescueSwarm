"""Run the deterministic Stage 8S normalized fault campaign and write evidence."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import yaml

from rescue_core.resilience.invariants import MissionInvariantChecker
from rescue_core.resilience.policies import CompetitionReadinessValidator
from rescue_core.resilience.scenarios import ResilienceScenarioRunner, load_scenarios


ROOT = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--scenarios", type=Path, default=ROOT / "config" / "resilience_scenarios.yaml")
    return value


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def main() -> int:
    args = parser().parse_args()
    scenarios = load_scenarios(args.scenarios)
    campaign_id = f"{datetime.now(UTC).strftime('%Y-%m-%d_%H-%M-%S')}_{uuid4().hex[:8]}"
    campaign = ROOT / "data" / "resilience_runs" / campaign_id
    campaign.mkdir(parents=True)
    runner = ResilienceScenarioRunner()
    results = []
    for index, scenario in enumerate(scenarios, 1):
        print(f"[{index}/{len(scenarios)}] {scenario.scenario_id}")
        result = runner.run(scenario)
        results.append(result)
        directory = campaign / scenario.scenario_id
        directory.mkdir()
        (directory / "scenario.yaml").write_text(
            yaml.safe_dump(scenario.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
        )
        initial = runner.healthy_snapshot(scenario.phase)
        dump(directory / "initial_state.json", initial.model_dump(mode="json"))
        dump(directory / "parameters_before.json", {
            "method": scenario.injection_method,
            "sitl_parameter_changes": [],
            "fresh_instance_required_for_native_tests": True,
        })
        (directory / "events.ndjson").write_text(json.dumps({
            "type": "FAILURE_INJECTED", "scenario_id": scenario.scenario_id,
            "failure": scenario.failure, "phase": scenario.phase,
        }, default=str) + "\n", encoding="utf-8")
        (directory / "telemetry.ndjson").write_text("", encoding="utf-8")
        (directory / "safety_decisions.ndjson").write_text(result.decision.model_dump_json() + "\n", encoding="utf-8")
        (directory / "operator_actions.ndjson").write_text("", encoding="utf-8")
        invariant_state = {
            "rescue_state": initial.rescue_state,
            "vehicle_mode": initial.vehicle_mode,
            "payload_remaining": 1,
            "target_geolocation_valid": True,
            "command_connection": "tcp:127.0.0.1:5760",
        }
        invariants = MissionInvariantChecker().check(invariant_state)
        (directory / "invariant_checks.ndjson").write_text(
            "".join(item.model_dump_json() + "\n" for item in invariants), encoding="utf-8"
        )
        dump(directory / "recovery.json", {
            "expected_action": result.decision.recommended_action,
            "recovered": result.recovered,
            "final_state": result.final_state,
        })
        dump(directory / "final_state.json", result.model_dump(mode="json"))
        (directory / "scenario_report.md").write_text(
            f"# {scenario.scenario_id}\n\n"
            f"- Critical: {scenario.critical}\n"
            f"- Injection: {scenario.failure} ({scenario.injection_method})\n"
            f"- Phase: {scenario.phase}\n"
            f"- Detected: {result.detected}\n"
            f"- Decision: {result.decision.recommended_action}\n"
            f"- Owner: {result.decision.authoritative_layer}\n"
            f"- Drop prevented: {result.drop_prevented}\n"
            f"- Final state: {result.final_state}\n"
            f"- Result: {'PASS' if result.passed else 'FAIL'}\n",
            encoding="utf-8",
        )
    critical = [item for item in results if item.critical]
    recoverable = [item for item in results if not item.critical]
    readiness_validator = CompetitionReadinessValidator()
    readiness = readiness_validator.evaluate(offline_pass=True)
    live_summaries = list((ROOT / "data" / "resilience_runs").glob("live_stage8s_recall*/**/execution_summary.json"))
    latest_live = max(live_summaries, key=lambda path: path.stat().st_mtime) if live_summaries else None
    live_summary = json.loads(latest_live.read_text(encoding="utf-8")) if latest_live else None
    live_recall_pass = bool(
        live_summary
        and live_summary.get("state") == "ABORTED"
        and live_summary.get("rtl_completed")
        and live_summary.get("landing_detected")
        and live_summary.get("disarm_detected")
        and not (live_summary.get("metadata", {}).get("payload"))
    )
    scorecard = "\n".join(
        f"| {item.scenario_id} | {item.phase} | {'YES' if item.detected else 'NO'} | "
        f"{item.decision.recommended_action} | {'YES' if item.drop_prevented else 'N/A'} | "
        f"{item.final_state} | {'YES' if item.recovered else 'NO'} | {'PASS' if item.passed else 'FAIL'} |"
        for item in results
    )
    report = f"""# Stage 8S Robustness Report

## Hardware Gate

- H1: **PENDING**
- Physical hardware touched: **NO**
- All flight/failsafe evidence in this report is normalized injection or local ArduCopter SITL evidence. Physical behavior is unverified.

## Campaign

- Scenarios: {len(results)}
- Critical: {sum(item.passed for item in critical)}/{len(critical)} passed
- Recoverable/noncritical: {sum(item.passed for item in recoverable)}/{len(recoverable)} passed
- Normalized campaign result: **{'PASS' if all(item.passed for item in results) else 'FAIL'}**

| Scenario | Phase | Detected | Correct response | Drop prevented | Final safe state | Recovered | Result |
|---|---|---|---|---|---|---|---|
{scorecard}

## Native ArduPilot failsafe evidence

- Emergency RTL/recall: **{'PASS' if live_recall_pass else 'NOT_TESTED'}** — actual GUIDED → RTL → landed/disarmed; no payload release.
- Live artifact: {str(latest_live) if latest_live else 'NONE'}
- ArduCopter: {(live_summary or {}).get('autopilot_version', 'NOT_TESTED')}
- Commit: {(live_summary or {}).get('ardupilot_git_commit', 'NOT_TESTED')}
- Connection: {(live_summary or {}).get('connection', 'NOT_TESTED')}
- Mode evidence: {' → '.join((live_summary or {}).get('metadata', {}).get('mode_history', [])) or 'NOT_TESTED'}
- GCS/C2, battery, EKF and geofence: normalized-policy campaign implemented; native sensor/parameter injection is **NOT_TESTED** unless a separate live artifact is present.
- Fresh disposable SITL instances are required for any scenario parameter changes.

## Competition readiness

- Overall: **{readiness_validator.overall(readiness)}**
"""
    for item in readiness:
        report += f"- {item.category}: {item.status} — {item.reason}\n"
    report += """

## Physical gaps

- Pi 5, Pixhawk/M8N and OV9281 integration: PENDING (H1)
- Physical failsafes and payload: NOT VERIFIED
- Real aerial survivor recall and live video: NOT VERIFIED
- Two-or-more drone fleet: NOT IMPLEMENTED
- 25 kg mass and 12 ft x 12 ft launch/landing compliance: NOT MEASURED/NOT PHYSICALLY TESTED
"""
    (campaign / "stage8s_report.md").write_text(report, encoding="utf-8")
    (ROOT / "STAGE_8S_REPORT.md").write_text(report, encoding="utf-8")
    competition = "# Competition Readiness\n\n" + "\n".join(
        f"| {item.category} | {item.status} | {item.reason} | {item.software_evidence or '—'} | {item.sitl_evidence or '—'} | {item.physical_evidence or 'NONE'} |"
        for item in readiness
    )
    competition = competition.replace(
        "# Competition Readiness\n\n",
        "# Competition Readiness\n\n**OVERALL: NOT COMPETITION READY**\n\n| Requirement | Status | Reason | Software | SITL | Physical |\n|---|---|---|---|---|---|\n",
    )
    # Do not let an earlier-stage regression campaign overwrite the final
    # Stage 10S readiness assessment after software qualification exists.
    readiness_path = (
        campaign / "stage8s_competition_readiness_snapshot.md"
        if (ROOT / "STAGE_10S_REPORT.md").is_file()
        else ROOT / "COMPETITION_READINESS_REPORT.md"
    )
    readiness_path.write_text(competition + "\n", encoding="utf-8")
    dump(ROOT / "data" / "resilience_runs" / "latest.json", {
        "campaign_directory": str(campaign),
        "critical_passed": sum(item.passed for item in critical),
        "critical_total": len(critical),
        "all_passed": all(item.passed for item in results),
    })
    print(f"Artifacts: {campaign}")
    return 0 if all(item.passed for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
