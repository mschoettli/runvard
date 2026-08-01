from modules import monitoring


def test_failed_webhook_delivery_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(monitoring, "ALERT_CONFIG", str(tmp_path / "alerts.json"))
    monkeypatch.setattr(monitoring, "ALERT_HISTORY", str(tmp_path / "history.json"))
    monitoring._save(monitoring.ALERT_CONFIG, {
        "rules": [], "channels": {"webhook": "https://alerts.example.test", "email": None},
    })
    monkeypatch.setattr(
        monitoring, "_send_webhook",
        lambda url, message: {"ok": False, "error": "connection refused"},
    )

    result = monitoring.trigger_alert("Time Machine replica failed", "webhook")

    assert result == {"ok": False, "delivery_error": "connection refused"}
    history = monitoring.get_alert_history()
    assert history[0]["delivered"] is False
    assert history[0]["delivery_error"] == "connection refused"


def test_in_app_alert_is_recorded_as_delivered(tmp_path, monkeypatch):
    monkeypatch.setattr(monitoring, "ALERT_CONFIG", str(tmp_path / "alerts.json"))
    monkeypatch.setattr(monitoring, "ALERT_HISTORY", str(tmp_path / "history.json"))

    result = monitoring.trigger_alert("Time Machine pool warning", "in-app")

    assert result == {"ok": True}
    assert monitoring.get_alert_history()[0]["delivered"] is True
