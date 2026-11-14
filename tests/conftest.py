"""Keep all temporary test artifacts within the writable workspace on Windows."""
from pathlib import Path
import tempfile

TEST_TEMP = Path(__file__).parents[1] / "artifacts" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TEST_TEMP)
