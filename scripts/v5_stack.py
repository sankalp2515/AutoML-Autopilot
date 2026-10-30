"""One-command local v5 startup and quiesced, checksummed backup/empty-target restore.

No user .env is read or changed. Internal credentials persist outside the checkout.
Model calls are disabled unless --allow-live is explicitly supplied with shell keys.
Never use `down -v`: restore refuses to overwrite an existing database or volume.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tarfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from prepare_v5_sources import prepare

VOLUMES=("artifacts","packs","verifier-state")
APP_SERVICES=("api","worker","repair-worker","verifier")
DATABASES=(("postgres","autopilot"),("verifier-postgres","verifier"))


def sha(path):
    with Path(path).open("rb") as stream:return hashlib.file_digest(stream,"sha256").hexdigest()


def command(argv,*,env=None,input=None,stdout=None):
    result=subprocess.run(argv,cwd=ROOT,env=env,input=input,stdout=stdout or subprocess.PIPE,stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f"{argv[0]} operation failed: "+result.stderr.decode(errors="replace")[-1500:])
    return result.stdout


class Stack:
    def __init__(self,args):
        self.args=args;self.project=args.project
        self.state=Path(args.state_dir or Path.home()/".local/share/automl-v5"/self.project).resolve()
        self.state.mkdir(parents=True,exist_ok=True);self.state.chmod(0o700)
        path=self.state/"operator.json"
        if path.exists():self.config=json.loads(path.read_text())
        else:
            if args.command not in {"up","restore"}:raise ValueError("no operator state for this project")
            names=("POSTGRES_PASSWORD","REPAIR_LEDGER_PASSWORD","POSTGRES_ADMIN_PASSWORD","REPAIR_LEDGER_ADMIN_PASSWORD","SECRET_KEY","AUTOPILOT_VERIFIER_TOKEN",
                   "REPAIR_SANDBOX_TOKEN","REPAIR_VERIFIER_TOKEN","REPAIR_ADMISSION_TOKEN",
                   "REPAIR_TRUSTED_SANDBOX_TOKEN","REPAIR_SESSION_SECRET")
            self.config={name:secrets.token_hex(32) for name in names}
            self.config["project"]=self.project
            path.write_text(json.dumps(self.config));path.chmod(0o600)
        if self.config["project"]!=self.project:raise ValueError("state belongs to another project")
        # Judge access is a persistent project policy, not a flag a later restart can forget.
        if args.judge_only and not self.config.get("judge_only"):
            self.config["judge_only"]=True
            path.write_text(json.dumps(self.config));path.chmod(0o600)
        judge_only=bool(self.config.get("judge_only"))
        # New operator-only admin roles are additive; existing data is not rewritten.
        if "POSTGRES_ADMIN_PASSWORD" not in self.config or "REPAIR_LEDGER_ADMIN_PASSWORD" not in self.config:
            self.config.setdefault("POSTGRES_ADMIN_PASSWORD",secrets.token_hex(32))
            self.config.setdefault("REPAIR_LEDGER_ADMIN_PASSWORD",secrets.token_hex(32))
            path.write_text(json.dumps(self.config));path.chmod(0o600)
        empty=self.state/"empty-compose.env";empty.touch()
        self.env={**os.environ,**{k:v for k,v in self.config.items() if k not in {"project","judge_only"}}}
        if judge_only:
            self.env["REPAIR_JUDGE_ONLY"]="true"
            for key,value in {"REPAIR_JUDGE_ONLY":"true","REPAIR_TOTAL_USD_CAP":"20",
                "REPAIR_DAILY_USD_CAP":"5","REPAIR_TASK_USD_CEILING":"0.60",
                "REPAIR_DAILY_TASKS_PER_TENANT":"12","REPAIR_CREATE_COOLDOWN_S":"30",
                "REPAIR_MAX_ACTIVE_TASKS":"2"}.items():self.env.setdefault(key,value)
            if float(self.env["REPAIR_TOTAL_USD_CAP"])<=0 or int(self.env["REPAIR_MAX_ACTIVE_TASKS"])<=0:
                raise ValueError("judge mode requires positive lifetime allowance and active-task limits")
            for key in ("NVIDIA_API_KEY","NVIDIA_API_KEY_2","NVIDIA_API_KEY_3","NVIDIA_API_KEY_4"):
                self.env[key]=""  # Do not silently substitute NVIDIA evidence for Nebius judging calls.
            if args.allow_live and (not self.env.get("NEBIUS_API_KEY") or not self.env.get("REPAIR_INVITE_CODES")):
                raise ValueError("live judge mode requires a Token Factory key and private judge invite codes in the process environment")
        if not args.allow_live:
            for name in ("NEBIUS_API_KEY","NVIDIA_API_KEY","NVIDIA_API_KEY_2","NVIDIA_API_KEY_3","NVIDIA_API_KEY_4","TAVILY_API_KEY"):
                self.env[name]=""
        base=4*(int(hashlib.sha256(self.project.encode()).hexdigest()[:4],16)%50)+8
        self.env.update(WEB_PORT=str(args.port),PUBLIC_ORIGIN=f"http://localhost:{args.port}",
            BACKEND_IMAGE=f"{self.project}-backend:local",WEB_IMAGE=f"{self.project}-web:local",
            REPAIR_LIVE_ENABLED="true" if args.allow_live else "false",
            REPAIR_SOURCE_FIXTURES_DIR=str(self.state/"fixture-sources"))
        for i,name in enumerate(("BACKPLANE_SUBNET","EGRESS_SUBNET","EDGE_SUBNET","VERIFICATION_SUBNET")):
            self.env[name]=f"10.203.{base+i}.0/24"
        volume=self.project+"_runner-environments"
        command(["docker","volume","create",volume,"--label","autopilot.project="+self.project])
        location=json.loads(command(["docker","volume","inspect",volume]))[0]["Mountpoint"]
        self.env["REPAIR_ENV_HOST_DIR"]=location
        override=self.state/"runner-volume.json"
        services={"sandbox-runner":{"volumes":[{"type":"volume","source":"runner-environments","target":location}]}}
        if judge_only:
            services["worker"]={"environment":{name:"" for name in ("NEBIUS_API_KEY","NVIDIA_API_KEY","TAVILY_API_KEY")}}
        override.write_text(json.dumps({"services":services,"volumes":{"runner-environments":{"external":True,"name":volume}}}))
        self.prefix=["docker","compose","--env-file",str(empty),"-p",self.project,"-f",str(ROOT/"docker-compose.yml"),"-f",str(override)]

    def compose(self,*args,**kw):return command([*self.prefix,*args],env=self.env,**kw)
    def operator(self,action,oneshot=False):
        command=("run","--rm","--no-deps","-T","api") if oneshot else ("exec","-T","api")
        return json.loads(self.compose(*command,"python","-m","app.repair.operations",action))

    def ledger(self,action="inspect-ledger"):
        return json.loads(self.compose("exec","-T","verifier","python","-m","app.repair.operations",action))

    def up(self,build=True):
        prepare(self.env["REPAIR_SOURCE_FIXTURES_DIR"])
        self.compose("config","--quiet")
        if build:self.compose("build","api","web") # Shared backend image is built once, not raced by five services.
        self.compose("up","-d","--wait","--wait-timeout","90","postgres","verifier-postgres")
        self.compose("run","--rm","--no-deps","api","python","-m","app.repair.operations","migrate")
        self.compose("up","-d","--no-build","--wait","--wait-timeout","240")
        return self.doctor()

    def doctor(self):
        output=self.compose("ps","--format","json")
        try:rows=json.loads(output)
        except json.JSONDecodeError:rows=[json.loads(line) for line in output.splitlines() if line.strip()]
        if isinstance(rows,dict):rows=[rows]
        states={r["Service"]:{"state":r["State"],"health":r.get("Health")} for r in rows}
        import urllib.request
        with urllib.request.urlopen(f"http://localhost:{self.args.port}/api/v1/v5/ready",timeout=10) as response:
            readiness=json.load(response)
        security={"queue_db_superuser":self.operator("inspect")["queue_db_superuser"],"ledger_db_superuser":self.ledger()["database_superuser"]}
        if any(security.values()):raise ValueError("legacy superuser application role detected; review the documented role migration before using this as a production stack")
        return {"project":self.project,"url":f"http://localhost:{self.args.port}/repair","services":states,"readiness":readiness,"security":security}

    def warm(self):
        for version in ("3.11","3.12"):command(["docker","pull",f"python:{version}-slim"])
        cold=self.operator("warm");warm=self.operator("warm")
        return {"base_versions":["3.11","3.12"],"first_prepare":cold,"cached_prepare":warm,"model_calls":0}

    def backup(self,directory):
        out=Path(directory).resolve()
        out.mkdir(mode=0o700,parents=True,exist_ok=False)
        paused=False;stopped=False
        try:
            self.operator("pause-admission");paused=True
            # Stop HTTP ingress first; a one-shot inspector executes no API lifespan.
            self.compose("stop","api");stopped=True
            before=self.operator("inspect",oneshot=True)
            before["ledger"]=self.ledger()
            if before["active_tasks"]:
                raise ValueError("finish/cancel active repair tasks before a consistent backup")
            self.compose("stop",*APP_SERVICES);stopped=True
            for service,user in DATABASES:
                with (out/(service+".dump")).open("wb") as stream:
                    self.compose("exec","-T",service,"pg_dump","-U",user,"-Fc",user,stdout=stream)
            for volume in VOLUMES:
                with (out/(volume+".tar")).open("wb") as stream:
                    command(["docker","run","--rm","--network","none","-v",self.project+"_"+volume+":/source:ro",
                        "--entrypoint","tar","postgres:16-alpine","-C","/source","-cf","-","."],stdout=stream)
            manifest={"version":1,"source_project":self.project,"snapshot":before,
                "files":{p.name:sha(p) for p in sorted(out.iterdir())},
                "images":{name:json.loads(command(["docker","image","inspect",self.env[name]]))[0]["Id"] for name in ("BACKEND_IMAGE","WEB_IMAGE")}}
            for path in out.iterdir():path.chmod(0o600)
            (out/"manifest.json").write_text(json.dumps(manifest,indent=2));(out/"manifest.json").chmod(0o600)
        finally:
            if stopped:self.compose("start",*APP_SERVICES)
            if paused:
                for _ in range(30):
                    try:self.operator("resume-admission");break
                    except RuntimeError:time.sleep(1)
                else:raise RuntimeError("admission remains paused; run resume-admission inside api")
        return {"backup":str(out),"checksums":manifest["files"],"snapshot":before}

    def restore(self,directory):
        source=Path(directory).resolve();manifest=json.loads((source/"manifest.json").read_text())
        if manifest["source_project"]==self.project:raise ValueError("restore requires a different, empty project")
        expected={s+".dump" for s,_ in DATABASES}|{v+".tar" for v in VOLUMES}
        if set(manifest["files"])!=expected:raise ValueError("unexpected backup members")
        for name,digest in manifest["files"].items():
            if sha(source/name)!=digest:raise ValueError("backup checksum mismatch: "+name)
            if name.endswith(".tar"):
                with tarfile.open(source/name) as archive:
                    for entry in archive:
                        if entry.name.startswith("/") or ".." in Path(entry.name).parts or not (entry.isdir() or entry.isfile()):
                            raise ValueError("unsafe backup archive")
        volumes=command(["docker","volume","ls","--format","{{.Name}}"]).decode().splitlines()
        containers=command(["docker","ps","-a","--filter","label=com.docker.compose.project="+self.project,"--format","{{.ID}}"])
        if containers.strip() or any(self.project+"_"+name in volumes for name in (*VOLUMES,"pgdata","ledger-pgdata")):
            raise ValueError("restore refuses existing target containers or data volumes")
        for name,image in manifest["images"].items():
            command(["docker","image","inspect",image])
            command(["docker","image","tag",image,self.env[name]])
        prepare(self.env["REPAIR_SOURCE_FIXTURES_DIR"])
        self.compose("up","-d","--wait","--wait-timeout","90","postgres","verifier-postgres")
        for service,user in DATABASES:
            self.compose("exec","-T",service,"pg_restore","-U",user,"-d",user,"--no-owner","--clean","--if-exists","--exit-on-error",
                         input=(source/(service+".dump")).read_bytes())
        for name in VOLUMES:
            volume=self.project+"_"+name
            command(["docker","volume","create",volume])
            command(["docker","run","-i","--rm","--network","none","-v",volume+":/target","--entrypoint","tar",
                "postgres:16-alpine","-C","/target","-xf","-"],input=(source/(name+".tar")).read_bytes())
        self.up(build=False)
        self.operator("resume-admission")
        after=self.operator("inspect")
        after["ledger"]=self.ledger()
        if after!=manifest["snapshot"]:raise ValueError("restored state differs from the saved snapshot")
        return {"restored":True,"project":self.project,"snapshot":after,"identical_receipt_hashes":True,"identical_replay_hashes":True}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("command",choices=["up","doctor","warm","backup","restore","seed-drill","down"])
    p.add_argument("--project",default="automl-v5-local");p.add_argument("--port",type=int,default=3133)
    p.add_argument("--state-dir");p.add_argument("--directory");p.add_argument("--allow-live",action="store_true")
    p.add_argument("--judge-only",action="store_true",help="reserve finite inference credits for invited judges; no provider keys in the legacy worker")
    args=p.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,50}",args.project):p.error("invalid project name")
    if args.command in {"backup","restore"} and not args.directory:p.error("--directory required")
    stack=Stack(args)
    if args.command=="up":result=stack.up()
    elif args.command=="doctor":result=stack.doctor()
    elif args.command=="warm":result=stack.warm()
    elif args.command=="seed-drill":result={**stack.operator(args.command),"ledger":stack.ledger("seed-ledger-drill"),"new_model_calls":0}
    elif args.command in {"backup","restore"}:result=getattr(stack,args.command)(args.directory)
    else:stack.compose("down");result={"stopped":True,"data_deleted":False}
    print(json.dumps(result,indent=2))


if __name__=="__main__":main()
