"""Bind every prepared environment to the admission's immutable dependency image."""
from app.repair.sandbox.base import SandboxError
import re


def immutable_image(image):
    return isinstance(image,str) and re.fullmatch(
        r"(?:sha256:[0-9a-f]{64}|nebius:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})",image) is not None


class PinnedRunner:
    def __init__(self,inner,expected):self.inner,self.expected=inner,expected
    def __getattr__(self,name):return getattr(self.inner,name)
    def prepare(self,*args,**kwargs):
        method=getattr(self.inner,"prepare_pinned",None)
        repo=args[0] if args else kwargs["repo_dir"]
        env=method(repo,image=self.expected,timeout_s=kwargs["timeout_s"]) if callable(method) else self.inner.prepare(*args,**kwargs)
        method=getattr(self.inner,"environment_image",None)
        image=method(env) if callable(method) else getattr(self.inner,"image",None)
        if image!=self.expected:
            self.inner.discard(env)
            raise SandboxError("dependency image differs from immutable admission; create a new task")
        return env


def pinned(runner,ecc):
    image=ecc.environment.get("image")
    return PinnedRunner(runner,image) if image else runner
