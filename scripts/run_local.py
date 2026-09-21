#!/usr/bin/env python3
"""Local development runner for gh-ops."""
import sys
import os

# Add src to path for local development
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main() -> None:
    """Run a gh-ops operation locally."""
    if len(sys.argv) < 2:
        print("Usage: python run_local.py <operation> [args...]")
        print("Operations: daily, oss-hunt, monitor, weekly")
        sys.exit(1)

    operation = sys.argv[1]
    print(f"Running operation: {operation}")
    # TODO: Import and call dispatcher
    print("Not yet implemented. Build Phase 1 first.")


if __name__ == "__main__":
    main()
