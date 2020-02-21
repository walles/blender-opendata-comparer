#!/usr/bin/env python3

import sys
import json
import pprint
import zipfile

from typing import Dict


def process_entry(entry: Dict):
    data = entry["data"]
    if type(data) is not list:
        data = [data]
    for data_point in data:
        blender_version = data_point["blender_version"]["version"]
        operating_system = data_point["system_info"]["system"]
        compute_devices = data_point["device_info"]["compute_devices"]
        if len(compute_devices) != 1:
            # Multiple compute devices, never mind
            return

        compute_device = compute_devices[0]
        if type(compute_device) is dict:
            compute_device = compute_device["name"]
        if compute_device != "AMD Ryzen 5 3500U with Radeon Vega Mobile Gfx":
            continue
        # FIXME: Look at only CPU devices
        print(compute_device)

        # FIXME: For each scene...
        for scene in data_point["scenes"]:
            scene_name = scene["name"]
            render_time_seconds = scene["stats"]["total_render_time"]
            t = {
                "blender_version": blender_version,
                "os": operating_system,
                "scene": scene_name,
                "render_time_s": render_time_seconds,
            }
            pprint.pprint(t)


def process_opendata(jsonl):
    for line in jsonl:
        entry = json.loads(line)
        try:
            process_entry(entry)
        except Exception as e:
            print(e, file=sys.stderr)
            pprint.pprint(entry, stream=sys.stderr)


with zipfile.ZipFile("opendata-2020-02-21-063254+0000.zip") as opendata:
    for entry in opendata.infolist():
        if not entry.filename.endswith(".jsonl"):
            continue
        with opendata.open(entry) as jsonl:
            process_opendata(jsonl.readlines())
