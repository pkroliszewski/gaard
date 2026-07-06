#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Build and optionally publish GAARD Python packages to PyPI.

Usage:
  scripts/build.sh [--upload] [--repository NAME] [--python PYTHON] [--package NAME]

Options:
  --upload           Upload checked distributions with Twine.
  --repository NAME  Twine repository name (default: pypi).
  --python PYTHON    Python executable to use (default: python3).
  --package NAME     Build only one package. Can be used multiple times.
                     Names: gaard-plugin-api, gaard-core, gaard-connectors,
                     gaard-llm, gaard-api, gaard-client. Defaults to all packages.
  -h, --help         Show this help.

The selected Python environment must contain the `build` and `twine` packages.
Install development requirements first with:
  python -m pip install -r requirements-dev.txt

Credentials are read by Twine, for example from TWINE_USERNAME and
TWINE_PASSWORD or from ~/.pypirc.

Examples:
  scripts/build.sh
  scripts/build.sh --package gaard-core
  scripts/build.sh --upload --repository testpypi
  scripts/build.sh --upload
USAGE
}

upload=false
repository="pypi"
python_bin="${PYTHON:-python3}"
selected_packages=()

package_path_for() {
  case "$1" in
    gaard-plugin-api|packages/gaard-plugin-api)
      echo "packages/gaard-plugin-api"
      ;;
    gaard-core|packages/gaard-core)
      echo "packages/gaard-core"
      ;;
    gaard-connectors|packages/gaard-connectors)
      echo "packages/gaard-connectors"
      ;;
    gaard-llm|packages/gaard-llm)
      echo "packages/gaard-llm"
      ;;
    gaard-api|packages/gaard-api)
      echo "packages/gaard-api"
      ;;
    gaard-client|packages/gaard-client)
      echo "packages/gaard-client"
      ;;
    *)
      return 1
      ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --upload)
      upload=true
      shift
      ;;
    --repository)
      repository="${2:?missing repository name}"
      shift 2
      ;;
    --python)
      python_bin="${2:?missing Python executable}"
      shift 2
      ;;
    --package)
      package_name="${2:?missing package name}"
      if ! package_path="$(package_path_for "$package_name")"; then
        echo "Unknown package: $package_name" >&2
        usage >&2
        exit 2
      fi
      selected_packages+=("$package_path")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"
dist_dir="$project_root/dist"
packages=(
  "packages/gaard-plugin-api"
  "packages/gaard-core"
  "packages/gaard-connectors"
  "packages/gaard-llm"
  "packages/gaard-api"
  "packages/gaard-client"
)

if [ "${#selected_packages[@]}" -gt 0 ]; then
  packages=("${selected_packages[@]}")
fi

if [[ "$python_bin" == */* ]]; then
  python_dir="$(cd "$(dirname "$python_bin")" && pwd)"
  python_bin="$python_dir/$(basename "$python_bin")"
fi

cd "$project_root"

sync_package_resources() {
  case "$1" in
    packages/gaard-api)
      install -d "packages/gaard-api/src/gaard_api/admin-web/assets"
      install -m 0644 \
        "resources/getgaard.svg" \
        "packages/gaard-api/src/gaard_api/admin-web/assets/getgaard.svg"
      ;;
  esac
}

if ! "$python_bin" -c 'import build, twine' >/dev/null 2>&1; then
  echo "Missing build tools for $python_bin." >&2
  echo "Install them with: $python_bin -m pip install --upgrade build twine" >&2
  exit 1
fi

rm -rf "$dist_dir"
mkdir -p "$dist_dir"

for package in "${packages[@]}"; do
  sync_package_resources "$package"
  echo "Building $package"
  "$python_bin" -m build "$package" --outdir "$dist_dir"
done

echo "Checking distributions"
"$python_bin" -m twine check "$dist_dir"/*

if [ "$upload" = false ]; then
  echo "Packages are ready in $dist_dir"
  echo "Add --upload to publish them."
  exit 0
fi

echo "Uploading packages to $repository"
"$python_bin" -m twine upload --repository "$repository" "$dist_dir"/*
