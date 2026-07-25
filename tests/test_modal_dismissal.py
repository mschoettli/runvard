from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1] / "static" / "index.html"
).read_text()


def test_main_modal_closes_only_through_its_close_button():
    assert '<button class="modal-close" onclick="closeModal()">✕</button>' in INDEX
    assert "$('#overlay').onclick" not in INDEX
