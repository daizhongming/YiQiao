import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "server" / "dashboard" / "public" / "fonts"
NOTICE_ROW = re.compile(r"^\|\s+`(?P<name>[^`]+)`\s+\|\s+`(?P<sha256>[A-F0-9]{64})`\s+\|$", re.MULTILINE)


def test_bundled_font_notice_covers_every_binary_by_hash():
    notice = (FONT_DIR / "FONT_LICENSES.md").read_text(encoding="utf-8")
    recorded = {match.group("name"): match.group("sha256") for match in NOTICE_ROW.finditer(notice)}
    actual = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest().upper()
        for path in FONT_DIR.iterdir()
        if path.suffix.lower() in {".ttf", ".woff2"}
    }

    assert recorded == actual


def test_dashboard_image_copies_font_license_material():
    dockerfile = (ROOT / "server" / "dashboard" / "Dockerfile").read_text(encoding="utf-8")

    assert re.search(
        r"^COPY server/dashboard/public/fonts/FONT_LICENSES\.md "
        r"server/dashboard/public/fonts/OFL-1\.1\.txt /usr/share/licenses/yiqiao/fonts/$",
        dockerfile,
        re.MULTILINE,
    )
    assert re.search(
        r"^COPY LICENSE /usr/share/licenses/yiqiao/fonts/RobotoMono-Apache-2\.0\.txt$",
        dockerfile,
        re.MULTILINE,
    )
    for target in (
        "FONT_LICENSES.md",
        "OFL-1.1.txt",
        "RobotoMono-Apache-2.0.txt",
    ):
        assert f"&& test -s /usr/share/licenses/yiqiao/fonts/{target}" in dockerfile
