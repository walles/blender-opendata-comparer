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
        compute_devices = data_point["device_info"]["compute_devices"]
        if len(compute_devices) != 1:
            # Multiple compute devices, never mind
            return

        compute_device = compute_devices[0]
        if type(compute_device) is dict:
            compute_device = compute_device["name"]
        print(compute_device)


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
