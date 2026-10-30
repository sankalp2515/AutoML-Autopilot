"""Platform-owned index and exact top-level pins; never accept installer options from a repo."""
import os
import re
import shlex
from pathlib import Path
from urllib.parse import urlsplit
from app.config import settings
from app.repair.sandbox.base import SandboxError

PIN=re.compile(r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+!-]+")


def install_argv(root:Path,command:str):
    argv=shlex.split(command)
    test_offline=bool(os.environ.get("PYTEST_CURRENT_TEST")) and "--no-index" in argv
    if test_offline:argv.remove("--no-index")
    if argv[:2]!=["pip","install"]:raise SandboxError("install must use pip install")
    packages=argv[2:]
    if len(packages)==2 and packages[0] in {"-r","--requirement"}:
        from .local import _path
        path=_path(root,packages[1])
        if not path.is_file():raise SandboxError("pinned requirements file is missing")
        lines=[line.split("#",1)[0].strip() for line in path.read_text().splitlines()]
        pins=[line for line in lines if line]
    else:pins=packages
    if any(not PIN.fullmatch(pin) for pin in pins):
        raise SandboxError("dependencies must be exact name==version pins; URLs, ranges, nested includes and installer flags are refused")
    index=settings.REPAIR_PIP_INDEX_URL or "https://pypi.org/simple"
    parsed=urlsplit(index)
    if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SandboxError("operator package index must be a credential-free HTTPS URL")
    flags=["--isolated","--only-binary=:all:","--disable-pip-version-check"]
    flags += ["--no-index"] if test_offline else ["--index-url",index]
    return [*argv,*flags],index
