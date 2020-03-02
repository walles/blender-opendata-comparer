#!/usr/bin/env python3

import sys
import json
import pprint
import zipfile
import traceback

from typing import Dict, NamedTuple, List, Iterable, Set

# Make a top list out of these
DEVICE_NAMES: List[str] = ["4870HQ", "9750H", "Max-Q"]


class Sample(NamedTuple):
    device_name: str
    device_type: str
    device_threads: int

    blender_version: str
    os_name: str
    scene_name: str

    render_time_seconds: float


class Device(NamedTuple):
    device_name: str
    device_threads: int


class Environment(NamedTuple):
    blender_version: str
    os_name: str
    scene_name: str


def process_entry_v1(entry: Dict) -> List[Sample]:
    data = entry["data"]
    blender_version = data["blender_version"]["version"]
    operating_system = data["system_info"]["system"]
    compute_devices = data["device_info"]["compute_devices"]
    device_type = data["device_info"]["device_type"]
    num_cpu_threads = data["device_info"]["num_cpu_threads"]

    if len(compute_devices) != 1:
        # Multiple compute devices, never mind
        return []
    device_name = compute_devices[0]
    if not device_name:
        # There are a few of these, just ignore them
        return []

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
                device_name=device_name,
                device_type=device_type,
                device_threads=num_cpu_threads,
                scene_name=scene_name,
                render_time_seconds=render_time_seconds,
            )
        )
    return samples


def process_entry_v2(entry: Dict) -> List[Sample]:
    data = entry["data"]
    blender_version = data["blender_version"]["version"]
    operating_system = data["system_info"]["system"]

    compute_devices = data["device_info"]["compute_devices"]
    if len(compute_devices) != 1:
        # Multiple compute devices or none (?), never mind
        return []
    compute_device = compute_devices[0]["name"]
    if not compute_device:
        # There are a few of these, just ignore them
        return []

    device_type = data["device_info"]["device_type"]
    num_cpu_threads = data["device_info"]["num_cpu_threads"]

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
                device_threads=num_cpu_threads,
                scene_name=scene_name,
                render_time_seconds=render_time_seconds,
            )
        )
    return samples


def process_entry_v3(entry: Dict) -> List[Sample]:
    samples: List[Sample] = []
    for data in entry["data"]:
        blender_version = data["blender_version"]["version"]
        operating_system = data["system_info"]["system"]
        compute_devices = data["device_info"]["compute_devices"]
        if len(compute_devices) != 1:
            # Multiple compute devices or none (?), never mind
            continue

        device_name = compute_devices[0]["name"]
        if not device_name:
            # There are a few of these, just ignore them
            continue

        device_type = compute_devices[0]["type"]
        num_cpu_threads = data["device_info"]["num_cpu_threads"]

        scene_name = data["scene"]["label"]
        render_time_seconds = data["stats"]["total_render_time"]
        samples.append(
            Sample(
                blender_version=blender_version,
                os_name=operating_system,
                device_name=device_name,
                device_type=device_type,
                device_threads=num_cpu_threads,
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
            elif entry["schema_version"] == "v2":
                samples += process_entry_v2(entry)
            elif entry["schema_version"] == "v3":
                samples += process_entry_v3(entry)
            else:
                pprint.pprint(entry, stream=sys.stderr)
                sys.exit("Unsupported schema version")
        except Exception:
            pprint.pprint(entry, stream=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            sys.exit(1)

    return samples


# List samples for all devices we're interested in
samples: List[Sample] = []
with zipfile.ZipFile("opendata-2020-02-21-063254+0000.zip") as opendata:
    for entry in opendata.infolist():
        if not entry.filename.endswith(".jsonl"):
            continue
        with opendata.open(entry) as jsonl:
            entry_samples = process_opendata(jsonl.readlines())

            for sample in entry_samples:
                # Filter out devices we're interested in
                match = False
                for device_name in DEVICE_NAMES:
                    if device_name in sample.device_name:
                        match = True
                if not match:
                    continue

                samples.append(sample)

print(f"Found {len(samples)} samples for the requested devices")

# Map devices to the fastest recorded rendering per scene
devices_to_fastest_per_scene: Dict[Device, Dict[str, float]] = {}
for sample in samples:
    device = Device(
        device_name=sample.device_name, device_threads=sample.device_threads,
    )

    scenes_dict = devices_to_fastest_per_scene.get(device, {})
    if not scenes_dict:
        # Not already present, add the new one
        devices_to_fastest_per_scene[device] = scenes_dict

    scene_name = sample.scene_name
    if scene_name not in scenes_dict:
        scenes_dict[scene_name] = sample.render_time_seconds
    else:
        current_best = scenes_dict[scene_name]
        if sample.render_time_seconds < current_best:
            scenes_dict[scene_name] = sample.render_time_seconds

print(f"Found {len(devices_to_fastest_per_scene)} matching devices")
if not devices_to_fastest_per_scene:
    sys.exit("No matching devices")

# List all known scenes
all_scenes: Set[str] = set()
for sample in samples:
    all_scenes.add(sample.scene_name)

# Figure out which common scenes we have
common_scenes = set(all_scenes)
for timings in devices_to_fastest_per_scene.values():
    common_scenes.intersection_update(timings.keys())

print(f"Matching devices has {len(common_scenes)}/{len(all_scenes)} scenes in common")
if not common_scenes:
    # FIXME: Handle this in a more informative manner
    sys.exit("No common scenes")

# For all devices, sum up the common-scene numbers
# FIXME: Just summing these will give more weight to complex scenes, do we want that?
devices_to_total_times: Dict[Device, float] = {}
for device, timings in devices_to_fastest_per_scene.items():
    sum = 0.0
    for scene in common_scenes:
        sum += devices_to_fastest_per_scene[device][scene]
    devices_to_total_times[device] = sum

# Rank devices per sum-of-common-scenes numbers
top_devices: List[Device] = sorted(
    list(devices_to_total_times.keys()), key=devices_to_total_times.get
)
for device in top_devices:
    print(f"{devices_to_total_times[device]:5d}: {device}")
