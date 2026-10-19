from pathlib import Path

_current_folder = Path(__file__).parent
_FILENAME_EVEUNIVERSE_TESTDATA = "eveuniverse.json"


def test_data_filename():
    return str(_current_folder / _FILENAME_EVEUNIVERSE_TESTDATA)


def test_image_filename():
    return str(_current_folder / "test_image.jpg")
