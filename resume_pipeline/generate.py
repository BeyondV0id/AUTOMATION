from __future__ import annotations

import argparse
from pathlib import Path

from .latex import VARIANTS, generate_all
from .profile import load_profile


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate reusable LaTeX resume variants")
    parser.add_argument("--profile", default="data/resume/profile.json")
    parser.add_argument("--variant", choices=["all", *VARIANTS], default="all")
    args = parser.parse_args()
    profile = load_profile(Path(args.profile))
    variants = None if args.variant == "all" else [args.variant]
    for path in generate_all(profile, variants):
        print(path)


if __name__ == "__main__":
    main()
