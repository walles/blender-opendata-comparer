#!/usr/bin/env python3

import sys
import json
import pprint
import zipfile
import traceback

from typing import Dict, NamedTuple, List, Iterable


class Sample(NamedTuple):
    blender_version: str
    os_name: str
    device_name: str
    device_type: str
    scene_name: str
    render_time_seconds: float


def process_entry_v1(entry: Dict) -> List[Sample]:
    data = entry["data"]
    blender_version = data["blender_version"]["version"]
    operating_system = data["system_info"]["system"]
    compute_devices = data["device_info"]["compute_devices"]
    device_type = data["device_info"]["device_type"]
    if len(compute_devices) != 1:
        # Multiple compute devices, never mind
        return []

    compute_device = compute_devices[0]
    # FIXME: Look at only CPU devices
    print(compute_device)

    samples: List[Sample] = []
    for scene in data["scenes"]:
        if scene["stats"]["result"] != "OK":
            continue

        scene_name = scene["name"]
        render_time_seconds = scene["stats"]["total_render_time"]
        samples.append(
            Sample(
                blender_version=blender_version,
                os_name=operating_system,
                device_name=compute_device,
                device_type=device_type,
                scene_name=scene_name,
                render_time_seconds=render_time_seconds,
            )
        )
    return samples


def process_opendata(jsonl: Iterable[bytes]) -> List[Sample]:
    samples: List[Sample] = []
    for line in jsonl:
        entry = json.loads(line)
        try:
            if entry["schema_version"] == "v1":
                samples += process_entry_v1(entry)
            else:
                pprint.pprint(entry, stream=sys.stderr)
                sys.exit("Unsupported schema version")
        except Exception as e:
            pprint.pprint(entry, stream=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            sys.exit(1)

    return samples


samples: List[Sample] = []
with zipfile.ZipFile("opendata-2020-02-21-063254+0000.zip") as opendata:
    for entry in opendata.infolist():
        if not entry.filename.endswith(".jsonl"):
            continue
        with opendata.open(entry) as jsonl:
            samples += process_opendata(jsonl.readlines())

for sample in samples:
    pprint.pprint(sample)
