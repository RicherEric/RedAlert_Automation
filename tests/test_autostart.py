"""The test profile must only change the keys it names and keep the rest of RA2MD.INI."""
from autostart import apply_profile


def test_apply_profile_keeps_other_keys(tmp_path):
    ini = tmp_path / "RA2MD.INI"
    ini.write_text("[Options]\nGameSpeed=1\nScrollRate=3\n\n[Skirmish]\nCredits=5000\n", encoding="latin-1")
    apply_profile(ini, {"Options": {"GameSpeed": "3"}, "Skirmish": {"Credits": "10000", "UnitCount": "10"},
                        "Video": {"ScreenWidth": "800"}})
    text = ini.read_text(encoding="latin-1")
    assert "GameSpeed=3" in text and "GameSpeed=1" not in text
    assert "ScrollRate=3" in text
    assert "Credits=10000" in text and "UnitCount=10" in text
    assert text.index("UnitCount=10") > text.index("[Skirmish]")
    assert "[Video]\nScreenWidth=800" in text
