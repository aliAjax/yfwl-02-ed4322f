"""Behavior checks for the thermal-network inverse handover task."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest


ENTRY = os.environ.get("YEL002_ENTRY", "/app/heat_network.py")
AMBIENT = 18.0


def _shift(values: list[float], lag: int) -> list[float]:
    return [values[0] if index < lag else values[index - lag] for index in range(8)]


def _case(terminal_count: int = 5, run_id: str = "NET-RUN-1", limit: float = 500.0) -> dict:
    terminals = []
    for index in range(terminal_count):
        flow = [
            round(7.0 + index * 1.7 + 1.2 * ((index + point) % 4), 3)
            for point in range(8)
        ]
        load_temp = [
            round(31.0 + index * 1.4 + 0.35 * point + 0.8 * math.sin((index + point) * 0.7), 3)
            for point in range(8)
        ]
        terminals.append(
            {
                "terminal_id": f"T-{index:02d}",
                "flow_profile": flow,
                "load_temp_profile": load_temp,
                "required_flow_m3h": round(8.0 + index * 1.3, 3),
                "static_pressure_pa": float(1000 + index * 300),
            }
        )

    order = list(range(terminal_count))[::-1]
    branches = []
    for branch_index, terminal_index in enumerate(order):
        lag = (terminal_index + 1) % 5
        loss = 0.04 + terminal_index * 0.012
        reference = _shift(terminals[terminal_index]["load_temp_profile"], lag)
        observed = [
            round(reference[point] - loss * (reference[point] - AMBIENT), 6)
            for point in range(8)
        ]
        branches.append(
            {
                "record_id": f"R-{terminal_index:02d}",
                "return_temp": observed,
                "pressure_pa": terminals[terminal_index]["static_pressure_pa"],
            }
        )

    options = []
    for index, terminal in enumerate(terminals):
        required = terminal["required_flow_m3h"]
        for option_index, (capacity, power) in enumerate(
            ((required, 2.0), (required + 5, 1.5))
        ):
            options.append(
                {
                    "record_id": f"R-{index:02d}",
                    "option_id": f"P-{index:02d}-{option_index}",
                    "capacity_m3h": round(capacity, 3),
                    "power_kw": power,
                }
            )

    supply = [round(55.0 + 0.5 * point, 6) for point in range(8)]
    heat = [0.0] * 8
    numerator = [0.0] * 8
    denominator = [0.0] * 8
    for terminal_index, terminal in enumerate(terminals):
        lag = (terminal_index + 1) % 5
        loss = 0.04 + terminal_index * 0.012
        flow = _shift(terminal["flow_profile"], lag)
        temperature = _shift(terminal["load_temp_profile"], lag)
        for point in range(8):
            weight = flow[point] * (1.0 - loss)
            heat[point] += weight * (supply[point] - temperature[point])
            numerator[point] += weight * temperature[point]
            denominator[point] += weight
    aggregate = {
        "heat_flow": [round(value, 6) for value in heat],
        "supply_temp": supply,
        "return_temp": [
            round(numerator[point] / denominator[point], 6) for point in range(8)
        ],
    }
    return {
        "schema_version": 1,
        "run_id": run_id,
        "network_id": "N-001",
        "terminal_demands": terminals,
        "branch_records": branches,
        "aggregate_observations": aggregate,
        "pump_options": options,
        "trunk_limit_m3h": limit,
    }


def _run(case: dict, tmp_path: Path):
    input_path = tmp_path / "network.json"
    output_dir = tmp_path / "result"
    input_path.write_text(json.dumps(case), encoding="utf-8")
    output_dir.mkdir(exist_ok=True)
    proc = subprocess.run(
        [sys.executable, ENTRY, str(input_path), str(output_dir)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    report_path = output_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    return proc, report, output_dir


def _run_raw(raw: str, tmp_path: Path):
    input_path = tmp_path / "network.json"
    output_dir = tmp_path / "result"
    input_path.write_text(raw, encoding="utf-8")
    output_dir.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, ENTRY, str(input_path), str(output_dir)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_output_contract_creates_three_artifacts(tmp_path):
    """题面：「交接目录生成报告、追加账本和检查点三类交付件。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""
    assert (output_dir / "ledger.jsonl").is_file()
    assert (output_dir / "checkpoint.json").is_file()
    assert report["summary"]["branch_count"] == 5


def test_success_output_channels_are_silent(tmp_path):
    """题面：「终端在正常完成时保持无文本输出。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_top_level_keys_reject_missing_field(tmp_path):
    """题面：「管网交接 JSON 的最外层字段为 schema_version,run_id,network_id,terminal_demands,branch_records,aggregate_observations,pump_options,trunk_limit_m3h。」"""
    case = _case()
    del case["network_id"]
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()


def test_terminal_keys_reject_extra_field(tmp_path):
    """题面：「用户端点记录包含 terminal_id,flow_profile,load_temp_profile,required_flow_m3h,static_pressure_pa 五个槽位。」"""
    case = _case()
    case["terminal_demands"][0]["note"] = "extra"
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()


def test_branch_keys_reject_extra_field(tmp_path):
    """题面：「支路记录包含 record_id,return_temp,pressure_pa 三个槽位。」"""
    case = _case()
    case["branch_records"][0]["heat_flow"] = [1.0] * 8
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()


def test_aggregate_keys_reject_missing_field(tmp_path):
    """题面：「聚合观测包含 heat_flow,supply_temp,return_temp 三个槽位。」"""
    case = _case()
    del case["aggregate_observations"]["supply_temp"]
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()


def test_pump_option_keys_reject_extra_field(tmp_path):
    """题面：「泵档记录包含 record_id,option_id,capacity_m3h,power_kw 四个槽位。」"""
    case = _case()
    case["pump_options"][0]["note"] = "extra"
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()


def test_profile_length_accepts_eight_values(tmp_path):
    """题面：「flow_profile、load_temp_profile、return_temp、heat_flow、supply_temp 和聚合 return_temp 各有 8 个数值。」"""
    case = _case()
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 0
    assert len(case["terminal_demands"][0]["flow_profile"]) == 8
    assert report["summary"]["branch_count"] == 5


def test_profile_length_rejects_seven_values(tmp_path):
    """题面：「flow_profile、load_temp_profile、return_temp、heat_flow、supply_temp 和聚合 return_temp 各有 8 个数值。」"""
    case = _case()
    case["terminal_demands"][0]["load_temp_profile"] = case["terminal_demands"][0]["load_temp_profile"][:-1]
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()


def test_duplicate_identifiers_are_refused(tmp_path):
    """题面：「record_id 与 terminal_id 不得重复。」"""
    case = _case()
    case["terminal_demands"][0]["terminal_id"] = case["branch_records"][0]["record_id"]
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()


def test_boolean_schema_version_is_refused(tmp_path):
    """题面：「布尔值不能通过计量校验。」"""
    case = _case()
    case["schema_version"] = True
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()


def test_schema_version_must_be_integer_one(tmp_path):
    """题面：「`schema_version` 必须是整数 `1`。」"""
    case = _case()
    case["schema_version"] = 2
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()


def test_maximum_network_size_is_supported(tmp_path):
    """题面：「接管名单最多包含 8 条支路和 8 个端点。」"""
    proc, report, output_dir = _run(_case(terminal_count=8), tmp_path)
    assert proc.returncode == 0
    assert report["summary"]["branch_count"] == 8
    assert len(report["assignments"]) == 8


def test_every_branch_needs_a_pump_option(tmp_path):
    """题面：「每个支路记录都必须有至少一个泵选项。」"""
    case = _case()
    case["pump_options"] = [item for item in case["pump_options"] if item["record_id"] != "R-00"]
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()


def test_report_bytes_end_with_newline(tmp_path):
    """题面：「文档排成单行对象文本，UTF-8，行尾一次换行。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    raw = (output_dir / "report.json").read_bytes()
    assert proc.returncode == 0
    assert raw.endswith(b"\n")
    assert b'": ' not in raw


def test_report_key_set_is_exact(tmp_path):
    """题面：「供热输出对象包含七个成员：schema_version,run_id,network_id,assignments,topology,selection,summary。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert type(report["schema_version"]) is int
    assert report["schema_version"] == 1
    assert set(report) == {
        "schema_version",
        "run_id",
        "network_id",
        "assignments",
        "topology",
        "selection",
        "summary",
    }


def test_assignment_item_key_set_is_exact(tmp_path):
    """题面：「支路映射行保留 record_id,terminal_id,lag_steps,loss_fraction 四项。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert all(
        set(item) == {"record_id", "terminal_id", "lag_steps", "loss_fraction"}
        for item in report["assignments"]
    )


def test_topology_item_key_set_is_exact(tmp_path):
    """题面：「拓扑行只携带 terminal_id 与 parent_terminal_id。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert all(set(item) == {"terminal_id", "parent_terminal_id"} for item in report["topology"])


def test_selection_item_key_set_is_exact(tmp_path):
    """题面：「泵选择行保留 terminal_id,record_id,option_id,power_kw 四项。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert all(
        set(item) == {"terminal_id", "record_id", "option_id", "power_kw"}
        for item in report["selection"]
    )


def test_summary_key_set_is_exact(tmp_path):
    """题面：「报告汇总区记录 branch_count,total_power_kw,trunk_flow_m3h。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert set(report["summary"]) == {"branch_count", "total_power_kw", "trunk_flow_m3h"}


def test_assignment_recovers_hidden_branch_mapping(tmp_path):
    """题面：「归属步骤要用支路回水轨迹拟合每个候选端点的滞后和散热系数，再用聚合热量和聚合回水温度闭合对应关系，不能从输入直接抄出完整映射。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    mapping = {item["record_id"]: item["terminal_id"] for item in report["assignments"]}
    assert proc.returncode == 0
    assert mapping == {f"R-{index:02d}": f"T-{index:02d}" for index in range(5)}


def test_lag_and_loss_are_recovered(tmp_path):
    """题面：「热滞后取 0 到 4 个时间步，步长为五分钟。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    recovered = {item["record_id"]: item for item in report["assignments"]}
    assert proc.returncode == 0
    assert recovered["R-00"]["lag_steps"] == 1
    assert recovered["R-01"]["lag_steps"] == 2
    assert recovered["R-02"]["lag_steps"] == 3
    assert recovered["R-00"]["loss_fraction"] == pytest.approx(0.04)
    assert recovered["R-04"]["loss_fraction"] == pytest.approx(0.088)


def test_aggregate_heat_history_closes(tmp_path):
    """题面：「聚合热量按各支路平移后的流量、供回水温差和散热系数求和；聚合回水温度按同一组平移流量和散热系数加权。」"""
    case = _case()
    proc, report, output_dir = _run(case, tmp_path)
    terms = {item["terminal_id"]: item for item in case["terminal_demands"]}
    branches = {item["record_id"]: item for item in case["branch_records"]}
    predicted = [0.0] * 8
    for assignment in report["assignments"]:
        term = terms[assignment["terminal_id"]]
        flow = _shift(term["flow_profile"], assignment["lag_steps"])
        temp = _shift(term["load_temp_profile"], assignment["lag_steps"])
        for point in range(8):
            predicted[point] += flow[point] * (1.0 - assignment["loss_fraction"]) * (
                case["aggregate_observations"]["supply_temp"][point] - temp[point]
            )
    error = math.sqrt(
        math.fsum(
            (predicted[point] - case["aggregate_observations"]["heat_flow"][point]) ** 2
            for point in range(8)
        )
        / 8.0
    )
    assert proc.returncode == 0
    assert error < 0.002


def test_aggregate_return_history_closes(tmp_path):
    """题面：「聚合回水温度按同一组平移流量和散热系数加权。」"""
    case = _case()
    proc, report, output_dir = _run(case, tmp_path)
    terms = {item["terminal_id"]: item for item in case["terminal_demands"]}
    numerator = [0.0] * 8
    denominator = [0.0] * 8
    for assignment in report["assignments"]:
        term = terms[assignment["terminal_id"]]
        flow = _shift(term["flow_profile"], assignment["lag_steps"])
        temp = _shift(term["load_temp_profile"], assignment["lag_steps"])
        for point in range(8):
            weight = flow[point] * (1.0 - assignment["loss_fraction"])
            numerator[point] += weight * temp[point]
            denominator[point] += weight
    predicted = [numerator[point] / denominator[point] for point in range(8)]
    error = math.sqrt(
        math.fsum(
            (predicted[point] - case["aggregate_observations"]["return_temp"][point]) ** 2
            for point in range(8)
        )
        / 8.0
    )
    assert proc.returncode == 0
    assert error < 0.002


def test_loss_and_lag_range_is_preserved(tmp_path):
    """题面：「loss_fraction 保留六位小数，且不能为负。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert all(0 <= item["lag_steps"] <= 6 for item in report["assignments"])
    assert all(item["loss_fraction"] >= 0 for item in report["assignments"])
    assert all(round(item["loss_fraction"], 6) == item["loss_fraction"] for item in report["assignments"])


def test_topology_connects_nearest_lower_pressure(tmp_path):
    """题面：「topology 按 static_pressure_pa 从小到大连接最近的更低压力端点，根节点的 parent_terminal_id 为 null。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    topology = {item["terminal_id"]: item["parent_terminal_id"] for item in report["topology"]}
    assert proc.returncode == 0
    assert topology == {"T-00": None, "T-01": "T-00", "T-02": "T-01", "T-03": "T-02", "T-04": "T-03"}


def test_selection_is_sorted_by_terminal_id(tmp_path):
    """题面：「selection 按 terminal_id 升序排列。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    terminals = [item["terminal_id"] for item in report["selection"]]
    assert proc.returncode == 0
    assert terminals == sorted(terminals)


def test_selection_minimizes_total_pump_power(tmp_path):
    """题面：「交接结果要能让下一班从聚合历程复核用户归属、热滞后、散热系数和泵功选择。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert report["summary"]["total_power_kw"] == pytest.approx(7.5)
    assert all(item["option_id"].endswith("-1") for item in report["selection"])


def test_repeated_run_keeps_report_bytes(tmp_path):
    """题面：「同一管网快照的报告字节保持一致。」"""
    proc_first, report_first, output_dir = _run(_case(), tmp_path)
    first = (output_dir / "report.json").read_bytes()
    proc_second, report_second, _ = _run(_case(), tmp_path)
    assert proc_first.returncode == 0
    assert proc_second.returncode == 0
    assert (output_dir / "report.json").read_bytes() == first


def test_repeated_run_does_not_append_ledger(tmp_path):
    """题面：「账本条目数在快照重放后保持稳定。」"""
    proc_first, report_first, output_dir = _run(_case(), tmp_path)
    first = (output_dir / "ledger.jsonl").read_bytes()
    proc_second, report_second, _ = _run(_case(), tmp_path)
    assert proc_first.returncode == 0
    assert proc_second.returncode == 0
    assert (output_dir / "ledger.jsonl").read_bytes() == first


def test_changed_digest_appends_ledger(tmp_path):
    """题面：「新的快照摘要会单独占一条账目。」"""
    proc_first, report_first, output_dir = _run(_case(), tmp_path)
    proc_second, report_second, _ = _run(_case(run_id="NET-RUN-2"), tmp_path)
    lines = (output_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert proc_first.returncode == 0
    assert proc_second.returncode == 0
    assert len(lines) == 2


def test_ledger_line_has_exact_keys(tmp_path):
    """题面：「input_digest,status,run_id,branch_count,terminal_count 是管网账本单行的字段。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    row = json.loads((output_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert proc.returncode == 0
    assert set(row) == {"input_digest", "status", "run_id", "branch_count", "terminal_count"}
    assert len(row["input_digest"]) == 64


def test_checkpoint_has_exact_keys(tmp_path):
    """题面：「检查点先记录 terminal_count，再依次保存 run_id,input_digest,report_digest。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    checkpoint = json.loads((output_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert proc.returncode == 0
    assert set(checkpoint) == {"terminal_count", "run_id", "input_digest", "report_digest"}
    assert len(checkpoint["report_digest"]) == 64


def test_missing_key_is_refused(tmp_path):
    """题面：「管网结构若缺键、含多余键或重复键，计算立即停止。」"""
    case = _case()
    del case["aggregate_observations"]
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert "input refused:" in proc.stderr
    assert not (output_dir / "report.json").exists()


def test_duplicate_json_key_is_refused(tmp_path):
    """题面：「管网结构若缺键、含多余键或重复键，计算立即停止。」"""
    raw = json.dumps(_case())[:-1] + ',"run_id":"NET-RUN-2"}'
    proc = _run_raw(raw, tmp_path)
    assert proc.returncode == 2
    assert "input refused:" in proc.stderr
    assert not (tmp_path / "result" / "report.json").exists()


def test_refusal_uses_fixed_stderr_prefix(tmp_path):
    """题面：「input refused: 后面的原因只写进标准错误。」"""
    case = _case()
    case["aggregate_observations"]["heat_flow"] = "not a list"
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert proc.stderr.splitlines()[0].startswith("input refused:")
    assert not (output_dir / "report.json").exists()


def test_trunk_limit_rejects_oversized_selection(tmp_path):
    """题面：「泵选项总容量需要落在 trunk_limit_m3h 以内。」"""
    proc, report, output_dir = _run(_case(limit=10.0), tmp_path)
    assert proc.returncode == 2
    assert "input refused:" in proc.stderr
    assert not (output_dir / "report.json").exists()


def test_refusal_preserves_existing_artifacts(tmp_path):
    """题面：「交接失败时原有报告、追加账本和检查点必须维持原状。」"""
    proc_first, report_first, output_dir = _run(_case(), tmp_path)
    old_report = (output_dir / "report.json").read_bytes()
    old_ledger = (output_dir / "ledger.jsonl").read_bytes()
    old_checkpoint = (output_dir / "checkpoint.json").read_bytes()
    broken = _case()
    del broken["aggregate_observations"]
    proc_second, report_second, _ = _run(broken, tmp_path)
    assert proc_first.returncode == 0
    assert proc_second.returncode == 2
    assert (output_dir / "report.json").read_bytes() == old_report
    assert (output_dir / "ledger.jsonl").read_bytes() == old_ledger
    assert (output_dir / "checkpoint.json").read_bytes() == old_checkpoint
