"""Build the API's sample catalog WITHOUT sealed tests, labels or reference patches."""
import argparse
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]


def prepare(out):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    count=0
    for source in sorted((ROOT/"fixtures/repos").iterdir()):
        if not (source/"repo/autopilot.yaml").is_file():continue
        destination=out/source.name
        if destination.is_symlink() or destination.resolve().parent != out:
            raise ValueError("source destination escapes output root")
        if destination.exists():
            if not (destination/"sample.json").is_file():raise ValueError("refusing to replace an unrecognized source directory")
            shutil.rmtree(destination)
        destination.mkdir()
        shutil.copytree(source/"repo",destination/"repo",ignore=shutil.ignore_patterns(".git","__pycache__",".pytest_cache"))
        if (source/"TASK.txt").is_file():shutil.copyfile(source/"TASK.txt",destination/"TASK.txt")
        (destination/"sample.json").write_text(json.dumps({"name":source.name,"sealed_bundle_on_verifier":True}))
        count+=1
    return count


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--out",default=str(ROOT/"fixtures/sources"))
    print(json.dumps({"samples":prepare(parser.parse_args().out),"sealed_material_included":False}))
