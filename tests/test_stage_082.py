"""Regression contracts for Vacuum Schedule 0.8.3 repetition semantics."""
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from execution_models import ExecutionAttempt, ExecutionMode, RepetitionMode  # noqa: E402




def test_repetition_model_distinguishes_requested_passes_from_physical_attempts():
    now = datetime(2026, 8, 18, 10, 0)
    native = ExecutionAttempt.create(
        job_id="j",
        execution_mode=ExecutionMode.REAL,
        zone_ids=("z1", "z2"),
        target_type="segment",
        targets=("0_16", "0_23"),
        cleaning_params={"passes": 2},
        now=now,
        requested_passes=2,
        repetition_mode=RepetitionMode.NATIVE,
        pass_total=1,
    )
    restored = ExecutionAttempt.from_dict(native.to_dict())
    assert restored.requested_passes == 2
    assert restored.repetition_mode is RepetitionMode.NATIVE
    assert restored.pass_index == 1 and restored.pass_total == 1 and restored.final_pass


def test_legacy_sequential_attempts_remain_emulated_after_upgrade():
    now = datetime(2026, 8, 18, 10, 0)
    legacy = {
        "attempt_id": "a",
        "job_id": "j",
        "execution_mode": "REAL",
        "zone_ids": ["z"],
        "target_type": "segment",
        "targets": ["0_16"],
        "cleaning_params": {"passes": 2},
        "pass_index": 1,
        "pass_total": 2,
        "state": "COMPLETED",
        "created_at": now.isoformat(),
    }
    restored = ExecutionAttempt.from_dict(legacy)
    assert restored.requested_passes == 2
    assert restored.repetition_mode is RepetitionMode.EMULATED
    assert restored.pass_total == 2


def test_manager_merges_targets_before_choosing_repetition_transport():
    manager = (MODULE / "execution_manager.py").read_text()
    assert 'dict.fromkeys(zone.robot_target_id' in manager
    assert 'passes belongs to one merged cleaning command' in manager
    assert 'RepetitionMode.NATIVE' in manager
    assert 'RepetitionMode.EMULATED' in manager


def test_roborock_segment_native_repeat_is_one_command_object():
    adapter = (MODULE / "execution_adapter.py").read_text()
    assert '"app_segment_clean"' in adapter
    assert '[{"segments": segments, "repeat": repeats}]' in adapter
    assert 'async_send_command' in adapter
    assert '_maps_trait' in adapter


def test_roborock_coordinate_zones_use_repeat_in_same_command():
    adapter = (MODULE / "execution_adapter.py").read_text()
    assert '"command": "app_zoned_clean"' in adapter
    assert '"params": [[*coords, repeats] for coords in zones]' in adapter


def test_frontend_labels_native_and_emulated_repetitions_explicitly():
    panel = (MODULE / "frontend" / "panel.js").read_text()
    assert '_attemptRepetitionLabel(a)' in panel
    assert 'panel.repetition_native' in panel
    assert 'panel.repetition_emulated' in panel
    assert 'a?.requested_passes ?? a?.cleaning_params?.passes' in panel
    for lang in ("en", "ru"):
        text = (MODULE / "frontend" / "localization" / f"{lang}.json").read_text()
        assert 'panel.repetition_native' in text
        assert 'panel.repetition_emulated' in text
