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

    def _embed_for(self, snapshot):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "printer_status.json"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            with patch.object(printer_status, "PRINTER_STATUS_PATH", path):
                return printer_status.build_printer_status_embed()

    def test_offline_takes_priority_over_low_ink(self):
        old_timestamp = datetime.now(timezone.utc) - timedelta(hours=2)
        embed = self._embed_for(
            self._snapshot(
                timestamp=old_timestamp.isoformat(),
                last_checked=datetime.now(timezone.utc).isoformat(),
                online=False,
            )
        )
        self.assertIn("seems to be offline (last seen 2 hours ago)", embed.description)
        self.assertIn("last known ink levels", embed.description)
        self.assertEqual(embed.fields[0].value, "Offline")
        self.assertEqual(embed.colour.value, 0x95A5A6)
        # Recent check, so the reading is not flagged as out of date
        self.assertNotIn("may be out of date", embed.footer.text)

    def test_offline_without_previous_reading(self):
        embed = self._embed_for({
            "supplies": [],
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "online": False,
        })
        self.assertEqual(embed.description, "🔌 The printer seems to be offline.")

    def test_levels_shown_as_coloured_bars(self):
        embed = self._embed_for(self._snapshot(supplies=[
            {"name": "cyan ink", "percentage": 50, "color": "#00FFFF"},
            {"name": "black ink", "percentage": 20, "color": "#000000"},
            {"name": "Photo ink", "percentage": 3, "color": "#123456"},
            {"name": "magenta ink", "percentage": None},
        ]))
        levels = embed.fields[1].value
        self.assertIn("**Cyan ink**: 50%\n" + "🟦" * 5 + "⬜" * 5, levels)
        self.assertIn("**Black ink**: 20% ⚠️\n" + "⬛" * 2 + "⬜" * 8, levels)
        # Unknown colour falls back to green, and a nearly empty supply keeps one segment
        self.assertIn("🟩" + "⬜" * 9, levels)
        self.assertIn("**Magenta ink**: level unknown", levels)


class PrinterMonitorTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, "scripts")
        import printer_monitor
        self.monitor = printer_monitor

    def test_failed_poll_keeps_last_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            data_file = Path(directory) / "printer_status.json"
            data_file.write_text(json.dumps({
                "timestamp": "2026-01-01T00:00:00Z",
                "supplies": [{"name": "Black", "percentage": 75}],
                "online": True,
            }), encoding="utf-8")
            with patch.object(self.monitor, "DATA_DIR", Path(directory)), \
                    patch.object(self.monitor, "DATA_FILE", data_file), \
                    patch.object(self.monitor, "query_printer", side_effect=OSError("unreachable")):
                self.assertEqual(self.monitor.main(), 1)
            snapshot = json.loads(data_file.read_text(encoding="utf-8"))
        self.assertFalse(snapshot["online"])
        self.assertEqual(snapshot["last_error"], "unreachable")
        self.assertEqual(snapshot["timestamp"], "2026-01-01T00:00:00Z")
        self.assertEqual(snapshot["supplies"], [{"name": "Black", "percentage": 75}])
        self.assertIn("last_checked", snapshot)


if __name__ == "__main__":
    unittest.main()
