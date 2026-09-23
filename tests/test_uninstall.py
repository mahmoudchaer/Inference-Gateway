from inference_gateway.uninstall import (
    PATH_EXPORT,
    PATH_MARKER,
    remove_profile_entry,
    remove_user_data,
)


def test_remove_profile_entry_removes_only_installer_lines(tmp_path):
    profile = tmp_path / ".zprofile"
    profile.write_text(f"export KEEP=1\n\n{PATH_MARKER}\n{PATH_EXPORT}\nexport ALSO_KEEP=1\n", encoding="utf-8")

    remove_profile_entry(profile)

    assert profile.read_text(encoding="utf-8") == "export KEEP=1\n\nexport ALSO_KEEP=1\n"


def test_remove_profile_entry_ignores_missing_file(tmp_path):
    remove_profile_entry(tmp_path / "missing")


def test_remove_user_data_deletes_everything(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.json").write_text("secret", encoding="utf-8")
    (data / "metrics.db").write_text("logs", encoding="utf-8")

    remove_user_data(data)

    assert not data.exists()
