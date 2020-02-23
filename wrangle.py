#!/usr/bin/env python3

import sys
import json
import pprint
import zipfile
import traceback
import statistics

from typing import Dict, NamedTuple, List, Iterable

DEVICE1 = "3500U"
DEVICE2 = "Radeon Vega 8 Mobile"


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
    device_type: str
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


def to_device_and_environment(
    samples: Iterable[Sample],
) -> Dict[Device, Dict[Environment, List[float]]]:

    by_device_and_environment: Dict[Device, Dict[Environment, List[float]]] = {}

    for sample in samples:
        device = Device(
            device_name=sample.device_name,
            device_type=sample.device_type,
            device_threads=sample.device_threads,
        )
        environment = Environment(
            blender_version=sample.blender_version,
            os_name=sample.os_name,
            scene_name=sample.scene_name,
        )

        if device not in by_device_and_environment:
            by_device_and_environment[device] = {}

        if environment not in by_device_and_environment[device]:
            by_device_and_environment[device][environment] = []

        by_device_and_environment[device][environment].append(
            sample.render_time_seconds
        )

    return by_device_and_environment


def get_device_by_name(devices: Iterable[Device], name: str) -> Device:
    hits: List[Device] = []
    for device in devices:
        if name in device.device_name:
            hits.append(device)

    if len(hits) == 1:
        return hits[0]

    if not hits:
        raise LookupError(f"Not found: {name}")

    hits_str = "\n  ".join(str(x) for x in hits)
    raise LookupError(f"Multiple hits for {name}: {hits_str}")


samples: List[Sample] = []
with zipfile.ZipFile("opendata-2020-02-21-063254+0000.zip") as opendata:
    for entry in opendata.infolist():
        if not entry.filename.endswith(".jsonl"):
            continue
        with opendata.open(entry) as jsonl:
            samples += process_opendata(jsonl.readlines())

by_device_and_environment = to_device_and_environment(samples)


# Find common environments between the two devices to compare
d1 = get_device_by_name(by_device_and_environment.keys(), DEVICE1)
environments1 = by_device_and_environment[d1].keys()

d2 = get_device_by_name(by_device_and_environment.keys(), DEVICE2)
environments2 = by_device_and_environment[d2].keys()

common_environments: List[Environment] = []
for e1 in environments1:
    if e1 not in environments2:
        continue
    common_environments.append(e1)

if not common_environments:
    sys.exit(f"No common environments between:\n* {d1}\n* {d2}")

print(f"Common environments between:\n  A: {d1}\n  B: {d2}")
for environment in common_environments:
    print(f"{str(environment)}")
    dt1 = statistics.median(by_device_and_environment[d1][environment])
    dt2 = statistics.median(by_device_and_environment[d2][environment])

    print(f"  A: {dt1:.0f}s")
    print(f"  B: {dt2:.0f}s")

    factor = dt1 / dt2
    description = f"A is faster than B by a factor of {factor:.1f}"
    if factor < 1:
        description = f"B is faster than A by a factor of {1.0/factor:.1f}"
    print(f"  {description}")
