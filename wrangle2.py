#!/usr/bin/env python3

import sys
import json
import pprint
import zipfile
import traceback

from typing import Dict, NamedTuple, List, Iterable, Set

# Make a top list out of these
DEVICE_NAMES: List[str] = ["4870HQ", "9750H", "Max-Q", "3500U", "Vega 8 Mobile"]
MIN_COMMON_SCENES_COUNT = 3


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


def get_scene_counts(
    devices_to_fastest_per_scene: Dict[Device, Dict[str, float]]
) -> Dict[str, int]:
    scene_counts: Dict[str, int] = {}
    for timings in devices_to_fastest_per_scene.values():
        for scene in timings.keys():
            scene_counts[scene] = scene_counts.get(scene, 0) + 1
    return scene_counts


def get_all_scenes(devices_to_fastest_per_scene: Dict[Device, Dict[str, float]]) -> set:
    all_scenes: Set[str] = set()
    for timings in devices_to_fastest_per_scene.values():
        all_scenes.update(timings.keys())
    return all_scenes


def censor_uncommon_devices(
    devices_to_fastest_per_scene: Dict[Device, Dict[str, float]], min_count: int
) -> None:
    """
    For as long as the devices in the dics have less than min_count
    scenes in common, drop one device at a time.

    This function modifies the dict.

    The scene to drop is picked like this:
    * Find the most common scenes among the devices
    * Ignore the scenes that all devices have in common
    * From the rest, find the most common scene
    * Drop one device that does not have that most common scene
    """
    while True:
        if len(devices_to_fastest_per_scene) <= 1:
            sys.exit(
                f"Unable to find any set of devices with {min_count} scenes in common"
            )

        scene_counts = get_scene_counts(devices_to_fastest_per_scene)
        common_scenes: Set[str] = set()
        for scene, count in scene_counts.items():
            if count == len(devices_to_fastest_per_scene):
                common_scenes.add(scene)

        if len(common_scenes) >= min_count:
            return

        # Ignore the scenes that everybody has in common
        for scene in common_scenes:
            del scene_counts[scene]

        assert scene_counts  # If this fails: WTF?

        # Find the most common remaining scene
        most_common_incomplete_scene = sorted(
            scene_counts.keys(), key=scene_counts.get
        )[-1]

        # Find a device that doesn't have that most common scene...
        for device, timings in devices_to_fastest_per_scene.items():
            if most_common_incomplete_scene in timings:
                continue

            # ... and drop it
            print(
                f"Dropping {device} lacking timings for {most_common_incomplete_scene}"
            )
            del devices_to_fastest_per_scene[device]
            break


def to_duration_string(seconds: float) -> str:
    seconds_count = int(seconds) % 60
    minutes_count = (int(seconds) // 60) % 60
    hours_count = int(seconds) // 3600
    if hours_count > 0:
        return f"{hours_count:2d}h{minutes_count:02d}m"
    return f"{minutes_count:2d}m{seconds_count:02d}s"


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

censor_uncommon_devices(devices_to_fastest_per_scene, MIN_COMMON_SCENES_COUNT)

print(f"Found {len(devices_to_fastest_per_scene)} matching devices")
if not devices_to_fastest_per_scene:
    sys.exit("FAILED: No matching devices")

scene_counts = get_scene_counts(devices_to_fastest_per_scene)
top_scenes: List[str] = sorted(scene_counts.keys(), key=scene_counts.get, reverse=True)
print("Most common scenes (with counts):")
for scene in top_scenes:
    print(f"{scene_counts[scene]:4d}: {scene}")

# Figure out which common scenes we have
common_scenes = set(get_all_scenes(devices_to_fastest_per_scene))
for timings in devices_to_fastest_per_scene.values():
    common_scenes.intersection_update(timings.keys())

print(
    f"Matching devices have {len(common_scenes)}/{len(get_all_scenes(devices_to_fastest_per_scene))} scenes in common"
)
if not common_scenes:
    # FIXME: Handle this in a more informative manner
    sys.exit("FAILED: No common scenes")

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

print("")
print("List of devices, from fastest to slowest")
for device in top_devices:
    duration_string = to_duration_string(devices_to_total_times[device])
    print(f"{duration_string}: {device}")
