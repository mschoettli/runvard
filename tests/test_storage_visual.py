from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "static" / "index.html"
MODERN_THEME_CSS = ROOT / "static" / "modern-theme.css"


def _function(html, name):
    match = re.search(
        rf"function {name}\([^)]*\)\{{.*?^\}}",
        html,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def test_storage_visual_uses_real_disks_and_caps_the_scene_at_six_items():
    html = INDEX_HTML.read_text()
    match = re.search(
        r"function modernStorageVisualState\(devices\)\{.*?^\}",
        html,
        flags=re.MULTILINE | re.DOTALL,
    )

    assert match is not None

    script = f"""
{match.group(0)}
const devices = [
  {{name:'nvme0n1', type:'disk'}},
  {{name:'nvme0n1p1', type:'part'}},
  {{name:'sda', type:'disk'}},
  {{name:'sdb', type:'disk'}},
  {{name:'sdc', type:'disk'}},
  {{name:'sdd', type:'disk'}},
  {{name:'sde', type:'disk'}},
  {{name:'sdf', type:'disk'}},
  {{name:'loop0', type:'loop'}},
];
const state = modernStorageVisualState(devices);
if (state.total !== 7) throw new Error(`Expected 7 disks, got ${{state.total}}`);
if (state.visible.length !== 5) throw new Error(`Expected 5 visible disks, got ${{state.visible.length}}`);
if (state.overflow !== 2) throw new Error(`Expected +2 overflow, got ${{state.overflow}}`);

const six = modernStorageVisualState(devices.filter(device => device.type === 'disk').slice(0, 6));
if (six.visible.length !== 6 || six.overflow !== 0) {{
  throw new Error(`Six disks must render directly: ${{JSON.stringify(six)}}`);
}}

const empty = modernStorageVisualState([]);
if (empty.total !== 0 || empty.visible.length !== 0 || empty.overflow !== 0) {{
  throw new Error(`Empty state is incorrect: ${{JSON.stringify(empty)}}`);
}}
"""
    subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_storage_visual_has_overflow_and_empty_styles():
    html = INDEX_HTML.read_text()
    css = MODERN_THEME_CSS.read_text()

    assert "summaryCard('Disks',modernStorageVisualState(d.devices).total" in html
    assert ".modern-bay-more" in css
    assert ".modern-storage-empty" in css
    assert ".modern-visual-storage .modern-visual-scene::after" in css


def test_primary_storage_volume_prefers_the_root_filesystem():
    html = INDEX_HTML.read_text()
    script = f"""
{_function(html, 'storageDeviceVolumes')}
{_function(html, 'primaryVolume')}
const disk = {{name:'sda', type:'disk', children:[
  {{name:'sda1', type:'part', fstype:'vfat', mountpoint:'/boot/efi'}},
  {{name:'sda2', type:'part', fstype:'ext4', mountpoint:'/'}},
  {{name:'sda3', type:'part', fstype:'swap', mountpoint:null}},
]}};
const selected = primaryVolume(disk);
if (selected.name !== 'sda2') throw new Error(`Expected root volume, got ${{selected.name}}`);
"""
    subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_storage_usage_combines_mounted_volumes_without_double_counting():
    html = INDEX_HTML.read_text()
    script = f"""
{_function(html, 'storageDeviceVolumes')}
{_function(html, 'storageMountForVolume')}
{_function(html, 'storageDeviceUsage')}
const disk = {{name:'sda', type:'disk', children:[
  {{name:'sda1', type:'part', fstype:'vfat', mountpoint:'/boot/efi'}},
  {{name:'sda2', type:'part', fstype:'ext4', mountpoint:'/'}},
]}};
const mounts = [
  {{device:'/dev/sda1', mountpoint:'/boot/efi', total:100, used:20, free:80}},
  {{device:'/dev/sda2', mountpoint:'/', total:900, used:300, free:600}},
  {{device:'/dev/sda2', mountpoint:'/', total:900, used:300, free:600}},
];
const usage = storageDeviceUsage(disk, mounts);
if (usage.total !== 1000 || usage.used !== 320 || usage.free !== 680 || usage.percent !== 32) {{
  throw new Error(`Unexpected usage: ${{JSON.stringify(usage)}}`);
}}
"""
    subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_modern_storage_uses_dedicated_drive_cards_and_responsive_grid():
    html = INDEX_HTML.read_text()
    css = MODERN_THEME_CSS.read_text()

    assert "renderModernDeviceCards(d.devices,mounted)" in html
    assert 'class="modern-drive-card"' in html
    assert 'class="modern-drive-usage-track"' in html
    assert 'class="modern-drive-volume-row"' in html
    assert ".modern-drive-grid" in css
    assert "grid-template-columns: 1fr;" in css
    assert "@media (min-width: 761px)" in css
    assert "minmax(20rem, 1fr)" in css
    assert "uiText(hiddenVolumes.length===1?'volume':'volumes')" in html
