"""
Shared setup for the tests and simulations. Each script puts the station folder (this folder's parent) on sys.path
itself, then imports this.
"""
import json
import os
import random
import shutil
import sys
import urllib.request

TESTS = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(TESTS)
HOME = os.path.expanduser("~")
# The station's state a simulation reads, copied once by freeze() so runs before and after a change see the same inputs
FROZEN_FILES = ("retrotv_manifest.json", "retrotv_index.json", "retrotv_schedule.json")


def station_modules():
    return [m for name, m in list(sys.modules.items()) if m and (name == "retrotv" or name.startswith("retrotv."))]


def set_everywhere(name, value):
    """Sets a module-level setting on every loaded station module that has it. A module that imported the setting
    from another has its own copy, so setting it in one place misses those."""
    hit = 0
    for module in station_modules():
        if hasattr(module, name):
            setattr(module, name, value)
            hit += 1
    if not hit:
        raise NameError(f"no station module has {name}")


def same_every_run(seed=7):
    """Makes a run repeatable: re-runs this script once with a fixed hash seed (so sets iterate in the same order;
    -I would ignore it, so the re-run goes without -I), and seeds every random.Random() the station makes for itself."""
    if os.environ.get("PYTHONHASHSEED") != "0":
        os.execve(sys.executable, [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]], {**os.environ, "PYTHONHASHSEED": "0"})

    class Seeded(random.Random):
        def __init__(self, x=None):
            super().__init__(seed if x is None else x)
    random.Random = Seeded
    random.seed(seed)


def offline():
    """No network: every request fails the way it would with the network down. Call once the station is imported."""
    def refuse(*args, **kwargs):
        raise OSError("tests don't use the network")
    urllib.request.urlopen = refuse
    for module in station_modules():
        if hasattr(module, "get_json"):
            module.get_json = refuse


# Where the station's names live, searched in this order by everything()
STATION_PARTS = ("station", "config", "rules", "cache", "lineup", "player", "overlays", "channels")


def everything():
    """One view of the station's modules, for tests written when they were one file: reading rs.NAME finds it in
    whichever module has it, and setting rs.NAME = value sets it in every module that has a copy, so the code that
    uses it sees it wherever that code lives."""
    import importlib
    parts = [importlib.import_module(f"retrotv.{name}") for name in STATION_PARTS]

    class Everything:
        def __getattr__(self, name):
            for part in parts:
                if hasattr(part, name):
                    return getattr(part, name)
            raise AttributeError(f"no station module has {name}")

        def __setattr__(self, name, value):
            set_everywhere(name, value)
    return Everything()


def redirect(old, new):
    """Points every path setting under `old` (a folder) at the same place under `new`, in every loaded station
    module. Settings like QUIZ_FILE are worked out from DATA_DIR when the module loads, so setting DATA_DIR alone
    would leave them on the live files. Returns how many settings moved."""
    old = old.rstrip("/")
    moved = 0
    for module in station_modules():
        for name, value in list(vars(module).items()):
            if name.isupper() and isinstance(value, str) and (value == old or value.startswith(old + "/")):
                setattr(module, name, new + value[len(old):])
                moved += 1
    return moved


def guard(rs):
    """A simulation must never delete real cache files: the real _remove_file deletes, and the real _load_manifest
    removes files its manifest doesn't list."""
    rs.CacheManager._remove_file = staticmethod(lambda e: None)


def freeze(dest):
    """Copies the station's state a simulation reads into dest, for comparing runs across a change."""
    os.makedirs(dest, exist_ok=True)
    for name in FROZEN_FILES:
        shutil.copy(os.path.join(HOME, name), os.path.join(dest, name))
    shutil.copytree(os.path.join(HOME, "retrotv_data"), os.path.join(dest, "retrotv_data"), dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("show_clips", "show_art", "team_logos", "*.tmp"))


def frozen(name):
    """A file of the station's state: from FROZEN when it's set, otherwise the live one."""
    return os.path.join(os.environ.get("FROZEN") or HOME, name)


def load_json(path):
    with open(path) as f:
        return json.load(f)
