"""Round-trip tests against an in-memory fake of the TMTools REST API.
Run: python3 -m unittest discover tools/automation-sync"""
import itertools
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sync  # noqa: E402


class FakeApi:
    def __init__(self):
        self.n = itertools.count(1)
        self.folders, self.autos, self.scripts, self.pages = {}, {}, {}, {}

    def _id(self):
        return f"id{next(self.n)}"

    def call(self, method, path, body=None):
        if path == "/api/v1/automations/folders":
            if method == "GET":
                return list(self.folders.values())
            r = {"id": self._id(), **body}
            self.folders[r["id"]] = r
            return r
        if path == "/api/v1/automations":
            if method == "GET":
                return [{**a, "last_triggered_at": "2026-11-21T00:00:00"} for a in self.autos.values()]
            r = {"id": self._id(), **body}
            self.autos[r["id"]] = r
            return r
        if m := re.fullmatch(r"/api/v1/automations/(\w+)", path):
            if method == "DELETE":
                del self.autos[m[1]]
            else:
                self.autos[m[1]].update(body)
            return None
        if path == "/api/v1/scripts":
            if method == "GET":
                return list(self.scripts.values())
            r = {"id": self._id(), **body}
            self.scripts[r["id"]] = r
            return r
        if m := re.fullmatch(r"/api/v1/scripts/(\w+)", path):
            if method == "DELETE":
                del self.scripts[m[1]]
            else:
                self.scripts[m[1]].update(body)
            return None
        if path == "/api/v1/button-pages":
            if method == "GET":
                return [dict(p) for p in self.pages.values()]
            r = {"id": self._id(), "buttons": [], **body}
            self.pages[r["id"]] = r
            return r
        if m := re.fullmatch(r"/api/v1/button-pages/(\w+)/buttons", path):
            r = {"id": self._id(), "page_id": m[1], **body}
            self.pages[m[1]]["buttons"].append(r)
            return r
        if m := re.fullmatch(r"/api/v1/button-pages/buttons/(\w+)", path):
            for p in self.pages.values():
                for b in list(p["buttons"]):
                    if b["id"] == m[1]:
                        p["buttons"].remove(b) if method == "DELETE" else b.update(body)
            return None
        if m := re.fullmatch(r"/api/v1/button-pages/(\w+)", path):
            if method == "DELETE":
                del self.pages[m[1]]
            else:
                self.pages[m[1]].update(body)
            return None
        raise AssertionError((method, path))


def seeded():
    api = FakeApi()
    top = api.call("POST", "/api/v1/automations/folders", {"name": "Timers", "parent_id": None})
    sub = api.call("POST", "/api/v1/automations/folders", {"name": "A/B Stage", "parent_id": top["id"]})
    api.call("POST", "/api/v1/automations", {
        "folder_id": sub["id"], "alias": "Countdown T-3", "enabled": False,
        "trigger_yaml": "platform: event\ntype: x\n", "condition_yaml": None,
        "action_yaml": "- action: timer.start\n  data:\n    id: t  \n"})
    api.call("POST", "/api/v1/automations", {
        "folder_id": None, "alias": "Root one", "enabled": True,
        "trigger_yaml": "t", "condition_yaml": "c: 1", "action_yaml": "a"})
    api.call("POST", "/api/v1/scripts", {"name": "Reset", "description": None, "action_yaml": "- x"})
    page = api.call("POST", "/api/v1/button-pages", {"name": "Main", "position": 0, "columns": 4})
    api.call("POST", f"/api/v1/button-pages/{page['id']}/buttons", {
        "label": "Go", "color": "#112233", "position": 0, "icon": None, "confirm": True, "action_yaml": "- go"})
    return api


def normal(api):
    """Snapshot with runtime-only fields already excluded."""
    return sync.fetch_snapshot(api)


class RoundTrip(unittest.TestCase):
    def test_export_import_into_empty_server_matches(self):
        src = seeded()
        with tempfile.TemporaryDirectory() as d:
            sync.write_snapshot(Path(d), normal(src))
            dst = FakeApi()
            n = sync.apply_snapshot(dst, sync.read_snapshot(Path(d)), log=lambda m: None)
            self.assertGreater(n, 0)
            self.assertEqual(normal(src), normal(dst))
            # idempotent
            self.assertEqual(sync.apply_snapshot(dst, sync.read_snapshot(Path(d)), log=lambda m: None), 0)

    def test_layout_and_no_runtime_fields(self):
        with tempfile.TemporaryDirectory() as d:
            sync.write_snapshot(Path(d), normal(seeded()))
            files = sorted(str(p.relative_to(d)) for p in Path(d).rglob("*.yaml"))
            self.assertIn("automations/timers/a-b-stage/countdown-t-3.yaml", files)
            self.assertIn("automations/root-one.yaml", files)
            text = (Path(d) / "automations/timers/a-b-stage/countdown-t-3.yaml").read_text()
            self.assertNotIn("last_triggered_at", text)
            self.assertNotIn("id", [l.split(":")[0] for l in text.splitlines()])
            self.assertIn("- A/B Stage", text)

    def test_update_prune_and_dry_run(self):
        src = seeded()
        with tempfile.TemporaryDirectory() as d:
            sync.write_snapshot(Path(d), normal(src))
            f = Path(d) / "automations/root-one.yaml"
            f.write_text(f.read_text().replace("enabled: true", "enabled: false"))
            want = sync.read_snapshot(Path(d))
            extra = src.call("POST", "/api/v1/automations", {
                "folder_id": None, "alias": "Stray", "enabled": True, "trigger_yaml": "t", "condition_yaml": None, "action_yaml": "a"})
            self.assertEqual(sync.apply_snapshot(src, want, dry=True, log=lambda m: None), 1)
            self.assertTrue(next(a for a in src.autos.values() if a["alias"] == "Root one")["enabled"])
            sync.apply_snapshot(src, want, log=lambda m: None)
            self.assertIn(extra["id"], src.autos)  # not pruned by default
            self.assertFalse(next(a for a in src.autos.values() if a["alias"] == "Root one")["enabled"])
            sync.apply_snapshot(src, want, prune=True, log=lambda m: None)
            self.assertNotIn(extra["id"], src.autos)

    def test_export_removes_stale_files(self):
        with tempfile.TemporaryDirectory() as d:
            api = seeded()
            sync.write_snapshot(Path(d), normal(api))
            for a in list(api.autos.values()):
                if a["alias"] == "Countdown T-3":
                    del api.autos[a["id"]]
            sync.write_snapshot(Path(d), normal(api))
            self.assertFalse((Path(d) / "automations/timers").exists())

    def test_invalid_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "automations").mkdir()
            (Path(d) / "automations/x.yaml").write_text("alias: x\n")
            with self.assertRaises(sync.SyncError):
                sync.read_snapshot(Path(d))


if __name__ == "__main__":
    unittest.main()
