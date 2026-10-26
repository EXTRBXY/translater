import json

from translater.config import (
    MOD_ALT,
    MOD_CONTROL,
    AppConfig,
    Hotkey,
    HotkeyConfig,
    load_config,
    save_config,
    validate,
)


def _config(**hotkeys: Hotkey) -> AppConfig:
    return AppConfig(hotkeys=HotkeyConfig(**{**HotkeyConfig().__dict__, **hotkeys}))


def test_defaults_are_valid() -> None:
    assert validate(AppConfig()) == []


def test_roundtrip(tmp_path) -> None:
    path = tmp_path / "config.json"
    config = AppConfig(
        hotkeys=HotkeyConfig(Hotkey(0x41, MOD_CONTROL), Hotkey(0x42, MOD_ALT), Hotkey(0x05), Hotkey(0x06)),
        unload_delay_s=0,
        max_record_s=60,
    )
    save_config(path, config)
    assert load_config(path) == config
    assert not path.with_suffix(".json.tmp").exists()


def test_missing_file_gives_defaults(tmp_path) -> None:
    assert load_config(tmp_path / "absent.json") == AppConfig()


def test_corrupt_file_gives_defaults(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_config(path) == AppConfig()


def test_invalid_fields_fall_back_individually(tmp_path) -> None:
    path = tmp_path / "config.json"
    data = AppConfig().to_json()
    data["unload_delay_s"] = 9999
    data["max_record_s"] = True
    data["hotkeys"]["outgoing_hold"] = {"vk": 0x05, "modifiers": []}
    data["hotkeys"]["incoming_toggle"] = {"vk": 0x41, "modifiers": ["hyper"]}
    path.write_text(json.dumps(data), encoding="utf-8")

    config = load_config(path)

    assert config.unload_delay_s == AppConfig().unload_delay_s
    assert config.max_record_s == AppConfig().max_record_s
    assert config.hotkeys.outgoing_hold == Hotkey(0x05)
    assert config.hotkeys.incoming_toggle == HotkeyConfig().incoming_toggle


def test_conflicting_hotkeys_reset_to_defaults(tmp_path) -> None:
    path = tmp_path / "config.json"
    same = Hotkey(0x41, MOD_CONTROL)
    save_config(path, _config(incoming_toggle=same, outgoing_toggle=same))
    assert load_config(path).hotkeys == HotkeyConfig()


def test_toggle_requires_modifier_and_keyboard_key() -> None:
    assert validate(_config(incoming_toggle=Hotkey(0x78)))
    assert validate(_config(incoming_toggle=Hotkey(0x05, MOD_CONTROL)))
    assert validate(_config(incoming_toggle=Hotkey(0x11, MOD_CONTROL)))


def test_hold_key_rules() -> None:
    assert validate(_config(outgoing_hold=Hotkey(0x01))) != []
    assert validate(_config(outgoing_hold=Hotkey(0x41, MOD_CONTROL))) != []
    assert validate(_config(outgoing_hold=Hotkey(HotkeyConfig().incoming_toggle.vk))) != []
    assert validate(_config(outgoing_hold=Hotkey(0x06))) == []


def test_hold_key_must_differ_from_game_ptt() -> None:
    assert validate(_config(outgoing_hold=Hotkey(0x06), game_ptt=Hotkey(0x06)))


def test_new_game_ptt_does_not_reset_user_hotkeys(tmp_path) -> None:
    path = tmp_path / "config.json"
    data = AppConfig().to_json()
    del data["hotkeys"]["game_ptt"]
    data["hotkeys"]["outgoing_hold"] = {"vk": 0x05, "modifiers": []}
    data["hotkeys"]["incoming_toggle"] = {"vk": 0x41, "modifiers": ["ctrl"]}
    path.write_text(json.dumps(data), encoding="utf-8")

    hotkeys = load_config(path).hotkeys

    assert hotkeys.outgoing_hold == Hotkey(0x05)
    assert hotkeys.incoming_toggle == Hotkey(0x41, MOD_CONTROL)
    assert hotkeys.game_ptt == Hotkey(0x06)


def test_tts_voice_roundtrip_and_validation(tmp_path) -> None:
    path = tmp_path / "config.json"
    save_config(path, AppConfig(tts_voice="Microsoft David Desktop - English (United States)"))
    assert load_config(path).tts_voice == "Microsoft David Desktop - English (United States)"
    assert validate(AppConfig(tts_voice="x" * 300))


def test_speech_threshold_validation() -> None:
    assert validate(AppConfig(speech_threshold=35)) == []
    assert validate(AppConfig(speech_threshold=5))


def test_recognition_mode_roundtrip_and_fallback(tmp_path) -> None:
    path = tmp_path / "config.json"
    save_config(path, AppConfig(recognition="quality"))
    assert load_config(path).recognition == "quality"
    assert AppConfig(recognition="quality").beam_size == 5
    assert AppConfig().beam_size == 2

    data = AppConfig().to_json()
    data["recognition"] = "turbo"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_config(path).recognition == AppConfig().recognition
    assert validate(AppConfig(recognition="turbo"))
