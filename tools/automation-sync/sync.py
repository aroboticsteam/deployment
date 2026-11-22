#!/usr/bin/env python3
"""Export / import TMTools automations, scripts and button pages as YAML files.

The database stays the runtime source of truth; this keeps a reviewable copy in
git and restores it. It talks to the normal REST API with an API key, so it runs
anywhere and needs no database access. There is deliberately no automatic
two-way sync: ``export`` overwrites the files, ``import`` overwrites the server.

    sync.py export [--dir DATA] [--url URL]     # DB -> files
    sync.py import [--dir DATA] [--dry-run] [--prune]   # files -> DB

Layout under DATA (default ./data):

    automations/<folder>/<sub>/<alias>.yaml   alias, enabled, trigger_yaml, condition_yaml, action_yaml, folder: [..]
    scripts/<name>.yaml                       name, description, action_yaml
    button-pages/<page>.yaml                  name, position, columns, buttons: [..]

Folders are identified by their name path (the ``folder`` list in each file),
never by database id; directory names are only for browsing. ``last_triggered_at``,
ids and timestamps are runtime-only and never written. Empty folders are not
exported. Auth: ``TMTOOLS_API_KEY`` or ``~/.config/tmtools/api-key``;
server: ``TMTOOLS_URL`` or ``--url``. Needs PyYAML.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

AUTO_FIELDS = ("alias", "enabled", "trigger_yaml", "condition_yaml", "action_yaml")
BUTTON_FIELDS = ("label", "color", "position", "icon", "confirm", "action_yaml")


class SyncError(Exception):
    pass


# ── HTTP ───────────────────────────────────────────────────────────────────


class Api:
    def __init__(self, base: str, key: str, insecure: bool = False) -> None:
        self.base = base.rstrip("/")
        self.key = key
        self.ctx = ssl._create_unverified_context() if insecure else None

    def call(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise SyncError(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')[:300]}") from None
        except urllib.error.URLError as e:
            raise SyncError(f"{method} {path}: {e.reason}") from None
        return json.loads(raw) if raw else None


# ── Snapshots (pure data, keyed by names, no ids) ──────────────────────────


def fetch_snapshot(api: Any) -> dict[str, Any]:
    folders = api.call("GET", "/api/v1/automations/folders")
    by_id = {f["id"]: f for f in folders}

    def path_of(fid: str | None) -> list[str]:
        out: list[str] = []
        while fid:
            f = by_id[fid]
            out.append(f["name"])
            fid = f["parent_id"]
        return out[::-1]

    autos: dict[tuple, dict] = {}
    for a in api.call("GET", "/api/v1/automations"):
        item = {"folder": path_of(a["folder_id"]), **{k: a.get(k) for k in AUTO_FIELDS}}
        key = (tuple(item["folder"]), item["alias"])
        if key in autos:
            raise SyncError(f"duplicate automation {item['alias']!r} in folder {item['folder']}; rename one")
        autos[key] = item
    scripts = {s["name"]: {k: s.get(k) for k in ("name", "description", "action_yaml")}
               for s in api.call("GET", "/api/v1/scripts")}
    pages: dict[str, dict] = {}
    for p in api.call("GET", "/api/v1/button-pages"):
        buttons = sorted(p.get("buttons", []), key=lambda b: (b["position"], b["label"]))
        pages[p["name"]] = {
            "name": p["name"], "position": p["position"], "columns": p["columns"],
            "buttons": [{k: b.get(k) for k in BUTTON_FIELDS} for b in buttons],
        }
    return {"automations": autos, "scripts": scripts, "pages": pages}


# ── Files ──────────────────────────────────────────────────────────────────


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unnamed"


def _str_rep(dumper: yaml.Dumper, value: str) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, _str_rep)


def dump(obj: Any) -> str:
    return yaml.dump(obj, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


def _unique_name(taken: set[str], base: str) -> str:
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    taken.add(name)
    return name


def write_snapshot(root: Path, snap: dict[str, Any]) -> None:
    for sub in ("automations", "scripts", "button-pages"):
        d = root / sub
        if d.exists():
            for f in d.rglob("*.yaml"):
                f.unlink()
    taken: dict[Path, set[str]] = {}
    for (folder, alias), item in sorted(snap["automations"].items()):
        d = root / "automations" / Path(*[slug(p) for p in folder]) if folder else root / "automations"
        d.mkdir(parents=True, exist_ok=True)
        name = _unique_name(taken.setdefault(d, set()), slug(alias))
        (d / f"{name}.yaml").write_text(dump(item))
    for kind, sub, items in (("script", "scripts", snap["scripts"]), ("page", "button-pages", snap["pages"])):
        d = root / sub
        d.mkdir(parents=True, exist_ok=True)
        names: set[str] = set()
        for key, item in sorted(items.items()):
            (d / f"{_unique_name(names, slug(key))}.yaml").write_text(dump(item))
    for d in sorted((root / "automations").rglob("*"), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()


def read_snapshot(root: Path) -> dict[str, Any]:
    def load(path: Path) -> dict:
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict):
            raise SyncError(f"{path}: expected a mapping")
        return data

    autos: dict[tuple, dict] = {}
    for f in sorted((root / "automations").rglob("*.yaml")):
        d = load(f)
        missing = [k for k in ("alias", "trigger_yaml", "action_yaml") if not d.get(k)]
        if missing:
            raise SyncError(f"{f}: missing {', '.join(missing)}")
        folder = d.get("folder") or []
        if not isinstance(folder, list):
            raise SyncError(f"{f}: 'folder' must be a list of names")
        item = {"folder": [str(x) for x in folder], "enabled": True, "condition_yaml": None}
        item.update({k: d[k] for k in AUTO_FIELDS if k in d})
        key = (tuple(item["folder"]), item["alias"])
        if key in autos:
            raise SyncError(f"{f}: duplicate automation {item['alias']!r} in {item['folder']}")
        autos[key] = item
    scripts, pages = {}, {}
    for f in sorted((root / "scripts").glob("*.yaml")):
        d = load(f)
        scripts[d["name"]] = {"name": d["name"], "description": d.get("description"), "action_yaml": d["action_yaml"]}
    for f in sorted((root / "button-pages").glob("*.yaml")):
        d = load(f)
        buttons = [{"color": "#374151", "position": i, "icon": None, "confirm": False, **b}
                   for i, b in enumerate(d.get("buttons") or [])]
        labels = [b["label"] for b in buttons]
        if len(set(labels)) != len(labels):
            raise SyncError(f"{f}: duplicate button labels")
        pages[d["name"]] = {"name": d["name"], "position": d.get("position", 0),
                            "columns": d.get("columns", 4), "buttons": buttons}
    return {"automations": autos, "scripts": scripts, "pages": pages}


# ── Import ─────────────────────────────────────────────────────────────────


def apply_snapshot(api: Any, want: dict, dry: bool = False, prune: bool = False, log=print) -> int:
    """Make the server match ``want``. Returns the number of changes."""
    changes = 0

    def do(msg: str, method: str, path: str, body: Any = None) -> Any:
        nonlocal changes
        changes += 1
        log(("would " if dry else "") + msg)
        return None if dry else api.call(method, path, body)

    # Folders: ensure every wanted path exists, creating parents first.
    folders = api.call("GET", "/api/v1/automations/folders")
    ids: dict[tuple, str | None] = {(): None}
    by_id = {f["id"]: f for f in folders}

    def path_of(f: dict) -> tuple:
        out, cur = [], f
        while cur:
            out.append(cur["name"])
            cur = by_id.get(cur["parent_id"])
        return tuple(out[::-1])

    for f in folders:
        ids[path_of(f)] = f["id"]
    for key in sorted(want["automations"]):
        folder = key[0]
        for i in range(1, len(folder) + 1):
            sub = folder[:i]
            if sub not in ids:
                created = do(f"create folder {'/'.join(sub)}", "POST", "/api/v1/automations/folders",
                             {"name": sub[-1], "parent_id": ids[sub[:-1]]})
                ids[sub] = created["id"] if created else f"(new:{'/'.join(sub)})"

    have = {}
    for a in api.call("GET", "/api/v1/automations"):
        have[(path_of(by_id[a["folder_id"]]) if a["folder_id"] else (), a["alias"])] = a
    for key, item in sorted(want["automations"].items()):
        body = {"folder_id": ids[key[0]], **{k: item.get(k) for k in AUTO_FIELDS}}
        cur = have.get(key)
        label = f"automation {'/'.join(key[0] + (key[1],))}"
        if cur is None:
            do(f"create {label}", "POST", "/api/v1/automations", body)
        elif any(cur.get(k) != body[k] for k in AUTO_FIELDS):
            do(f"update {label}", "PUT", f"/api/v1/automations/{cur['id']}", body)
    if prune:
        for key, cur in sorted(have.items()):
            if key not in want["automations"]:
                do(f"delete automation {'/'.join(key[0] + (key[1],))}", "DELETE", f"/api/v1/automations/{cur['id']}")

    have_s = {s["name"]: s for s in api.call("GET", "/api/v1/scripts")}
    for name, item in sorted(want["scripts"].items()):
        cur = have_s.get(name)
        if cur is None:
            do(f"create script {name}", "POST", "/api/v1/scripts", item)
        elif any(cur.get(k) != item[k] for k in ("description", "action_yaml")):
            do(f"update script {name}", "PUT", f"/api/v1/scripts/{cur['id']}", item)
    if prune:
        for name, cur in sorted(have_s.items()):
            if name not in want["scripts"]:
                do(f"delete script {name}", "DELETE", f"/api/v1/scripts/{cur['id']}")

    have_p = {p["name"]: p for p in api.call("GET", "/api/v1/button-pages")}
    for name, item in sorted(want["pages"].items()):
        cur = have_p.get(name)
        meta = {k: item[k] for k in ("name", "position", "columns")}
        if cur is None:
            created = do(f"create button page {name}", "POST", "/api/v1/button-pages", meta)
            page_id, have_b = (created["id"] if created else None), {}
        else:
            page_id = cur["id"]
            have_b = {b["label"]: b for b in cur.get("buttons", [])}
            if any(cur[k] != meta[k] for k in meta):
                do(f"update button page {name}", "PUT", f"/api/v1/button-pages/{page_id}", meta)
        for b in item["buttons"]:
            curb = have_b.get(b["label"])
            if curb is None:
                do(f"create button {name}/{b['label']}", "POST", f"/api/v1/button-pages/{page_id}/buttons", b)
            elif any(curb.get(k) != b[k] for k in BUTTON_FIELDS):
                do(f"update button {name}/{b['label']}", "PUT", f"/api/v1/button-pages/buttons/{curb['id']}", b)
        if prune:
            for label, curb in sorted(have_b.items()):
                if label not in {b["label"] for b in item["buttons"]}:
                    do(f"delete button {name}/{label}", "DELETE", f"/api/v1/button-pages/buttons/{curb['id']}")
    if prune:
        for name, cur in sorted(have_p.items()):
            if name not in want["pages"]:
                do(f"delete button page {name}", "DELETE", f"/api/v1/button-pages/{cur['id']}")
    return changes


# ── CLI ────────────────────────────────────────────────────────────────────


def _api_key() -> str:
    key = os.environ.get("TMTOOLS_API_KEY")
    if not key:
        p = Path.home() / ".config/tmtools/api-key"
        key = p.read_text().strip() if p.exists() else ""
    if not key:
        raise SyncError("set TMTOOLS_API_KEY or put a key in ~/.config/tmtools/api-key")
    return key


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["export", "import"])
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parents[2] / "data"))
    ap.add_argument("--url", default=os.environ.get("TMTOOLS_URL"))
    ap.add_argument("--insecure", action="store_true", help="skip TLS verification (self-signed server)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune", action="store_true", help="import: also delete server items missing from the files")
    a = ap.parse_args(argv)
    try:
        if not a.url:
            raise SyncError("pass --url or set TMTOOLS_URL")
        api = Api(a.url, _api_key(), a.insecure)
        root = Path(a.dir)
        if a.command == "export":
            snap = fetch_snapshot(api)
            write_snapshot(root, snap)
            print(f"exported {len(snap['automations'])} automations, {len(snap['scripts'])} scripts, "
                  f"{len(snap['pages'])} button pages to {root}")
        else:
            n = apply_snapshot(api, read_snapshot(root), dry=a.dry_run, prune=a.prune)
            print(f"{n} change(s){' (dry run)' if a.dry_run else ''}")
    except SyncError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
