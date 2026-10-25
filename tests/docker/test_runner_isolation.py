"""Isolation is demonstrated by processes on the real Docker daemon."""

import pytest
from test_runner_restore import archive

pytest_plugins = ["test_runner_restore"]


@pytest.mark.docker
def test_network_loopback_credentials_socket_and_readonly_root(runner, plan, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "host-secret-never-forward")
    prepared = runner.prepare(plan, archive({"original.txt": b"original"}))
    script = """
const fs=require('fs'),net=require('net'),http=require('http');
if(process.env.GITHUB_TOKEN || process.env.GH_TOKEN || process.env.DOCKER_HOST) process.exit(2);
if(fs.existsSync('/var/run/docker.sock')) process.exit(3);
try { fs.writeFileSync('/etc/escaped','bad'); process.exit(4); } catch(e) {
 if(e.code!=='EROFS' && e.code!=='EACCES') throw e;
}
const server=http.createServer((q,s)=>s.end('mock'));
server.listen(0,'127.0.0.1',()=>{
 http.get('http://127.0.0.1:'+server.address().port,r=>{
  r.resume(); r.on('end',()=>{
   server.close(); const s=net.connect({host:'1.1.1.1',port:443});
   s.on('connect',()=>process.exit(5));
   s.on('error',()=>console.log('isolated mock works'));
   s.setTimeout(1500,()=>{s.destroy();process.exit(6)});
  });
 });
});
"""
    result = runner.run_fresh(prepared.prepared_id, ("node", "-e", script), plan.limits)
    assert b"isolated mock works" in runner.artifacts.get(result.stdout_artifact)
    assert result.storage_policy == "readonly-root+bounded-tmpfs-v1"
    assert result.storage_verified


@pytest.mark.docker
def test_workspace_limit_is_really_enforced_and_observed(runner, plan):
    from dataclasses import replace

    from robo_minerador.execution.docker import ProjectExecutionFailure

    small = replace(plan, limits=replace(plan.limits, workspace_limit_mb=16))
    prepared = runner.prepare(small, archive({"original.txt": b"original"}))
    script = """
const fs=require('fs'); console.log('filling');
const fd=fs.openSync('large','w');const b=Buffer.alloc(1024*1024,1);
for(let i=0;i<40;i++)fs.writeSync(fd,b);
"""
    with pytest.raises(ProjectExecutionFailure) as failure:
        runner.run_fresh(prepared.prepared_id, ("node", "-e", script), small.limits)
    assert b"ENOSPC" in runner.artifacts.get(failure.value.result.stderr_artifact)
    assert failure.value.result.storage_verified
    assert failure.value.result.workspace_used_bytes > 0
