"""External footprint repository manager for klepcbgen.

Reads `footprints.json` (repo config) and shallow-clones the listed Git repos
into a cache dir, then serves the real KiCad `.kicad_mod` footprint text for
the parts the generator needs (stabilizers, and later switches/diodes).

The whole point is to use REAL footprints from community keyboard library
repos (keebio, ai03, etc.) instead of the hand-rolled placeholder templates.
Repos are pinned by config; nothing here hardcodes a URL.

Usage:
    from footprint_lib import FootprintLib
    lib = FootprintLib()                 # reads footprints.json, clones as needed
    text = lib.stabilizer("cherry_mx")   # returns the .kicad_mod file text
"""
import json
import os
import shutil
import subprocess
import sys

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "footprints.json")


def _expand(path):
    return os.path.abspath(os.path.expanduser(path))


class FootprintLib:
    def __init__(self, config_file=CONFIG_FILE, cache_dir=None):
        with open(config_file, encoding="utf-8") as f:
            self.config = json.load(f)
        self.cache_dir = _expand(cache_dir or self.config.get(
            "cache_dir", "~/.klepcbgen/footprint-repos"))
        self._cloned = {}   # repo key -> local dir

    # --- repo management -------------------------------------------------
    def ensure_repo(self, key):
        """Shallow-clone the repo identified by `key` if not already cached.
        Returns the local repo directory. Raises if the repo is missing from
        the config or the clone fails."""
        if key in self._cloned:
            return self._cloned[key]
        info = self.config["repos"].get(key)
        if not info:
            raise ValueError(f"footprints.json has no repo named '{key}'")
        d = os.path.join(self.cache_dir, key)
        if os.path.isdir(os.path.join(d, ".git")):
            self._cloned[key] = d
            return d
        os.makedirs(self.cache_dir, exist_ok=True)
        url = info["git"]
        ref = info.get("ref", "master")
        tmp = d + ".tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", ref, url, tmp],
            check=True, capture_output=True,
        )
        os.rename(tmp, d)
        self._cloned[key] = d
        return d

    def _footprint_path(self, repo, filename):
        d = self.ensure_repo(repo)
        return os.path.join(d, filename)

    # --- stabilizer footprints ------------------------------------------
    def stabilizer(self, switch_type):
        """Return the raw .kicad_mod text of the stabilizer footprint for a
        given switch type ('cherry_mx' | 'alps' | 'choc')."""
        key = (switch_type or "cherry_mx").lower()
        st = self.config["stabilizers"].get(key)
        if not st:
            st = self.config["stabilizers"].get("cherry_mx")
        repo = st["repo"]
        filename = st["footprint"]
        p = self._footprint_path(repo, filename)
        if not os.path.isfile(p):
            raise FileNotFoundError(
                f"stabilizer footprint '{filename}' not found in repo "
                f"'{repo}' (looked at {p})")
        with open(p, encoding="utf-8") as f:
            return f.read()

    def stabilizer_meta(self, switch_type):
        """Return the config dict (repo/footprint/per_stem) for a switch type."""
        key = (switch_type or "cherry_mx").lower()
        return self.config["stabilizers"].get(key) or \
            self.config["stabilizers"]["cherry_mx"]
