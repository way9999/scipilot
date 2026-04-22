#!/usr/bin/env python3
"""
License key generator for SciPilot.

Format: SP-{tier}-{serial}-{checksum}
  - tier: S (Student) or P (Pro)
  - serial: 5-digit number (00001-99999)
  - checksum: 4 hex chars from djb2(payload + secret) % 65536

Usage:
  python generate_keys.py --tier S --count 10 --start 1
  python generate_keys.py --tier P --count 5 --start 100
"""

import argparse

SECRET = "SciPilot2024LicenseKey"


def djb2(input_str: str) -> int:
    """DJB2 hash function."""
    hash_val = 5381
    for char in input_str:
        hash_val = ((hash_val * 33) + ord(char)) & 0xFFFFFFFFFFFFFFFF
    return hash_val


def generate_key(tier: str, serial: int) -> str:
    """Generate a license key."""
    tier_char = tier.upper()
    if tier_char not in ("S", "P"):
        raise ValueError("tier must be S or P")

    serial_str = f"{serial:05d}"
    payload = f"SP{tier_char}{serial_str}"

    hash_val = djb2(f"{payload}{SECRET}") % 65536
    checksum = f"{hash_val:04X}"

    return f"{payload}-{checksum}"


def validate_key(key: str) -> tuple[bool, str]:
    """Validate a license key, return (valid, tier)."""
    clean = key.upper().replace("-", "").replace(" ", "")

    if len(clean) != 12 or not clean.startswith("SP"):
        return False, "Invalid format"

    tier_char = clean[2]
    if tier_char not in ("S", "P"):
        return False, "Invalid tier"

    tier = "Student" if tier_char == "S" else "Pro"
    payload = clean[:8]
    checksum = clean[8:12]

    expected_hash = djb2(f"{payload}{SECRET}") % 65536
    expected_checksum = f"{expected_hash:04X}"

    if checksum != expected_checksum:
        return False, "Invalid checksum"

    return True, tier


def main():
    parser = argparse.ArgumentParser(description="Generate SciPilot license keys")
    parser.add_argument("--tier", choices=["S", "P"], required=True, help="License tier (S=Student, P=Pro)")
    parser.add_argument("--count", type=int, default=1, help="Number of keys to generate")
    parser.add_argument("--start", type=int, default=1, help="Starting serial number")
    parser.add_argument("--validate", type=str, help="Validate a single key instead of generating")
    args = parser.parse_args()

    if args.validate:
        valid, tier = validate_key(args.validate)
        if valid:
            print(f"✅ Valid: {args.validate} → {tier}")
        else:
            print(f"❌ Invalid: {args.validate} ({tier})")
        return

    print(f"Generating {args.count} {('Student' if args.tier == 'S' else 'Pro')} keys starting from {args.start}")
    print("-" * 40)

    for i in range(args.start, args.start + args.count):
        key = generate_key(args.tier, i)
        print(key)

    print("-" * 40)
    print("Copy these keys and distribute to users.")


if __name__ == "__main__":
    main()