# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Lambda function to package agent code for ZIP deployment.

Downloads ARM64 wheels, extracts them, bundles with agent code,
and uploads to S3. Triggered as a CloudFormation Custom Resource.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def send_response(
    event: dict,
    context,
    status: str,
    reason: str = "",
    physical_resource_id: str = None,
) -> None:
    """
    Send response to CloudFormation.

    Args:
        event: CloudFormation event.
        context: Lambda context.
        status: SUCCESS or FAILED.
        reason: Reason for failure.
        physical_resource_id: Physical resource ID.
    """
    response_body = json.dumps(
        {
            "Status": status,
            "Reason": reason or f"See CloudWatch Log Stream: {context.log_stream_name}",
            "PhysicalResourceId": physical_resource_id or context.log_stream_name,
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
        }
    ).encode("utf-8")

    response_url = event["ResponseURL"]
    if not response_url.lower().startswith("https://"):
        raise ValueError(f"Only HTTPS URLs are allowed, got: {response_url}")

    req = urllib.request.Request(
        response_url,
        data=response_body,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req)  # noqa: S310  # nosec B310


def download_wheels(requirements: list[str], download_dir: Path) -> None:
    """
    Download ARM64 Linux wheels for the given requirements.

    Args:
        requirements: List of package specifiers.
        download_dir: Directory to download wheels to.
    """
    logger.info(f"Downloading wheels for: {requirements}")

    # Write requirements to temp file
    req_file = download_dir / "requirements.txt"
    req_file.write_text("\n".join(requirements))

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "-r",
            str(req_file),
            "--platform",
            "manylinux2014_aarch64",
            "--python-version",
            "312",
            "--only-binary=:all:",
            "-d",
            str(download_dir),
            "--quiet",
        ],
        check=True,
    )

    # Also download OpenTelemetry
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "aws-opentelemetry-distro",
            "--platform",
            "manylinux2014_aarch64",
            "--python-version",
            "312",
            "--only-binary=:all:",
            "-d",
            str(download_dir),
            "--quiet",
        ],
        check=True,
    )


def extract_wheels(download_dir: Path, package_dir: Path) -> None:
    """
    Extract all wheel files to the package directory.

    Args:
        download_dir: Directory containing wheel files.
        package_dir: Directory to extract to.
    """
    for wheel in download_dir.glob("*.whl"):
        logger.info(f"Extracting: {wheel.name}")
        with zipfile.ZipFile(wheel, "r") as whl:
            whl.extractall(package_dir)


def create_otel_wrapper(package_dir: Path) -> None:
    """
    Create the opentelemetry-instrument wrapper script.

    Args:
        package_dir: Root package directory.
    """
    bin_dir = package_dir / "bin"
    bin_dir.mkdir(exist_ok=True)

    script = bin_dir / "opentelemetry-instrument"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "# Lightweight wrapper: skips slow OTel auto-discovery.\n"
        "# BedrockAgentCoreApp handles its own instrumentation internally.\n"
        "import sys, runpy\n"
        "if len(sys.argv) < 2:\n"
        "    print('Usage: opentelemetry-instrument <script>', file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "sys.argv = sys.argv[1:]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )


def create_deployment_zip(package_dir: Path, output_path: Path) -> None:
    """
    Create the deployment ZIP file with proper permissions.

    Args:
        package_dir: Directory to zip.
        output_path: Output ZIP file path.
    """
    logger.info(f"Creating deployment ZIP: {output_path}")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(package_dir):
            # Add directories
            for dir_name in dirs:
                dir_path = Path(root) / dir_name
                arcname = str(dir_path.relative_to(package_dir)) + "/"
                info = zipfile.ZipInfo(arcname)
                info.external_attr = 0o755 << 16
                zipf.writestr(info, "")

            # Add files
            for file_name in files:
                file_path = Path(root) / file_name
                arcname = str(file_path.relative_to(package_dir))
                info = zipfile.ZipInfo(arcname)
                # Executables in bin/ get 755, others get 644
                if arcname.startswith("bin/"):
                    info.external_attr = 0o755 << 16
                else:
                    info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zipf.writestr(info, file_path.read_bytes())


def handler(event: dict, context) -> None:
    """
    Lambda handler for Custom Resource.

    Args:
        event: CloudFormation Custom Resource event.
        context: Lambda context.
    """
    logger.info(f"Event: {json.dumps(event)}")

    request_type = event["RequestType"]
    props = event["ResourceProperties"]

    # On Delete, just succeed since there's nothing to clean up. The bucket handles its own cleanup.
    if request_type == "Delete":
        send_response(event, context, "SUCCESS")
        return

    try:
        bucket_name = props["BucketName"]
        object_key = props["ObjectKey"]
        requirements = props["Requirements"]
        asset_bucket = props["AssetBucket"]
        asset_key = props["AssetKey"]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            download_dir = tmp_path / "wheels"
            package_dir = tmp_path / "package"
            source_zip = tmp_path / "source.zip"
            download_dir.mkdir()
            package_dir.mkdir()

            # Download and extract wheels
            download_wheels(requirements, download_dir)
            extract_wheels(download_dir, package_dir)

            # Create OpenTelemetry wrapper
            create_otel_wrapper(package_dir)

            # Download agent source asset from S3 and extract into package dir
            logger.info(
                f"Downloading source asset from s3://{asset_bucket}/{asset_key}"
            )
            s3.download_file(asset_bucket, asset_key, str(source_zip))
            with zipfile.ZipFile(source_zip, "r") as zf:
                zf.extractall(package_dir)

            # Create ZIP
            zip_path = tmp_path / "deployment_package.zip"
            create_deployment_zip(package_dir, zip_path)

            # Upload to S3
            logger.info(f"Uploading to s3://{bucket_name}/{object_key}")
            s3.upload_file(str(zip_path), bucket_name, object_key)

        send_response(
            event,
            context,
            "SUCCESS",
            physical_resource_id=f"{bucket_name}/{object_key}",
        )

    except Exception as e:
        logger.exception("Failed to package agent")
        send_response(event, context, "FAILED", reason=str(e))
