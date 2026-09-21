"""Property-based tests for Phase 4 monitors (Hypothesis)."""
import dataclasses

import hypothesis
import pytest
from hypothesis import given, assume, settings
from hypothesis import strategies as st

from src.core.monitor import MonitorCategory, MonitorResult, MonitorStatus, classify_changes_as_alert
from src.core.models import FieldChange


class TestMonitorResultProperties:
    """Properties that must hold for any MonitorResult."""

    @given(
        monitor=st.text(min_size=1, max_size=50),
        resource=st.text(min_size=1, max_size=100),
        status=st.sampled_from(list(MonitorStatus)),
        summary=st.text(min_size=1, max_size=500),
        category=st.sampled_from(list(MonitorCategory)),
    )
    def test_to_dict_roundtrip(self, monitor, resource, status, summary, category):
        """to_dict() produces valid structure for any input."""
        result = MonitorResult(
            monitor=monitor,
            resource=resource,
            status=status,
            summary=summary,
            category=category,
        )
        d = result.to_dict()
        assert d["monitor"] == monitor
        assert d["resource"] == resource
        assert d["status"] == status.value
        assert d["summary"] == summary
        assert d["category"] == category.value
        assert "evaluated_at" in d

    @given(
        monitor=st.text(min_size=1, max_size=50),
        resource=st.text(min_size=1, max_size=100),
        status=st.sampled_from(list(MonitorStatus)),
        summary=st.text(min_size=1, max_size=500),
        category=st.sampled_from(list(MonitorCategory)),
    )
    def test_frozen(self, monitor, resource, status, summary, category):
        """MonitorResult is frozen (immutable)."""
        result = MonitorResult(
            monitor=monitor,
            resource=resource,
            status=status,
            summary=summary,
            category=category,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.status = MonitorStatus.ERROR


class TestClassifyChangesAsAlertProperties:
    """Properties for classify_changes_as_alert."""

    @given(
        fields=st.lists(st.text(min_size=1, max_size=50), min_size=0, max_size=10),
    )
    def test_empty_changes_never_alert(self, fields):
        """Empty changes never produce an alert."""
        alert_fields = frozenset(fields)
        assert classify_changes_as_alert((), alert_fields) is False

    @given(
        field_name=st.text(min_size=1, max_size=50),
    )
    def test_matching_field_is_alert(self, field_name):
        """A change matching an alert field is always an alert."""
        changes = (FieldChange(field=field_name, before="a", after="b"),)
        alert_fields = frozenset({field_name})
        assert classify_changes_as_alert(changes, alert_fields) is True

    @given(
        non_alert=st.text(min_size=1, max_size=50),
        alert_field=st.text(min_size=1, max_size=50),
    )
    def test_non_matching_field_not_alert(self, non_alert, alert_field):
        """A change NOT matching any alert field is not an alert."""
        assume(non_alert != alert_field)
        changes = (FieldChange(field=non_alert, before="a", after="b"),)
        alert_fields = frozenset({alert_field})
        assert classify_changes_as_alert(changes, alert_fields) is False
