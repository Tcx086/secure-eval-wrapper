"""Deterministic monitoring policy configuration."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.monitoring.models import HealthCheckDefinition


@dataclass(frozen=True)
class MonitoringConfiguration:
    checks: tuple[HealthCheckDefinition, ...] = ()
    maximum_data_age_seconds: Mapping[str, Decimal] = field(default_factory=dict)
    maximum_signal_age_seconds: Decimal = Decimal("3600")
    maximum_active_order_age_seconds: Decimal = Decimal("3600")
    warning_blocked_rate: Decimal = Decimal("0.10")
    critical_blocked_rate: Decimal = Decimal("0.50")
    warning_utilization: Decimal = Decimal("0.80")
    critical_utilization: Decimal = Decimal("1.00")
    warning_drawdown: Decimal = Decimal("0.10")
    critical_drawdown: Decimal = Decimal("0.20")
    flat_signal_warning_ratio: Decimal = Decimal("0.90")
    heartbeat_interval_seconds: Decimal = Decimal("30")
    test_request_grace_seconds: Decimal = Decimal("10")
    disconnect_timeout_seconds: Decimal = Decimal("60")
    incident_management_enabled: bool = True
    configuration_version: str = "phase6-v1"

    def __post_init__(self) -> None:
        ages = dict(self.maximum_data_age_seconds)
        for key, value in ages.items():
            if not isinstance(key, str) or not key.strip() or not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("maximum_data_age_seconds requires non-empty keys and non-negative finite Decimals")
        object.__setattr__(self, "maximum_data_age_seconds", MappingProxyType(ages))
        decimal_fields = (
            "maximum_signal_age_seconds", "maximum_active_order_age_seconds",
            "warning_blocked_rate", "critical_blocked_rate", "warning_utilization",
            "critical_utilization", "warning_drawdown", "critical_drawdown",
            "flat_signal_warning_ratio", "heartbeat_interval_seconds",
            "test_request_grace_seconds", "disconnect_timeout_seconds",
        )
        for name in decimal_fields:
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a non-negative finite Decimal")
        if self.warning_blocked_rate > self.critical_blocked_rate or self.warning_utilization > self.critical_utilization or self.warning_drawdown > self.critical_drawdown:
            raise ValueError("warning thresholds must not exceed critical thresholds")
        if self.critical_blocked_rate > 1 or self.flat_signal_warning_ratio > 1:
            raise ValueError("ratio thresholds must be at most one")
        if not self.configuration_version.strip():
            raise ValueError("configuration_version must be non-empty")
        names = [(check.category.value, check.component, check.check_name) for check in self.checks]
        if len(names) != len(set(names)):
            raise ValueError("monitoring check definitions must be unique")

    @property
    def config_sha256(self) -> str:
        return sha256_payload({
            "checks": [asdict(check) for check in self.checks],
            "maximum_data_age_seconds": dict(self.maximum_data_age_seconds),
            "maximum_signal_age_seconds": self.maximum_signal_age_seconds,
            "maximum_active_order_age_seconds": self.maximum_active_order_age_seconds,
            "warning_blocked_rate": self.warning_blocked_rate,
            "critical_blocked_rate": self.critical_blocked_rate,
            "warning_utilization": self.warning_utilization,
            "critical_utilization": self.critical_utilization,
            "warning_drawdown": self.warning_drawdown,
            "critical_drawdown": self.critical_drawdown,
            "flat_signal_warning_ratio": self.flat_signal_warning_ratio,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "test_request_grace_seconds": self.test_request_grace_seconds,
            "disconnect_timeout_seconds": self.disconnect_timeout_seconds,
            "incident_management_enabled": self.incident_management_enabled,
            "configuration_version": self.configuration_version,
        })

    def enabled(self, check_name: str) -> bool:
        matches = [check for check in self.checks if check.check_name == check_name]
        return True if not matches else matches[0].enabled