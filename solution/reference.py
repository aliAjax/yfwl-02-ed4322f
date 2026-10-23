#!/usr/bin/env python3
"""Reference solver for the thermal-network inverse handover task.

The unlabeled branches expose only return-temperature traces. The plant
observation supplies aggregate heat, supply temperature and return temperature
histories. A branch is modeled as a delayed, attenuated copy of its assigned
terminal load-temperature trace. Pairwise nonlinear fits provide a cost matrix;
a bitmask assignment closes the correspondence without enumerating
permutations. Aggregate heat and return histories then validate and refine the
mapping. Pump selection uses the recovered branch identities and a second
bitmask DP under the shared trunk-flow limit.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


TOP_KEYS = {
    "schema_version",
    "run_id",
    "network_id",
    "terminal_demands",
    "branch_records",
    "aggregate_observations",
    "pump_options",
    "trunk_limit_m3h",
}
TERMINAL_KEYS = {
    "terminal_id",
    "flow_profile",
    "load_temp_profile",
    "required_flow_m3h",
    "static_pressure_pa",
}
BRANCH_KEYS = {"record_id", "return_temp", "pressure_pa"}
AGGREGATE_KEYS = {"heat_flow", "supply_temp", "return_temp"}
OPTION_KEYS = {"record_id", "option_id", "capacity_m3h", "power_kw"}
REPORT_KEYS = {
    "schema_version",
    "run_id",
    "network_id",
    "assignments",
    "topology",
    "selection",
    "summary",
}
LEDGER_KEYS = {"input_digest", "status", "run_id", "branch_count", "terminal_count"}
CHECKPOINT_KEYS = {"terminal_count", "run_id", "input_digest", "report_digest"}
MAX_TERMINALS = 8
MAX_LAG_STEPS = 4
AMBIENT_C = 18.0


def _number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _no_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate)
    if not isinstance(value, dict):
        raise ValueError("top-level object required")
    return value


def _keys(obj: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError(f"{label} must be an object")
    if set(obj) != expected:
        raise ValueError(f"{label} key set does not match")
    return obj


def _series(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 8 or not all(_number(item) for item in value):
        raise ValueError(f"{label} must contain twelve finite values")
    return [float(item) for item in value]


def _validate(payload: dict[str, Any]) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, list[float]],
    list[dict[str, Any]],
    float,
]:
    _keys(payload, TOP_KEYS, "input")
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise ValueError("schema_version must be integer 1")
    if not isinstance(payload["run_id"], str) or not payload["run_id"]:
        raise ValueError("run_id must be non-empty")
    if not isinstance(payload["network_id"], str) or not payload["network_id"]:
        raise ValueError("network_id must be non-empty")

    terminals = payload["terminal_demands"]
    branches = payload["branch_records"]
    options = payload["pump_options"]
    limit = payload["trunk_limit_m3h"]
    if not isinstance(terminals, list) or not isinstance(branches, list):
        raise ValueError("terminal_demands and branch_records must be arrays")
    if not 2 <= len(terminals) <= MAX_TERMINALS:
        raise ValueError("two to twelve terminal demands are supported")
    if len(branches) != len(terminals) or len(branches) > MAX_TERMINALS:
        raise ValueError("one branch record per terminal and at most twelve are supported")
    if not isinstance(options, list):
        raise ValueError("pump_options must be an array")
    if not _number(limit) or float(limit) < 0:
        raise ValueError("trunk_limit_m3h must be non-negative")

    terminal_ids: set[str] = set()
    normalized_terminals: list[dict[str, Any]] = []
    for item in terminals:
        _keys(item, TERMINAL_KEYS, "terminal_demand")
        terminal_id = item["terminal_id"]
        if not isinstance(terminal_id, str) or not terminal_id or terminal_id in terminal_ids:
            raise ValueError("terminal ids must be unique non-empty strings")
        terminal_ids.add(terminal_id)
        flow_profile = _series(item["flow_profile"], "flow_profile")
        load_temp_profile = _series(item["load_temp_profile"], "load_temp_profile")
        required_flow = item["required_flow_m3h"]
        static_pressure = item["static_pressure_pa"]
        if not _number(required_flow) or float(required_flow) <= 0:
            raise ValueError("required_flow_m3h must be positive")
        if not _number(static_pressure) or float(static_pressure) < 0:
            raise ValueError("static_pressure_pa must be non-negative")
        normalized_terminals.append(
            {
                "terminal_id": terminal_id,
                "flow_profile": flow_profile,
                "load_temp_profile": load_temp_profile,
                "required_flow_m3h": float(required_flow),
                "static_pressure_pa": float(static_pressure),
            }
        )

    record_ids: set[str] = set()
    normalized_branches: list[dict[str, Any]] = []
    for item in branches:
        _keys(item, BRANCH_KEYS, "branch_record")
        record_id = item["record_id"]
        if not isinstance(record_id, str) or not record_id or record_id in record_ids:
            raise ValueError("record ids must be unique non-empty strings")
        record_ids.add(record_id)
        return_temp = _series(item["return_temp"], "return_temp")
        pressure = item["pressure_pa"]
        if not _number(pressure) or float(pressure) < 0:
            raise ValueError("pressure_pa must be non-negative")
        normalized_branches.append(
            {"record_id": record_id, "return_temp": return_temp, "pressure_pa": float(pressure)}
        )
    if terminal_ids & record_ids:
        raise ValueError("record_id and terminal_id namespaces overlap")

    aggregate = _keys(payload["aggregate_observations"], AGGREGATE_KEYS, "aggregate_observations")
    normalized_aggregate = {
        "heat_flow": _series(aggregate["heat_flow"], "aggregate heat_flow"),
        "supply_temp": _series(aggregate["supply_temp"], "aggregate supply_temp"),
        "return_temp": _series(aggregate["return_temp"], "aggregate return_temp"),
    }

    option_ids: set[str] = set()
    options_by_record: dict[str, list[dict[str, Any]]] = {record_id: [] for record_id in record_ids}
    for item in options:
        _keys(item, OPTION_KEYS, "pump_option")
        record_id = item["record_id"]
        if record_id not in record_ids:
            raise ValueError("pump option references an unknown branch")
        option_id = item["option_id"]
        if not isinstance(option_id, str) or not option_id or option_id in option_ids:
            raise ValueError("option ids must be unique non-empty strings")
        option_ids.add(option_id)
        capacity = item["capacity_m3h"]
        power = item["power_kw"]
        if not _number(capacity) or float(capacity) <= 0:
            raise ValueError("capacity_m3h must be positive")
        if not _number(power) or float(power) < 0:
            raise ValueError("power_kw must be non-negative")
        options_by_record[record_id].append(
            {
                "record_id": record_id,
                "option_id": option_id,
                "capacity_m3h": float(capacity),
                "power_kw": float(power),
            }
        )
    if any(not values for values in options_by_record.values()):
        raise ValueError("every branch needs a pump option")
    return normalized_terminals, normalized_branches, normalized_aggregate, options, float(limit)


def _shift(values: list[float], lag: int) -> list[float]:
    return [values[0] if index < lag else values[index - lag] for index in range(8)]


def _fit_branch(
    branch: dict[str, Any],
    terminal: dict[str, Any],
) -> tuple[int, float, float]:
    """Fit branch lag and loss from its return-temperature trace."""

    observed = branch["return_temp"]
    best: tuple[int, float, float] | None = None
    for lag in range(MAX_LAG_STEPS + 1):
        reference = _shift(terminal["load_temp_profile"], lag)
        delta = [value - AMBIENT_C for value in reference]
        denominator = math.fsum(value * value for value in delta)
        if denominator < 1e-12:
            continue
        numerator = math.fsum(
            (reference[index] - observed[index]) * delta[index]
            for index in range(8)
        )
        loss = min(0.45, max(0.0, numerator / denominator))
        residual_values = [
            (reference[index] - loss * delta[index]) - observed[index]
            for index in range(8)
        ]
        residual = math.sqrt(math.fsum(value * value for value in residual_values) / 8.0)
        candidate = (lag, loss, residual)
        if best is None or candidate[2] < best[2] - 1e-12:
            best = candidate
    if best is None:
        raise ValueError("branch temperature trace has no usable variation")
    return best


def _pair_cost(
    branches: list[dict[str, Any]],
    terminals: list[dict[str, Any]],
) -> list[list[tuple[int, float, float]]]:
    costs: list[list[tuple[int, float, float]]] = []
    for branch in branches:
        row = []
        for terminal in terminals:
            row.append(_fit_branch(branch, terminal))
        costs.append(row)
    return costs


def _assignment_dp(costs: list[list[tuple[int, float, float]]]) -> list[int]:
    """Find the minimum pairwise branch-to-terminal bijection by bitmask DP."""

    size = len(costs)
    states: dict[int, tuple[float, tuple[int, ...]]] = {0: (0.0, ())}
    for mask in range(1 << size):
        if mask not in states:
            continue
        row = bin(mask).count("1")
        if row == size:
            continue
        current, chosen = states[mask]
        for column in range(size):
            bit = 1 << column
            if mask & bit:
                continue
            candidate = current + costs[row][column][2]
            next_mask = mask | bit
            old = states.get(next_mask)
            choice = chosen + (column,)
            if old is None or (candidate, choice) < (old[0], old[1]):
                states[next_mask] = (candidate, choice)
    if (1 << size) - 1 not in states:
        raise ValueError("no branch mapping exists")
    return list(states[(1 << size) - 1][1])


def _aggregate_prediction(
    terminals: list[dict[str, Any]],
    mapping: list[int],
    fits: list[list[tuple[int, float, float]]],
    observation: dict[str, list[float]],
) -> tuple[list[float], list[float]]:
    heat = [0.0] * 8
    return_numerator = [0.0] * 8
    return_denominator = [0.0] * 8
    for branch_index, terminal_index in enumerate(mapping):
        lag, loss, _ = fits[branch_index][terminal_index]
        terminal = terminals[terminal_index]
        flow = _shift(terminal["flow_profile"], lag)
        temp = _shift(terminal["load_temp_profile"], lag)
        for point in range(8):
            weight = flow[point] * (1.0 - loss)
            heat[point] += weight * (observation["supply_temp"][point] - temp[point])
            return_numerator[point] += weight * temp[point]
            return_denominator[point] += weight
    aggregate_return = [
        return_numerator[point] / return_denominator[point]
        if return_denominator[point] > 1e-12
        else 0.0
        for point in range(8)
    ]
    return heat, aggregate_return


def _global_score(
    terminals: list[dict[str, Any]],
    mapping: list[int],
    costs: list[list[tuple[int, float, float]]],
    aggregate: dict[str, list[float]],
) -> tuple[float, float, list[float], list[float]]:
    pair_error = math.fsum(costs[index][mapping[index]][2] for index in range(len(mapping)))
    predicted_heat, predicted_return = _aggregate_prediction(
        terminals, mapping, costs, aggregate
    )
    heat_error = math.sqrt(
        math.fsum(
            (predicted_heat[index] - aggregate["heat_flow"][index]) ** 2
            for index in range(8)
        )
        / 8.0
    )
    return_error = math.sqrt(
        math.fsum(
            (predicted_return[index] - aggregate["return_temp"][index]) ** 2
            for index in range(8)
        )
        / 8.0
    )
    return pair_error + 0.02 * heat_error + 0.05 * return_error, heat_error, predicted_heat, predicted_return


def _solve_mapping(
    terminals: list[dict[str, Any]],
    branches: list[dict[str, Any]],
    aggregate: dict[str, list[float]],
) -> tuple[list[int], list[list[tuple[int, float, float]]]]:
    pairs = _pair_cost(branches, terminals)
    mapping = _assignment_dp(pairs)
    best_score, _, _, _ = _global_score(terminals, mapping, pairs, aggregate)

    # Resolve pairwise temperature ties against the aggregate heat and return
    # histories, without enumerating the full permutation set.
    changed = True
    while changed:
        changed = False
        for left in range(len(mapping)):
            for right in range(left + 1, len(mapping)):
                trial = list(mapping)
                trial[left], trial[right] = trial[right], trial[left]
                score, _, _, _ = _global_score(terminals, trial, pairs, aggregate)
                if score + 1e-12 < best_score:
                    mapping = trial
                    best_score = score
                    changed = True
    return mapping, pairs


def _topology(terminals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        terminals,
        key=lambda item: (item["static_pressure_pa"], item["terminal_id"]),
    )
    rows: list[dict[str, Any]] = []
    for index, terminal in enumerate(ordered):
        parent = None if index == 0 else ordered[index - 1]["terminal_id"]
        rows.append({"terminal_id": terminal["terminal_id"], "parent_terminal_id": parent})
    return rows


def _select_pumps(
    terminals: list[dict[str, Any]],
    branches: list[dict[str, Any]],
    mapping: list[int],
    options: list[dict[str, Any]],
    limit: float,
) -> list[dict[str, Any]]:
    by_branch: dict[str, list[dict[str, Any]]] = {item["record_id"]: [] for item in branches}
    for option in options:
        by_branch[option["record_id"]].append(option)
    states: dict[int, tuple[float, float, list[dict[str, Any]]]] = {0: (0.0, 0.0, [])}
    for branch_index, terminal_index in enumerate(mapping):
        terminal = terminals[terminal_index]
        choices = sorted(
            (
                item
                for item in by_branch[branches[branch_index]["record_id"]]
                if item["capacity_m3h"] >= terminal["required_flow_m3h"]
            ),
            key=lambda item: (item["option_id"], item["capacity_m3h"], item["power_kw"]),
        )
        if not choices:
            raise ValueError("no pump option covers a terminal's required flow")
        next_states: dict[int, tuple[float, float, list[dict[str, Any]]]] = {}
        for flow, power, selected in states.values():
            for option in choices:
                next_flow = flow + option["capacity_m3h"]
                if next_flow > limit + 1e-9:
                    continue
                key = int(round(next_flow * 1_000_000))
                row = {
                    "terminal_id": terminal["terminal_id"],
                    "record_id": branches[branch_index]["record_id"],
                    "option_id": option["option_id"],
                    "power_kw": option["power_kw"],
                }
                old = next_states.get(key)
                if old is None or (power + option["power_kw"], row["option_id"]) < (
                    old[1],
                    old[2][-1]["option_id"],
                ):
                    next_states[key] = (
                        next_flow,
                        power + option["power_kw"],
                        selected + [row],
                    )
        states = next_states
        if not states:
            raise ValueError("no pump selection fits the trunk limit")
    candidates = [item for item in states.values() if len(item[2]) == len(mapping)]
    if not candidates:
        raise ValueError("no complete pump selection exists")
    best = min(candidates, key=lambda item: (item[1], item[0], item[2]))
    return sorted(best[2], key=lambda item: item["terminal_id"])


def _canonical(report: dict[str, Any]) -> bytes:
    return (json.dumps(report, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _append_ledger(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _build(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    terminals, branches, aggregate, options, limit = _validate(payload)
    mapping, fits = _solve_mapping(terminals, branches, aggregate)
    assignments: list[dict[str, Any]] = []
    for branch_index, terminal_index in enumerate(mapping):
        lag, loss, residual = fits[branch_index][terminal_index]
        if residual > 0.03:
            raise ValueError("branch temperature trace does not match any terminal")
        assignments.append(
            {
                "record_id": branches[branch_index]["record_id"],
                "terminal_id": terminals[terminal_index]["terminal_id"],
                "lag_steps": int(lag),
                "loss_fraction": round(float(loss), 6),
            }
        )
    assignments.sort(key=lambda item: item["record_id"])
    topology = _topology(terminals)
    selection = _select_pumps(terminals, branches, mapping, options, limit)
    report = {
        "schema_version": 1,
        "run_id": payload["run_id"],
        "network_id": payload["network_id"],
        "assignments": assignments,
        "topology": topology,
        "selection": selection,
        "summary": {
            "branch_count": len(branches),
            "total_power_kw": round(math.fsum(item["power_kw"] for item in selection), 6),
            "trunk_flow_m3h": round(
                math.fsum(
                    item["capacity_m3h"]
                    for item in options
                    if item["option_id"] in {row["option_id"] for row in selection}
                ),
                6,
            ),
        },
    }
    if set(report) != REPORT_KEYS:
        raise ValueError("internal report key mismatch")
    return report, hashlib.sha256(_canonical(report)).hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("input refused: expected input and output paths", file=sys.stderr)
        return 2
    input_path = Path(argv[1])
    output_dir = Path(argv[2])
    try:
        payload = _read_json(input_path)
        report, report_digest = _build(payload)
        output_dir.mkdir(parents=True, exist_ok=True)
        input_digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
        ledger = {
            "input_digest": input_digest,
            "status": "accepted",
            "run_id": payload["run_id"],
            "branch_count": len(payload["branch_records"]),
            "terminal_count": len(payload["terminal_demands"]),
        }
        _keys(ledger, LEDGER_KEYS, "ledger")
        checkpoint = {
            "terminal_count": len(payload["terminal_demands"]),
            "run_id": payload["run_id"],
            "input_digest": input_digest,
            "report_digest": report_digest,
        }
        _keys(checkpoint, CHECKPOINT_KEYS, "checkpoint")
        ledger_path = output_dir / "ledger.jsonl"
        old_ledger = ledger_path.read_text(encoding="utf-8").splitlines() if ledger_path.exists() else []
        row_text = json.dumps(ledger, ensure_ascii=True, separators=(",", ":"))
        if not old_ledger or old_ledger[-1] != row_text:
            _append_ledger(ledger_path, ledger)
        _atomic_write(output_dir / "report.json", _canonical(report))
        _atomic_write(
            output_dir / "checkpoint.json",
            (json.dumps(checkpoint, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8"),
        )
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        print(f"input refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
