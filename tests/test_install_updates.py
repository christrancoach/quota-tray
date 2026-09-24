"""Per-user install/uninstall (temp folders, fake shortcut maker, real registry untouched) and update check."""
import io
import json

import pytest

from quota_core import install, updates


def test_install_copies_both_apps_and_makes_shortcuts(tmp_path):
    src = tmp_path / "dist"
    src.mkdir()
    (src / "QuotaTray.exe").write_bytes(b"tray")
    (src / "QuotaWidget.exe").write_bytes(b"widget")
    made = []
    dest, menu = tmp_path / "Programs" / "QuotaTray", tmp_path / "StartMenu"
    out = install.install(src, dest, menu, register=False, repoint_run=False,
                          shortcut=lambda link, target, desc: made.append((link.name, target.name)))
    assert [p.name for p in out] == ["QuotaTray.exe", "QuotaWidget.exe"]
    assert (dest / "QuotaWidget.exe").read_bytes() == b"widget"
    assert made == [("Quota Tray.lnk", "QuotaTray.exe"), ("Quota Widget.lnk", "QuotaWidget.exe")]


def test_install_needs_at_least_one_exe(tmp_path):
    with pytest.raises(FileNotFoundError):
        install.install(tmp_path, tmp_path / "d", tmp_path / "m", register=False, repoint_run=False,
                        shortcut=lambda *a: None)


def test_uninstall_removes_shortcuts_without_touching_real_settings(tmp_path):
    menu = tmp_path / "StartMenu"
    menu.mkdir()
    (menu / "Quota Tray.lnk").write_text("x")
    install.uninstall(tmp_path / "d", menu, deregister=False, run_values={}, remove_folder=False)
    assert not menu.exists()


def test_not_offered_when_running_from_source():
    assert install.running_exe_dir() is None and not install.is_installed_copy()


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("remote,expect", [("1.3.0", True), ("1.2.0", False), ("1.1.9", False), ("2.0", True)])
def test_update_check_compares_versions(monkeypatch, remote, expect):
    body = json.dumps({"version": remote, "url": "https://example.com/QuotaTray.zip", "notes": "n"}).encode()
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: FakeResp(body))
    found = updates.check("https://example.com/version.json", current="1.2.0")
    assert (found is not None) == expect
    if found:
        assert found.url == "https://example.com/QuotaTray.zip"


def test_update_check_is_quiet_when_off_or_broken(monkeypatch):
    assert updates.check(None) is None
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: FakeResp(b"not json"))
    assert updates.check("https://example.com/v.json") is None
    body = json.dumps({"version": "9.9", "url": "http://insecure.example/x"}).encode()
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: FakeResp(body))
    assert updates.check("https://example.com/v.json", current="1.0").url == "https://example.com/v.json"  # no http links


def test_update_check_reads_github_latest_release(monkeypatch):
    rel = {"tag_name": "v1.3.0", "html_url": "https://github.com/o/r/releases/tag/v1.3.0", "body": "Notes",
           "draft": False, "prerelease": False}
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: FakeResp(json.dumps(rel).encode()))
    found = updates.check("https://api.github.com/repos/o/r/releases/latest", current="1.2.0")
    assert found.version == "1.3.0" and found.url.endswith("/tag/v1.3.0") and found.notes == "Notes"
    rel["prerelease"] = True
    assert updates.check("https://api.github.com/repos/o/r/releases/latest", current="1.2.0") is None


def test_default_update_url_is_the_projects_release_feed(monkeypatch):
    from quota_core.config import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["update_url"] == "https://api.github.com/repos/christrancoach/quota-tray/releases/latest"
    assert updates.is_configured(DEFAULT_CONFIG["update_url"])
    calls = []
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: calls.append(1))
    assert updates.check("https://api.github.com/repos/OWNER/REPO/releases/latest") is None and calls == []
    assert updates.check("") is None and calls == []                      # empty = off


def test_real_shortcut_creation_in_a_temp_folder(tmp_path):
    target = tmp_path / "QuotaTray.exe"
    target.write_bytes(b"x")
    link = tmp_path / "Quota Tray.lnk"
    install.make_shortcut(link, target, "LLM subscription quota")
    assert link.is_file() and link.stat().st_size > 0
