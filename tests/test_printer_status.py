import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "src")

from bot import printer_status


class PrinterStatusEmbedTests(unittest.TestCase):
    def _snapshot(self, **overrides):
        snapshot = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "printer_state": "idle",
            "supplies": [
                {"name": "Black", "percentage": 75},
                {"name": "Cyan", "percentage": 12},
            ],
        }
        snapshot.update(overrides)
        return snapshot

    def test_embed_calls_out_low_supplies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "printer_status.json"
            path.write_text(json.dumps(self._snapshot()), encoding="utf-8")
            with patch.object(printer_status, "PRINTER_STATUS_PATH", path):
                embed = printer_status.build_printer_status_embed()
        self.assertIn("Ink pantry alert", embed.description)
        self.assertEqual(embed.colour.value, 0xE67E22)
        self.assertIn("Cyan", embed.fields[2].value)

    def test_embed_marks_old_snapshot_stale(self):
        old_timestamp = datetime.now(timezone.utc) - timedelta(minutes=16)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "printer_status.json"
            path.write_text(
                json.dumps(
                    self._snapshot(
                        timestamp=old_timestamp.isoformat(),
                        supplies=[{"name": "Black", "percentage": 75}],
                    )
                ),
                encoding="utf-8",
            )
            with patch.object(printer_status, "PRINTER_STATUS_PATH", path):
                embed = printer_status.build_printer_status_embed()
        self.assertIn("last known", embed.description)
        self.assertIn("may be out of date", embed.footer.text)

    def test_missing_snapshot_returns_fallback(self):
        with patch.object(printer_status, "PRINTER_STATUS_PATH", Path("/does/not/exist")):
            embed = printer_status.build_printer_status_embed()
        self.assertIn("don't have a printer update", embed.description)


if __name__ == "__main__":
    unittest.main()
