import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

TAG = "vulkan-sdk-1.4.357.0"
SOURCES = {
    "Vulkan-Headers": "e87dce08116151f6b6d7de6b6faf41498e87e6cf848ff16fa3bd5402190ad4a3",
    "Vulkan-Loader": "54f2537df22313768da0317dda2abdaaab7711b4081c48c869a79db343d0ae70",
}


def machine(path):
    with open(path, "rb") as stream:
        if stream.read(2) != b"MZ":
            raise RuntimeError(f"Invalid PE: {path}")
        stream.seek(0x3C)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        if stream.read(4) != b"PE\0\0":
            raise RuntimeError(f"Invalid PE header: {path}")
        return struct.unpack("<H", stream.read(2))[0]


def run(*args):
    subprocess.run(args, check=True)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: build_vulkan_loader.py OUTPUT_DLL LIBMPV_DLL")
    output = Path(sys.argv[1]).resolve()
    libmpv = Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory(prefix="ytcut-vulkan-") as workspace:
        root = Path(workspace)
        paths = {}
        for name, checksum in SOURCES.items():
            archive = root / f"{name}.tar.gz"
            url = f"https://github.com/KhronosGroup/{name}/archive/refs/tags/{TAG}.tar.gz"
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read()
            digest = hashlib.sha256(data).hexdigest()
            if digest != checksum:
                raise RuntimeError(f"{name}: SHA256 mismatch: {digest}")
            archive.write_bytes(data)
            with tarfile.open(archive, "r:gz") as source:
                source.extractall(root, filter="data")
            paths[name] = root / f"{name}-{TAG}"

        prefix = root / "prefix"
        hdr_build = root / "headers-build"
        run("cmake", "-S", str(paths["Vulkan-Headers"]), "-B", str(hdr_build),
            "-G", "Visual Studio 18 2026", "-A", "x64",
            f"-DCMAKE_INSTALL_PREFIX={prefix}")
        run("cmake", "--install", str(hdr_build), "--config", "Release")

        loader_build = root / "loader-build"
        run("cmake", "-S", str(paths["Vulkan-Loader"]), "-B", str(loader_build),
            "-G", "Visual Studio 18 2026", "-A", "x64",
            f"-DVULKAN_HEADERS_INSTALL_DIR={prefix}",
            "-DBUILD_TESTS=OFF", "-DUPDATE_DEPS=OFF")
        run("cmake", "--build", str(loader_build), "--config", "Release", "--parallel", "4")
        candidates = list(loader_build.rglob("vulkan-1.dll"))
        if len(candidates) != 1:
            raise RuntimeError(f"Expected one vulkan-1.dll, found {candidates}")
        if machine(candidates[0]) != machine(libmpv) or machine(libmpv) != 0x8664:
            raise RuntimeError("Vulkan loader architecture does not match x64 libmpv")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidates[0], output)
        print(f"Vulkan loader: {output} ({output.stat().st_size} bytes, x64)")


if __name__ == "__main__":
    main()
