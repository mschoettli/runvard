from modules import system_mgr


def test_power_action_uses_systemctl_for_sleep_actions(monkeypatch):
    calls = []

    def fake_run(cmd, timeout=60):
        calls.append(cmd)
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(system_mgr, "_run", fake_run)

    result = system_mgr.power_action("suspend")

    assert result["ok"] is True
    assert calls == [["systemctl", "suspend"]]


def test_power_action_rejects_delay_for_sleep_actions(monkeypatch):
    monkeypatch.setattr(system_mgr, "_run", lambda *args, **kwargs: {"ok": True})

    result = system_mgr.power_action("hibernate", 5)

    assert result["ok"] is False
    assert "Delay" in result["stderr"]


def test_power_action_rejects_unknown_action():
    result = system_mgr.power_action("explode")

    assert result["ok"] is False
    assert "Unbekannte" in result["stderr"]


def test_parse_powerprofiles_list_extracts_profiles_and_active():
    text = """
      power-saver:
        Driver: placeholder
    * balanced:
        Driver: placeholder
      performance:
        Driver: placeholder
    """

    profiles, active = system_mgr.parse_powerprofiles_list(text)

    assert profiles == ["power-saver", "balanced", "performance"]
    assert active == "balanced"


def test_parse_logind_config_keeps_supported_keys_only():
    text = """
    [Login]
    IdleAction=suspend
    IdleActionSec=20min
    HandlePowerKey=ignore
    RuntimeDirectorySize=10%
    #HandleLidSwitch=poweroff
    """

    result = system_mgr.parse_logind_config(text)

    assert result == {
        "IdleAction": "suspend",
        "IdleActionSec": "20min",
        "HandlePowerKey": "ignore",
    }


def test_parse_systemd_inhibitors_table():
    text = """
    WHO                          UID USER PID  COMM            WHAT  WHY
    NetworkManager               0   root 755  NetworkManager  sleep NetworkManager needs to turn off networks

    1 inhibitors listed.
    """

    result = system_mgr.parse_systemd_inhibitors(text)

    assert result == [{
        "who": "NetworkManager",
        "uid": "0",
        "user": "root",
        "pid": "755",
        "what": "sleep",
        "why": "NetworkManager needs to turn off networks",
    }]
