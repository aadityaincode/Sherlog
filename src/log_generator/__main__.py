"""CLI: python -m src.log_generator [--normal 400 --broken 8 --declined 20 --seed 42]"""

import argparse

from .generator import generate_dataset, write_dataset


def main():
    parser = argparse.ArgumentParser(
        prog="python -m src.log_generator",
        description="Generate the NovaStream synthetic log dataset (Scenario C).",
    )
    parser.add_argument("--normal", type=int, default=400, help="normal renewals (task spec: 300-500)")
    parser.add_argument("--broken", type=int, default=8, help="silently broken renewals (task spec: 5-10)")
    parser.add_argument("--declined", type=int, default=20, help="card-declined noise transactions")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducible datasets")
    parser.add_argument("--day", default="2026-07-09", help="date the logs span (09:00-15:00)")
    parser.add_argument("--out", default="data/generated", help="output directory")
    args = parser.parse_args()

    transactions, monitoring, answer_key, windows = generate_dataset(
        normal=args.normal,
        broken=args.broken,
        declined=args.declined,
        seed=args.seed,
        day=args.day,
    )
    summary = write_dataset(args.out, transactions, monitoring, answer_key)

    print(f"Wrote dataset to {args.out}/")
    for name, count in summary.items():
        print(f"  {name}: {count}")
    print("  incident windows:")
    for start, end in windows:
        print(f"    {start:%H:%M:%S} - {end:%H:%M:%S}")


if __name__ == "__main__":
    main()
