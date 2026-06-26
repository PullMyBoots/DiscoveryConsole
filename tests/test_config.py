"""Tests for YAML configuration."""

import tempfile

import pytest

from coral.config import (
    AgentConfig,
    ComputeConfig,
    CoralConfig,
    EvaluationConfig,
    FinalGraderConfig,
    GraderConfig,
    GraderProfileConfig,
    KnowledgeConfig,
    ResourceConfig,
    RunConfig,
    TaskConfig,
    WorkspaceConfig,
)


def test_config_roundtrip():
    config = CoralConfig(
        task=TaskConfig(name="test", description="A test", tips="Be fast"),
        grader=GraderConfig(
            entrypoint="my_pkg.grader:Grader",
            setup=["uv pip install -e ./my_pkg"],
            args={"k": 1},
        ),
        agents=AgentConfig(count=2, model="opus"),
    )

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        config.to_yaml(f.name)
        restored = CoralConfig.from_yaml(f.name)

    assert restored.task.name == "test"
    assert restored.grader.entrypoint == "my_pkg.grader:Grader"
    assert restored.grader.setup == ["uv pip install -e ./my_pkg"]
    assert restored.grader.args == {"k": 1}
    assert restored.agents.count == 2
    assert restored.agents.model == "opus"


def test_config_from_dict():
    data = {
        "task": {"name": "t", "description": "d"},
        "grader": {"entrypoint": "kernel_builder.grader:Grader"},
    }
    config = CoralConfig.from_dict(data)
    assert config.task.name == "t"
    assert config.grader.entrypoint == "kernel_builder.grader:Grader"
    assert config.agents.count == 1  # default
    assert config.evaluation.level == "L2"


def test_compute_config_defaults():
    config = CoralConfig.from_dict(
        {
            "task": {"name": "t", "description": "d"},
            "grader": {"entrypoint": "pkg.grader:Grader"},
        }
    )

    assert config.compute.backend == "local"
    assert config.compute.allow_unisolated_local is False
    assert config.compute.classes["explore"].default_profile == "cpu-small"
    assert config.compute.classes["explore"].allow_private_data is False
    assert config.compute.profiles["cpu-small"].cpu_cores == 2
    assert config.compute.profiles["cpu-small"].memory_gb == 8
    assert config.compute.profiles["gpu-small"].gpu_count == 1


def test_compute_config_roundtrip():
    config = CoralConfig.from_dict(
        {
            "task": {"name": "t", "description": "d"},
            "grader": {"entrypoint": "pkg.grader:Grader"},
            "compute": {
                "backend": "local",
                "allow_unisolated_local": True,
                "pool": {"gpu_count": 2, "gpu_ids": ["0", "1"]},
                "classes": {
                    "explore": {
                        "default_profile": "gpu-fast",
                        "network": True,
                        "max_running_per_agent": 2,
                    }
                },
                "profiles": {
                    "gpu-fast": {
                        "cpu_cores": 6,
                        "memory_gb": 40,
                        "gpu_count": 1,
                        "timeout": 1200,
                        "env": {"EXPERIMENT_MODE": "1"},
                    }
                },
            },
        }
    )

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        config.to_yaml(f.name)
        restored = CoralConfig.from_yaml(f.name)

    assert restored.compute.pool.gpu_ids == ["0", "1"]
    assert restored.compute.allow_unisolated_local is True
    assert restored.compute.classes["explore"].default_profile == "gpu-fast"
    assert restored.compute.classes["explore"].network is True
    assert restored.compute.classes["explore"].max_running_per_agent == 2
    assert restored.compute.profiles["gpu-fast"].cpu_cores == 6
    assert restored.compute.profiles["gpu-fast"].env == {"EXPERIMENT_MODE": "1"}


def test_compute_rejects_unsupported_backend():
    with pytest.raises(ValueError, match="compute.backend"):
        ComputeConfig(backend="docker")


def test_compute_class_requires_defined_default_profile():
    with pytest.raises(ValueError, match="default_profile"):
        CoralConfig.from_dict(
            {
                "task": {"name": "t", "description": "d"},
                "grader": {"entrypoint": "pkg.grader:Grader"},
                "compute": {
                    "classes": {"explore": {"default_profile": "missing"}},
                    "profiles": {"cpu-small": {"cpu_cores": 1}},
                },
            }
        )


def test_evaluation_level_roundtrip():
    config = CoralConfig(
        task=TaskConfig(name="test", description="A test"),
        evaluation=EvaluationConfig(level="l3"),
        grader=GraderConfig(
            entrypoint="pkg.grader:BGrader",
            final=FinalGraderConfig(entrypoint="pkg.grader:CGrader"),
        ),
    )

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        config.to_yaml(f.name)
        restored = CoralConfig.from_yaml(f.name)

    assert restored.evaluation.level == "L3"
    assert restored.evaluation.score_space() == "B"
    assert restored.evaluation.score_space(final=True) == "C"
    assert restored.grader.final.entrypoint == "pkg.grader:CGrader"


def test_evaluation_rejects_invalid_level():
    with pytest.raises(ValueError, match="evaluation.level"):
        CoralConfig.from_dict(
            {
                "task": {"name": "t", "description": "d"},
                "evaluation": {"level": "L4"},
                "grader": {"entrypoint": "pkg.grader:Grader"},
            }
        )


def test_l1_rejects_hidden_private_assets():
    with pytest.raises(ValueError, match="L1"):
        CoralConfig.from_dict(
            {
                "task": {"name": "t", "description": "d"},
                "evaluation": {"level": "L1"},
                "grader": {
                    "entrypoint": "pkg.grader:Grader",
                    "private": ["hidden/"],
                },
            }
        )


def test_l2_rejects_final_grader():
    with pytest.raises(ValueError, match="L2"):
        CoralConfig.from_dict(
            {
                "task": {"name": "t", "description": "d"},
                "evaluation": {"level": "L2"},
                "grader": {
                    "entrypoint": "pkg.grader:Grader",
                    "final": {"entrypoint": "pkg.grader:FinalGrader"},
                },
            }
        )


def test_l3_requires_final_grader():
    with pytest.raises(ValueError, match="L3"):
        CoralConfig.from_dict(
            {
                "task": {"name": "t", "description": "d"},
                "evaluation": {"level": "L3"},
                "grader": {"entrypoint": "pkg.grader:Grader"},
            }
        )


def test_legacy_grader_type_rejected():
    """Removed grader.type field raises a ValueError with upgrade guidance."""
    data = {
        "task": {"name": "t", "description": "d"},
        "grader": {"type": "function"},
    }
    with pytest.raises(ValueError, match="grader.type"):
        CoralConfig.from_dict(data)


def test_legacy_grader_module_rejected():
    """Removed grader.module field raises a ValueError with upgrade guidance."""
    data = {
        "task": {"name": "t", "description": "d"},
        "grader": {"module": "my.module"},
    }
    with pytest.raises(ValueError, match="grader.module"):
        CoralConfig.from_dict(data)


def test_legacy_islands_config_rejected():
    data = {
        "task": {"name": "t", "description": "d"},
        "islands": {"count": 2},
    }
    with pytest.raises(ValueError, match="removed topology section"):
        CoralConfig.from_dict(data)


def test_legacy_islands_dotlist_rejected():
    config = CoralConfig.from_dict({"task": {"name": "t", "description": "d"}})
    with pytest.raises(ValueError, match="removed topology section"):
        CoralConfig.merge_dotlist(config, ["islands.count=2"])


def test_sharing_toggles_are_removed():
    with pytest.raises(ValueError, match=r"sharing\.\* toggles have been removed"):
        CoralConfig.from_dict(
            {
                "task": {"name": "t", "description": "d"},
                "grader": {"entrypoint": "pkg.grader:Grader"},
                "sharing": {"notes": False},
            }
        )


def test_agent_runtime_options_roundtrip():
    config = CoralConfig(
        task=TaskConfig(name="test", description="A test"),
        agents=AgentConfig(
            runtime="codex",
            model="gpt-5.4",
            runtime_options={
                "model_reasoning_effort": "medium",
                "fast_mode": True,
            },
        ),
    )

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        config.to_yaml(f.name)
        restored = CoralConfig.from_yaml(f.name)

    assert restored.agents.runtime_options == {
        "model_reasoning_effort": "medium",
        "fast_mode": True,
    }


def test_config_setup_roundtrip():
    config = CoralConfig(
        task=TaskConfig(name="test", description="A test"),
        workspace=WorkspaceConfig(
            setup=["pip install numpy", "python download_data.py"],
        ),
    )

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        config.to_yaml(f.name)
        restored = CoralConfig.from_yaml(f.name)

    assert restored.workspace.setup == ["pip install numpy", "python download_data.py"]


def test_config_setup_defaults_empty():
    data = {
        "task": {"name": "t", "description": "d"},
    }
    config = CoralConfig.from_dict(data)
    assert config.workspace.setup == []


def test_knowledge_config_roundtrip():
    config = CoralConfig(
        task=TaskConfig(name="test", description="A test"),
        knowledge=KnowledgeConfig(path="./kb", snapshot=False),
    )

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        config.to_yaml(f.name)
        restored = CoralConfig.from_yaml(f.name)

    assert restored.knowledge.path == "./kb"
    assert restored.knowledge.snapshot is False


def test_grader_profiles_roundtrip():
    config = CoralConfig(
        task=TaskConfig(name="test", description="A test"),
        grader=GraderConfig(
            eval_version="eval_v2",
            profile="quick",
            timeout=900,
            profiles={
                "quick": GraderProfileConfig(
                    label="Quick",
                    timeout=120,
                    args={"profile": "quick", "cases": 10},
                    resources=ResourceConfig(cpu_cores=2, memory_gb=8, gpu_count=1),
                )
            },
            resources=ResourceConfig(cpu_cores=1, memory_gb=4),
        ),
    )

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        config.to_yaml(f.name)
        restored = CoralConfig.from_yaml(f.name)

    assert restored.grader.eval_version == "eval_v2"
    assert restored.grader.profile == "quick"
    assert restored.grader.profiles["quick"].timeout == 120
    assert restored.grader.profiles["quick"].args == {"profile": "quick", "cases": 10}
    assert restored.grader.resources.cpu_cores == 1
    assert restored.grader.resources.memory_gb == 4
    assert restored.grader.profiles["quick"].resources.cpu_cores == 2
    assert restored.grader.profiles["quick"].resources.memory_gb == 8
    assert restored.grader.profiles["quick"].resources.gpu_count == 1


def test_grader_parallel_resource_pool_roundtrip():
    config = CoralConfig.from_dict(
        {
            "task": {"name": "test", "description": "A test"},
            "grader": {
                "resources": {"gpu_count": 1},
                "parallel": {
                    "max_workers": 4,
                    "resources": {
                        "cpu_cores": 64,
                        "memory_gb": 256,
                        "storage_gb": 1024,
                        "gpu_count": 3,
                        "gpu_ids": ["0", "1", "2"],
                    },
                },
            },
        }
    )

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        config.to_yaml(f.name)
        restored = CoralConfig.from_yaml(f.name)

    assert restored.grader.resources.gpu_count == 1
    assert restored.grader.parallel.max_workers == 4
    assert restored.grader.parallel.resources.cpu_cores == 64
    assert restored.grader.parallel.resources.memory_gb == 256
    assert restored.grader.parallel.resources.storage_gb == 1024
    assert restored.grader.parallel.resources.gpu_count == 3
    assert restored.grader.parallel.resources.gpu_ids == ["0", "1", "2"]


def test_resource_config_rejects_negative_values():
    with pytest.raises(ValueError, match="cpu_cores"):
        ResourceConfig(cpu_cores=-1)
    with pytest.raises(ValueError, match="memory_gb"):
        ResourceConfig(memory_gb=-1)
    with pytest.raises(ValueError, match="storage_gb"):
        ResourceConfig(storage_gb=-1)
    with pytest.raises(ValueError, match="gpu_count"):
        ResourceConfig(gpu_count=-1)


# --- OmegaConf-specific tests ---


def test_dotlist_merge():
    config = CoralConfig(
        task=TaskConfig(name="test", description="A test"),
        agents=AgentConfig(count=1, model="sonnet"),
    )
    merged = CoralConfig.merge_dotlist(config, ["agents.count=4", "agents.model=opus"])
    assert merged.agents.count == 4
    assert merged.agents.model == "opus"
    # Original unchanged
    assert config.agents.count == 1


def test_dotlist_merge_nested():
    config = CoralConfig(
        task=TaskConfig(name="test", description="A test"),
        grader=GraderConfig(timeout=300),
    )
    merged = CoralConfig.merge_dotlist(config, ["grader.timeout=600"])
    assert merged.grader.timeout == 600


def test_dotlist_merge_empty():
    config = CoralConfig(
        task=TaskConfig(name="test", description="A test"),
    )
    merged = CoralConfig.merge_dotlist(config, [])
    assert merged.task.name == "test"


def test_missing_required_field():
    """Missing task.name should raise an error."""
    from omegaconf.errors import MissingMandatoryValue

    with pytest.raises(MissingMandatoryValue):
        CoralConfig.from_dict({"task": {"description": "d"}})


def test_missing_task_description():
    from omegaconf.errors import MissingMandatoryValue

    with pytest.raises(MissingMandatoryValue):
        CoralConfig.from_dict({"task": {"name": "t"}})


@pytest.mark.parametrize("key", ["heartbeat", "reflect_every", "heartbeat_every"])
def test_removed_agent_heartbeat_keys_are_rejected(key):
    data = {
        "task": {"name": "t", "description": "d"},
        "agents": {key: [] if key == "heartbeat" else 3},
    }

    with pytest.raises(ValueError, match="Removed agent loop key"):
        CoralConfig.from_dict(data)


def test_run_config_defaults():
    config = CoralConfig(
        task=TaskConfig(name="t", description="d"),
    )
    assert config.run.verbose is False
    assert config.run.ui is False
    assert config.run.session == "tmux"
    assert config.run.max_runtime_seconds == 0


def test_run_config_dotlist_override():
    config = CoralConfig(
        task=TaskConfig(name="t", description="d"),
    )
    merged = CoralConfig.merge_dotlist(
        config,
        ["run.session=local", "run.verbose=true", "run.max_runtime_seconds=1200"],
    )
    assert merged.run.session == "local"
    assert merged.run.verbose is True
    assert merged.run.ui is False
    assert merged.run.max_runtime_seconds == 1200


def test_run_config_roundtrip():
    config = CoralConfig(
        task=TaskConfig(name="t", description="d"),
        run=RunConfig(verbose=True, ui=True, session="docker", max_runtime_seconds=600),
    )

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        config.to_yaml(f.name)
        restored = CoralConfig.from_yaml(f.name)

    assert restored.run.verbose is True
    assert restored.run.ui is True
    assert restored.run.session == "docker"
    assert restored.run.max_runtime_seconds == 600


def test_run_config_rejects_negative_runtime_limit():
    with pytest.raises(ValueError, match="max_runtime_seconds"):
        RunConfig(max_runtime_seconds=-1)


def test_to_dict_excludes_task_dir():
    config = CoralConfig(
        task=TaskConfig(name="t", description="d"),
    )
    config.task_dir = "/some/path"
    d = config.to_dict()
    assert "task_dir" not in d


# --- Warm-start config tests ---


def test_assignments_yaml_roundtrip():
    """agents.assignments survives a YAML to_yaml/from_yaml roundtrip."""
    from coral.config import AgentAssignmentConfig

    config = CoralConfig(
        task=TaskConfig(name="t", description="d"),
        agents=AgentConfig(
            assignments=[
                AgentAssignmentConfig(runtime="claude_code", model="opus", count=2),
                AgentAssignmentConfig(
                    runtime="codex",
                    model="gpt-5.4",
                    count=1,
                    runtime_options={"fast_mode": True},
                ),
            ],
        ),
    )
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        config.to_yaml(f.name)
        restored = CoralConfig.from_yaml(f.name)

    assert len(restored.agents.assignments) == 2
    assert restored.agents.assignments[0].runtime == "claude_code"
    assert restored.agents.assignments[0].model == "opus"
    assert restored.agents.assignments[0].count == 2
    assert restored.agents.assignments[1].runtime == "codex"
    assert restored.agents.assignments[1].runtime_options == {"fast_mode": True}


def test_assignments_model_default_from_runtime_via_preprocess():
    """Empty model on an assignment is back-filled from the runtime default."""
    data = {
        "task": {"name": "t", "description": "d"},
        "agents": {
            "assignments": [
                {"runtime": "codex", "count": 1},
            ],
        },
    }
    config = CoralConfig.from_dict(data)
    assert config.agents.assignments[0].model == "gpt-5.4"


def test_skills_config_roundtrip():
    """agents.skills survives a YAML to_yaml/from_yaml roundtrip."""
    skills = ["./skills/test-skill", "./skills/other"]
    config = CoralConfig(
        task=TaskConfig(name="t", description="d"),
        agents=AgentConfig(skills=skills),
    )
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        config.to_yaml(f.name)
        restored = CoralConfig.from_yaml(f.name)

    assert restored.agents.skills == skills


def test_skills_config_defaults_empty():
    data = {"task": {"name": "t", "description": "d"}}
    config = CoralConfig.from_dict(data)
    assert config.agents.skills == []
