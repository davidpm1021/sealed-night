from pathlib import Path
import xml.etree.ElementTree as ET

from app.engine.magebench_runtime import bridge_username, make_server_config


def test_server_config_rewrites_both_ports(tmp_path: Path):
    source = tmp_path / "source.xml"
    source.write_text(
        '<?xml version="1.0"?><config><server port="17171" secondaryBindPort="17179"/></config>',
        encoding="utf-8",
    )
    destination = tmp_path / "out.xml"
    make_server_config(source, destination, 18000)
    root = ET.parse(destination).getroot()
    server = root.find("server")
    assert server is not None
    assert server.get("port") == "18000"
    assert server.get("secondaryBindPort") == "18008"


def test_bridge_usernames_fit_xmage_default_constraints():
    name = bridge_username("AbC-123_very-long-player-id", "A")
    assert 3 <= len(name) <= 14
    assert all(ch.islower() or ch.isdigit() or ch == "_" for ch in name)
