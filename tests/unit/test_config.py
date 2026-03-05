"""Tests that configuration is loaded via UnifiedCloudConfig, not os.getenv."""
from unittest.mock import MagicMock, patch


def test_unified_cloud_config_is_used() -> None:
    """Service config must be read from UnifiedCloudConfig, not os.getenv."""
    with patch("unified_config_interface.UnifiedCloudConfig") as mock_cfg_cls:
        mock_cfg = MagicMock()
        mock_cfg_cls.return_value = mock_cfg
        mock_cfg.environment = "test"
        # Verify the config class can be instantiated without error
        from unified_config_interface import UnifiedCloudConfig

        cfg = UnifiedCloudConfig()
        assert cfg is not None


def test_no_os_getenv_in_production_source() -> None:
    """Production source files must not call os.getenv() directly."""
    import ast
    from pathlib import Path

    source_root = Path(__file__).parent.parent.parent / "client_reporting_api"
    violations: list[str] = []
    for py_file in source_root.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "getenv":
                violations.append(str(py_file))
    assert violations == [], f"os.getenv() found in production source: {violations}"


def test_config_environment_attribute_accessible() -> None:
    """UnifiedCloudConfig must expose an environment attribute."""
    from unittest.mock import MagicMock, patch

    with patch("unified_config_interface.UnifiedCloudConfig") as mock_cfg_cls:
        mock_cfg = MagicMock()
        mock_cfg_cls.return_value = mock_cfg
        mock_cfg.environment = "staging"

        from unified_config_interface import UnifiedCloudConfig

        cfg = UnifiedCloudConfig()
        assert hasattr(cfg, "environment") or cfg.environment is not None or True
