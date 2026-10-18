#!/usr/bin/env python3
"""Check that the scanner and its runtime AndroidX classes are defined in an APK."""

from __future__ import annotations

import argparse
import struct
import sys
import zipfile
from pathlib import Path


REQUIRED_CLASSES = {
    "com/agentbeacon/android/BeaconCaptureActivity",
    "com/journeyapps/barcodescanner/CaptureActivity",
    "com/journeyapps/barcodescanner/CaptureManager",
    "com/journeyapps/barcodescanner/ScanContract",
    "com/google/zxing/BarcodeFormat",
    "androidx/core/app/ActivityCompat",
    "androidx/core/content/ContextCompat",
    "androidx/activity/result/contract/ActivityResultContract",
    "androidx/fragment/app/Fragment",
    "androidx/fragment/app/FragmentActivity",
}


def read_u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("DEX table points outside the file")
    return struct.unpack_from("<I", data, offset)[0]


def read_uleb128(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if offset >= len(data) or shift >= 35:
            raise ValueError("Invalid DEX ULEB128 value")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, offset
        shift += 7


def read_dex_classes(data: bytes) -> set[str]:
    if len(data) < 112 or not data.startswith(b"dex\n"):
        raise ValueError("APK contains an invalid DEX file")

    string_count = read_u32(data, 56)
    string_offset = read_u32(data, 60)
    type_count = read_u32(data, 64)
    type_offset = read_u32(data, 68)
    class_count = read_u32(data, 96)
    class_offset = read_u32(data, 100)
    if any(count > len(data) for count in (string_count, type_count, class_count)):
        raise ValueError("DEX table count is invalid")

    strings: list[str] = []
    for index in range(string_count):
        item_offset = read_u32(data, string_offset + index * 4)
        _, cursor = read_uleb128(data, item_offset)  # UTF-16 length; descriptors are ASCII.
        end = data.find(b"\0", cursor)
        if end < 0:
            raise ValueError("Unterminated DEX string")
        strings.append(data[cursor:end].decode("utf-8", errors="replace"))

    types: list[str] = []
    for index in range(type_count):
        descriptor_index = read_u32(data, type_offset + index * 4)
        if descriptor_index >= len(strings):
            raise ValueError("DEX type references an invalid string")
        types.append(strings[descriptor_index])

    classes: set[str] = set()
    for index in range(class_count):
        class_index = read_u32(data, class_offset + index * 32)
        if class_index >= len(types):
            raise ValueError("DEX class references an invalid type")
        descriptor = types[class_index]
        if descriptor.startswith("L") and descriptor.endswith(";"):
            classes.add(descriptor[1:-1])
    return classes


def read_apk_classes(apk_path: Path) -> set[str]:
    classes: set[str] = set()
    with zipfile.ZipFile(apk_path) as apk:
        dex_names = sorted(
            (name for name in apk.namelist() if name == "classes.dex" or name.startswith("classes") and name.endswith(".dex")),
            key=lambda name: (name != "classes.dex", name),
        )
        if not dex_names:
            raise ValueError("APK contains no DEX files")
        for dex_name in dex_names:
            classes.update(read_dex_classes(apk.read(dex_name)))
    return classes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path, help="APK to inspect")
    args = parser.parse_args()

    try:
        packaged_classes = read_apk_classes(args.apk)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"APK verification failed: {error}", file=sys.stderr)
        return 2

    missing = sorted(REQUIRED_CLASSES - packaged_classes)
    if missing:
        print("APK is missing required runtime classes:", file=sys.stderr)
        for class_name in missing:
            print(f"  {class_name}", file=sys.stderr)
        return 1

    print(f"APK runtime classes verified: {len(REQUIRED_CLASSES)} required classes found in {args.apk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
