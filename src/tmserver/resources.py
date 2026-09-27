"""Read the small, immutable protocol tables shipped with the server."""

import json
from importlib.resources import files


def read_text(name):
    return files("tmserver").joinpath("data", name).read_text(encoding="utf8")


def read_json(name):
    return json.loads(read_text(name))
