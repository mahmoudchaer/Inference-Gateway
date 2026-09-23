import pytest

import inference_gateway.update as updater
from inference_gateway.update import artifact_name, checksum_for, version_key


def test_artifact_name_for_supported_platforms():
    assert artifact_name("Darwin", "arm64") == "inference-gateway-macos-arm64"
    assert artifact_name("Darwin", "x86_64") == "inference-gateway-macos-x64"
    assert artifact_name("Windows", "AMD64") == "inference-gateway-windows-x64.exe"


def test_artifact_name_rejects_unsupported_platform():
    with pytest.raises(RuntimeError, match="not available"):
        artifact_name("Linux", "x86_64")


def test_checksum_for_accepts_standard_and_binary_markers():
    contents = "abc  other-file\ndef *inference-gateway-macos-arm64\n"
    assert checksum_for(contents, "inference-gateway-macos-arm64") == "def"


def test_checksum_for_rejects_missing_artifact():
    with pytest.raises(RuntimeError, match="missing"):
        checksum_for("abc  other-file\n", "missing-file")


def test_version_key_compares_release_versions():
    assert version_key("v0.1.10") > version_key("0.1.9")


def test_ssl_context_uses_bundled_ca_file(monkeypatch):
    seen = {}
    expected = object()
    monkeypatch.setattr(updater.certifi, "where", lambda: "/bundled/cacert.pem")

    def fake_context(*, cafile):
        seen["cafile"] = cafile
        return expected

    monkeypatch.setattr(updater.ssl, "create_default_context", fake_context)

    assert updater._ssl_context() is expected
    assert seen["cafile"] == "/bundled/cacert.pem"
