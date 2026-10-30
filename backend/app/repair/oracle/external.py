"""Sealed artifact tests: trusted Python never imports candidate Python or pickles.

sealed/external-probes.json opts a bundle into this protocol. A test may inspect
only explicitly exported JSON/CSV/text; legacy candidate-import tests fail closed.
"""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from app.repair import contracts as c
from app.repair.oracle import common as cm
from app.repair.sandbox.base import Upload


def sealed_tests(runner,env,contract,sealed,manifest):
    protocol=sealed/"external-probes.json"
    method=getattr(runner,"trusted_run",None)
    if not callable(method) or not protocol.is_file():
        return cm.check("sealed_tests",c.CheckStatus.INCONCLUSIVE,
            "sealed tests require external-probes.json and a clean evaluator; candidate-import tests are unsupported")
    try:
        config=json.loads(protocol.read_text())
        if config["version"]!=1 or config["kind"]!="artifact-tests":raise ValueError("unsupported protocol")
        exports=config["exports"]
        if not isinstance(exports,dict) or not 1<=len(exports)<=20:raise ValueError("invalid exports")
        ph=cm.placeholders(contract)
        needed=" ".join(exports.values())
        if "{artifacts}" in needed or "{predictions}" in needed:
            trained=cm.train(runner,env,contract)
            if trained.exit_code or trained.timed_out:raise ValueError("candidate training failed")
        if "{predictions}" in needed:
            result=runner.run(env,cm.expand(contract.commands.predict,ph),timeout_s=contract.limits.train_seconds)
            if result.exit_code or result.timed_out:raise ValueError("candidate predictions failed")
        uploads=[];total=0
        for name,source in exports.items():
            c.safe_relpath(name)
            if Path(name).suffix.lower() not in {".csv",".json",".txt"}:raise ValueError("export must be inert text data")
            path=source.format_map(ph);c.safe_relpath(path)
            if Path(path).suffix.lower() not in {".csv",".json",".txt"}:raise ValueError("candidate object artifacts are forbidden")
            raw=runner.read_file(env,path,max_bytes=2_000_001);total+=len(raw)
            if len(raw)>2_000_000 or total>10_000_000:raise ValueError("export limit exceeded")
            uploads.append(Upload("inputs/"+name,raw))
        source=sealed/manifest.tests
        for path in sorted(source.rglob("*.py")):
            if path.is_symlink() or not path.resolve().is_relative_to(sealed.resolve()):raise ValueError("invalid sealed test path")
            uploads.append(Upload("tests/"+path.relative_to(source).as_posix(),path.read_bytes()))
        if len(uploads)>200:raise ValueError("sealed test count exceeded")
        report="_ap/sealed.xml"
        result,files=method(env,["python","-I","-m","pytest","-q","-c","/dev/null",
            "-p","no:cacheprovider","tests",f"--junitxml={report}"],timeout_s=contract.limits.test_seconds,
            uploads=uploads,collect=[report],env={"AP_INPUT_DIR":"/work/inputs"})
        root=ET.fromstring(files[report]);cases=list(root.iter("testcase"))
        failed=[case.attrib.get("name","") for case in cases
                if any(case.find(kind) is not None for kind in ("failure","error","skipped"))]
        passed=bool(cases) and not failed and result.exit_code==0 and not result.timed_out
        return cm.check("sealed_tests",c.CheckStatus.PASS if passed else c.CheckStatus.FAIL,
            f"{len(cases)-len(failed)}/{len(cases)} external artifact tests pass",
            failed=failed[:20],external_container=True,candidate_source_present=False,same_dependency_image=True)
    except (ValueError,KeyError,TypeError,FileNotFoundError,ET.ParseError) as exc:
        return cm.check("sealed_tests",c.CheckStatus.FAIL,"external artifact test protocol failed",error=type(exc).__name__)
