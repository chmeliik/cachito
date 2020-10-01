#!/usr/bin/env python3
import argparse
import contextlib
import datetime
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_NAME = Path(sys.argv[0]).name


class FindBuilddepsError(Exception):
    """Failed to find build dependencies."""


def _pip_download(requirements_files, output_file, tmpdir, no_cache):
    """Run pip download, write output to file."""
    cmd = ["pip", "download", "-d", tmpdir, "--no-binary", ":all:", "--verbose"]
    if no_cache:
        cmd.append("--no-cache-dir")
    for file in requirements_files:
        cmd.append("-r")
        cmd.append(file)

    with open(output_file, "w") as outfile:
        subprocess.run(cmd, stdout=outfile, stderr=outfile, check=True)


def _filter_builddeps(pip_download_output_file):
    """Find builddeps in output of pip download."""
    # Leading whitespace => package is a build dependency
    # (because all recursive runtime dependencies were present in input files)
    builddep_re = re.compile(r"^\s+Collecting (\S+)")

    with open(pip_download_output_file) as f:
        matches = (builddep_re.match(line) for line in f)
        builddeps = set(match.group(1) for match in matches if match)

    return sorted(builddeps)


@contextlib.contextmanager
def _remove_on_success(tmpdir):
    """Remove tmpdir only if no exception is raised during context."""
    try:
        yield
    except:  # noqa: E722
        # Re-raise the exception but keep the tmpdir
        raise
    else:
        shutil.rmtree(tmpdir)


def find_builddeps(requirements_files, no_cache=False):
    """
    Find build dependencies for packages in requirements files.

    :param requirements_files: list of requirements file paths
    :param no_cache: do not use pip cache when downloading packages
    :return: list of build dependencies (in requirements.txt format)
    """
    tmpdir = tempfile.mkdtemp(prefix=f"{SCRIPT_NAME}-")

    with _remove_on_success(tmpdir):
        pip_output_file = Path(tmpdir) / "pip-download-output.txt"

        try:
            _pip_download(requirements_files, pip_output_file, tmpdir, no_cache)
        except subprocess.CalledProcessError:
            msg = f"pip download failed, see {pip_output_file} for more info"
            raise FindBuilddepsError(msg)

        return _filter_builddeps(pip_output_file)


def main():
    """Run script."""
    ap = argparse.ArgumentParser()
    ap.add_argument("requirements_files", metavar="REQUIREMENTS_FILE", nargs="+")
    ap.add_argument(
        "-o", "--output-file", metavar="FILE", help="write output to this file"
    )
    ap.add_argument(
        "-a",
        "--append",
        action="store_true",
        help="append to output file instead of overwriting",
    )
    ap.add_argument(
        "--no-cache",
        action="store_true",
        help="do not use pip cache when downloading packages",
    )

    args = ap.parse_args()
    builddeps = find_builddeps(args.requirements_files, no_cache=args.no_cache)

    # Month Day Year HH:MM:SS
    date = datetime.datetime.now().strftime("%b %d %Y %H:%M:%S")

    lines = [f"# Generated by {SCRIPT_NAME} on {date}"]
    if builddeps:
        lines.extend(builddeps)
    else:
        lines.append("# <no build dependencies found>")

    file_content = "\n".join(lines)

    if args.output_file:
        mode = "a" if args.append else "w"
        with open(args.output_file, mode) as f:
            print(file_content, file=f)
    else:
        print(file_content)


if __name__ == "__main__":
    try:
        main()
    except FindBuilddepsError as e:
        exit(f"ERROR: {e}")