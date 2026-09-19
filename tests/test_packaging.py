"""The packaging tooling: the icon, the version resource and the prune rules.

None of this can be fully proved without a Windows machine, but the parts
that are decidable here are worth locking down. A mistake in the prune rules
either breaks the packaged application on a machine nobody can debug from
here, or silently doubles the folder size.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from app.version import APP_NAME, VERSION, VERSION_TUPLE
from tools import prune_rules
from tools.make_icon import SIZES, build_ico
from tools.make_version_file import build_version_file

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class TestIcon:
    @pytest.fixture(scope="class")
    def icon(self, tmp_path_factory):
        return build_ico(tmp_path_factory.mktemp("icon") / "TimeTrack.ico")

    def test_it_is_a_valid_icon_container(self, icon):
        data = icon.read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        assert reserved == 0
        assert kind == 1  # 1 = icon, 2 = cursor
        assert count == len(SIZES)

    def test_every_windows_size_is_present(self, icon):
        """Windows picks a different size per context; a scaled-up icon
        looks blurry exactly where a user judges whether to trust software."""
        data = icon.read_bytes()
        _reserved, _kind, count = struct.unpack("<HHH", data[:6])
        widths = []
        for index in range(count):
            entry = data[6 + index * 16 : 22 + index * 16]
            width, _height, _colours, _res, _planes, _bpp, _size, _offset = struct.unpack(
                "<BBBBHHII", entry
            )
            widths.append(width or 256)  # 0 means 256 in the ICO format
        assert widths == list(SIZES)

    def test_each_entry_holds_a_real_png(self, icon):
        data = icon.read_bytes()
        _reserved, _kind, count = struct.unpack("<HHH", data[:6])
        for index in range(count):
            entry = data[6 + index * 16 : 22 + index * 16]
            *_rest, length, offset = struct.unpack("<BBBBHHII", entry)
            payload = data[offset : offset + length]
            assert payload[:8] == PNG_MAGIC
            assert len(payload) == length

    def test_the_entries_do_not_overlap_or_run_past_the_end(self, icon):
        data = icon.read_bytes()
        _reserved, _kind, count = struct.unpack("<HHH", data[:6])
        spans = []
        for index in range(count):
            entry = data[6 + index * 16 : 22 + index * 16]
            *_rest, length, offset = struct.unpack("<BBBBHHII", entry)
            spans.append((offset, offset + length))
        assert spans == sorted(spans)
        for (_start, end), (next_start, _next_end) in zip(spans, spans[1:]):
            assert end == next_start
        assert spans[-1][1] == len(data)


class TestVersionResource:
    @pytest.fixture
    def resource(self, tmp_path):
        return build_version_file(tmp_path / "version_info.txt").read_text()

    def test_it_carries_the_application_identity(self, resource):
        """An unsigned binary with no metadata looks more suspicious to
        endpoint protection than one that says what it is."""
        assert f"StringStruct('ProductName', '{APP_NAME}')" in resource
        assert f"StringStruct('OriginalFilename', '{APP_NAME}.exe')" in resource
        assert "CompanyName" in resource
        assert "LegalCopyright" in resource

    def test_the_version_matches_the_application(self, resource):
        assert f"StringStruct('FileVersion', '{VERSION}')" in resource
        assert f"filevers={VERSION_TUPLE}" in resource

    def test_it_is_the_structure_pyinstaller_expects(self, resource):
        """Evaluated with PyInstaller's own constructor names in scope.

        PyInstaller's real parser is Windows-only, so this checks the shape
        rather than the semantics - enough to catch a typo or a wrong field.
        """
        captured = {}

        def recorder(name):
            def build(*args, **kwargs):
                captured.setdefault(name, []).append((args, kwargs))
                return (name, args, kwargs)

            return build

        namespace = {
            name: recorder(name)
            for name in (
                "VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable",
                "StringStruct", "VarFileInfo", "VarStruct",
            )
        }
        eval(compile(resource, "version_info.txt", "eval"), namespace)  # noqa: S307
        assert "VSVersionInfo" in captured
        assert "FixedFileInfo" in captured
        assert len(captured["StringStruct"]) == 8


class TestPruneRules:
    @pytest.mark.parametrize(
        "path",
        [
            "PySide6/Qt6Core.dll",
            "PySide6/Qt6Gui.dll",
            "PySide6/Qt6Widgets.dll",
            "PySide6/Qt6Network.dll",  # the single-instance guard needs it
            "PySide6/QtWidgets.pyd",
            "PySide6/plugins/platforms/qwindows.dll",
            "PySide6/plugins/styles/qmodernwindowsstyle.dll",
            "PySide6/plugins/imageformats/qico.dll",
            "PySide6/plugins/imageformats/qpng.dll",
            "PySide6/Qt/lib/libQt6Widgets.so.6",
            "base_library.zip",
            "openpyxl/cell/_writer.py",
        ],
    )
    def test_things_the_application_needs_are_kept(self, path):
        assert not prune_rules.should_drop(path)

    @pytest.mark.parametrize(
        "path",
        [
            "PySide6/Qt6Qml.dll",
            "PySide6/Qt6Quick.dll",
            "PySide6/Qt6QuickControls2.dll",
            "PySide6/Qt6VirtualKeyboard.dll",
            "PySide6/Qt6Pdf.dll",
            "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
            "PySide6/plugins/imageformats/qpdf.dll",
            "PySide6/plugins/imageformats/qtiff.dll",
            "PySide6/plugins/imageformats/qwebp.dll",
            "PySide6/qmltooling/qmldbg_debugger.dll",
            "PySide6/assistant.exe",
            "PySide6/linguist.exe",
            "PySide6/qmlls.exe",
            "PySide6/Qt/lib/libQt6Qml.so.6",
        ],
    )
    def test_things_it_never_touches_are_dropped(self, path):
        assert prune_rules.should_drop(path)

    def test_windows_and_unix_separators_are_both_understood(self):
        assert prune_rules.should_drop("PySide6\\Qt6Qml.dll")
        assert prune_rules.should_drop("PySide6/Qt6Qml.dll")

    def test_the_software_opengl_fallback_can_be_kept_on_request(self, monkeypatch):
        """There is no way to test this on the target machine from here, so
        it stays a switch the user can flip rather than a silent decision."""
        assert prune_rules.should_drop("PySide6/opengl32sw.dll")
        monkeypatch.setenv(prune_rules.KEEP_OPENGL_ENV, "1")
        assert not prune_rules.should_drop("PySide6/opengl32sw.dll")

    def test_a_library_sharing_a_tool_name_is_not_dropped_by_accident(self):
        """'uic' and 'rcc' are tool names; nested files must survive."""
        assert not prune_rules.should_drop("PySide6/Qt/lib/uic.so")
        assert not prune_rules.should_drop("some/nested/rcc.dll")
        assert prune_rules.should_drop("uic.exe")

    def test_prune_filters_a_toc(self):
        toc = [
            ("PySide6/Qt6Core.dll", "/src/Qt6Core.dll", "BINARY"),
            ("PySide6/Qt6Qml.dll", "/src/Qt6Qml.dll", "BINARY"),
        ]
        assert [entry[0] for entry in prune_rules.prune(toc)] == ["PySide6/Qt6Core.dll"]


class TestSpecFile:
    @pytest.fixture(scope="class")
    def spec(self):
        return Path("TimeTrack.spec").read_text()

    def test_it_builds_one_folder_not_one_file(self, spec):
        """One-file builds self-extract at startup, which is what malware
        packers do and a known antivirus trigger."""
        assert "COLLECT(" in spec
        assert "exclude_binaries=True" in spec

    def test_upx_compression_is_off(self, spec):
        """UPX is itself an antivirus trigger."""
        assert "upx=False" in spec
        assert "upx=True" not in spec

    def test_it_builds_a_windowed_application(self, spec):
        assert "console=False" in spec

    def test_the_heavy_qt_modules_are_excluded(self, spec):
        for module in ("QtWebEngineCore", "QtQuick", "Qt3DCore", "QtMultimedia", "QtCharts"):
            assert f"PySide6.{module}" in spec, module

    def test_qtnetwork_is_not_excluded(self, spec):
        """The single-instance guard uses QLocalServer from QtNetwork."""
        assert "PySide6.QtNetwork" not in spec
