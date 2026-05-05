#!/usr/bin/env python3
"""
Validation script to check if all required dependencies are properly configured.
This script can be run locally to verify the Lambda dependencies before deployment.
"""

import sys
from typing import List, Tuple


def check_requirements_file() -> Tuple[bool, List[str]]:
    """Check if requirements.txt exists and contains all required dependencies."""
    required_deps = {
        "strands-agents": ">=0.1.0",
        "hypothesis": ">=6.92.0",
    }

    # These are provided by Lambda runtime or layers, should NOT be in requirements.txt
    excluded_deps = ["boto3", "aws-lambda-powertools"]

    errors = []

    try:
        with open("requirements.txt", "r") as f:
            content = f.read()

        for dep, version in required_deps.items():
            if dep not in content:
                errors.append(f"Missing dependency: {dep}{version}")
            elif version not in content:
                errors.append(f"Incorrect version for {dep}: expected {version}")

        # Check that excluded dependencies are not present
        for dep in excluded_deps:
            # Check if the dependency is listed (not just in comments)
            lines = [
                line.strip()
                for line in content.split("\n")
                if line.strip() and not line.strip().startswith("#")
            ]
            if any(dep in line for line in lines):
                errors.append(
                    f"Dependency {dep} should not be in requirements.txt (provided by Lambda runtime/layer)"
                )

    except FileNotFoundError:
        errors.append("requirements.txt file not found")
        return False, errors

    return len(errors) == 0, errors


def main():
    """Main validation function."""
    print("Validating Lambda dependencies...")
    print("-" * 50)

    success, errors = check_requirements_file()

    if success:
        print("✓ All required dependencies are properly configured")
        print("\nConfigured dependencies:")
        print("  - strands-agents >= 0.1.0 (AI analysis engine)")
        print("  - hypothesis >= 6.92.0 (Property-based testing)")
        print("\nProvided by Lambda runtime/layers:")
        print("  - boto3 (AWS SDK - included in Lambda runtime)")
        print("  - aws-lambda-powertools (Lambda utilities - installed as layer)")
        return 0
    else:
        print("✗ Dependency validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
