import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from storage_contract import exercise_backend_contract
from workflow_v2.filesystem import FilesystemStorage


class WorkflowV2FilesystemStorageContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_filesystem_backend_satisfies_shared_contract(self):
        exercise_backend_contract(
            self,
            lambda: FilesystemStorage(Path(self.tmp.name)),
        )


if __name__ == "__main__":
    unittest.main()
