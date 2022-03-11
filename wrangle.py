#!/usr/bin/env python3

import os
import sys
import json
import pprint
import zipfile
import traceback
import urllib.request as request

from typing import Dict, NamedTuple, List, Iterable, Set, cast

# Make a top list out of these
DEVICE_NAMES: List[str] = [
    "4850HQ",  # 15" Macbook Pro, late 2013
    "RTX ",  # Fastest GPUs: https://opendata.blender.org/#fastest-total-median-render-time-gpus-chart
    "3500U",  # Lenovo Thinkpad E495
    "11800H",  # Intel CPU, comes with some laptops
    "Apple M1",
]
MIN_COMMON_SCENES_COUNT = 5


LOCAL_DATABASE_FILENAME = "/tmp/opendata-latest.zip"


class Sample(NamedTuple):
    device_name: str
    device_type: str
    device_threads: int

    blender_version: str
    os_name: str
    scene_name: str

    render_time_seconds: float


class Device:
    name: str
    threads: int

    def __init__(self, name: str, threads: int) -> None:
        self.name = name
        self.threads = threads

    def __eq__(self, o: object) -> bool:
        them = cast(Device, o)
        if self.name.lower() != them.name.lower():
            return False
        if self.threads != them.threads:
            return False
        return True

    def __hash__(self) -> int:
        return hash((self.name.lower(), self.threads))

    def __str__(self) -> str:
        if self.threads:
            return f"{self.name} ({self.threads} threads)"
        return self.name


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

        # Apple M1s tend to be listed with four identical entries, handle that
        # case.
        device_names = set()
        for device in compute_devices:
            device_names.add(device["name"])
        if len(device_names) != 1:
            # Multiple different kinds of compute devices or none (?), never
            # mind
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
    line_count = 0
    for line in jsonl:
        line_count += 1
        entry = json.loads(line)
        try:
            if entry["schema_version"] == "v1":
                samples += process_entry_v1(entry)
            elif entry["schema_version"] == "v2":
                samples += process_entry_v2(entry)
            elif entry["schema_version"] == "v3":
                samples += process_entry_v3(entry)
            elif entry["schema_version"] == "v4":
                # Don't know what the difference is between v3 and v4, just use
                # the v3 parser for both for now until we figure out why we need
                # a specific one for v4.
                samples += process_entry_v3(entry)
            else:
                pprint.pprint(entry, stream=sys.stderr)
                sys.exit("Unsupported schema version")
        except Exception:
            pprint.pprint(entry, stream=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            sys.exit(1)

    print(
        f"Found {len(samples)} data points in {line_count} lines, at {len(samples)/line_count:.1f} data points per line"
    )
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


def seconds_to_string(seconds: float) -> str:
    seconds_count = int(seconds) % 60
    minutes_count = (int(seconds) // 60) % 60
    hours_count = int(seconds) // 3600
    if hours_count > 0:
        return f"{hours_count:2d}h{minutes_count:02d}m"
    return f"{minutes_count:2d}m{seconds_count:02d}s"


def to_duration_description(seconds: float, threads: int) -> str:
    result = seconds_to_string(seconds)
    if threads > 0:
        single_core_duration = seconds_to_string(seconds * threads)
        result += f" (single threaded: {single_core_duration})"
    else:
        result += f"                          "

    return result


def get_zipfile_name() -> str:
    if os.path.exists(LOCAL_DATABASE_FILENAME):
        print(f"Database found in {LOCAL_DATABASE_FILENAME}")
        return LOCAL_DATABASE_FILENAME

    print(f'Downloading performance database into "{LOCAL_DATABASE_FILENAME}"...')
    request.urlretrieve(
        "https://opendata.blender.org/snapshots/opendata-latest.zip",
        LOCAL_DATABASE_FILENAME,
    )
    return LOCAL_DATABASE_FILENAME


# List samples for all devices we're interested in
samples: List[Sample] = []
with zipfile.ZipFile(get_zipfile_name()) as opendata:
    for entry in opendata.infolist():
        if not entry.filename.endswith(".jsonl"):
            continue
        db_size_mb = entry.file_size // (1024 * 1024)
        print(f"Parsing {db_size_mb}MB database...")
        with opendata.open(entry) as jsonl:
            entry_samples = process_opendata(jsonl.readlines())

            lowercase_device_names = []
            for device_name in DEVICE_NAMES:
                lowercase_device_names.append(device_name.lower())

            for sample in entry_samples:
                # Filter out devices we're interested in
                match = False
                for device_name in lowercase_device_names:
                    if device_name in sample.device_name.lower():
                        match = True
                if not match:
                    continue

                samples.append(sample)

print(f"Found {len(samples)} samples for the requested devices")

# Map devices to the fastest recorded rendering per scene
devices_to_fastest_per_scene: Dict[Device, Dict[str, float]] = {}
for sample in samples:
    device_threads = sample.device_threads
    if sample.device_type != "CPU":
        # The threads is for CPUs only, coalesce GPU devices with different thread counts
        device_threads = 0
    device = Device(name=sample.device_name, threads=device_threads)

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

# For all devices, compute the geometric mean of all common-scene numbers
devices_to_total_times: Dict[Device, float] = {}
for device, timings in devices_to_fastest_per_scene.items():
    product = 1.0
    for scene in common_scenes:
        product *= devices_to_fastest_per_scene[device][scene]
    devices_to_total_times[device] = product ** (1.0 / len(common_scenes))

# Rank devices per sum-of-common-scenes numbers
top_devices: List[Device] = sorted(
    list(devices_to_total_times.keys()), key=devices_to_total_times.get
)

print("")
print("List of devices, from fastest to slowest")
for device in top_devices:
    threads = device.threads
    duration_string = to_duration_description(devices_to_total_times[device], threads)
    print(f"{duration_string}: {device}")
