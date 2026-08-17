import json
from types import SimpleNamespace
import pytest
from modules import workspace_app

REVISION = "a" * 40

def release():
    return {"schema":"workspace.release-candidate@1","releaseId":f"workspace-{REVISION}","revision":REVISION,"createdAt":"2026-08-17T10:00:00.000Z","platform":workspace_app._platform(),"migrationInventorySha256":"sha256:"+"d"*64,"images":{"web":{"repository":workspace_app.REPOSITORIES["web"],"digest":"sha256:"+"b"*64,"revision":REVISION},"migrator":{"repository":workspace_app.REPOSITORIES["migrator"],"digest":"sha256:"+"c"*64,"revision":REVISION}},"promotion":{"status":"blocked-until-trust-root","signature":None,"trustRoot":None}}

@pytest.fixture
def handler(monkeypatch, tmp_path):
    app = tmp_path / "workspace"; app.mkdir(); (app / "compose.yaml").write_text("services: {}\n")
    for name, value in {"APP_DIR":app,"COMPOSE_FILE":app/"compose.yaml","STATUS_FILE":app/"status.json","AUDIT_FILE":app/"audit.jsonl","LOCK_FILE":app/"lock","BACKUP_DIR":app/"backups","PROBE_DUMP":app/"probe/source.dump","BOOTSTRAP_MARKER":app/".synthetic-bootstrap-complete","BIND_ADDRESS_FILE":app/"bind-address"}.items(): monkeypatch.setattr(workspace_app,name,value)
    return app

def test_start_uses_only_local_images_and_fixed_order(handler, monkeypatch):
    calls=[]; monkeypatch.setattr(workspace_app,"health",lambda:{"health":"healthy"})
    result=workspace_app.start(initiator_role="admin",runner=lambda command,**kwargs:calls.append((command,kwargs)))
    assert result=={"state":"running","health":"healthy"}
    assert calls[0][0][-1]=="workspace-web:local" and calls[1][0][-1]=="workspace-migrator:local"
    assert calls[2][0][-1]=="postgres" and calls[3][0][-1]=="migrator" and calls[4][0][-1]=="bootstrap-development" and calls[5][0][-2:]==("web","gateway")
    assert workspace_app.BOOTSTRAP_MARKER.read_text()=="synthetic-only\n"
    assert all(call[1]["env"]["WORKSPACE_WEB_IMAGE"]=="workspace-web:local" for call in calls)
    assert all(call[1]["env"]["WORKSPACE_BIND_ADDRESS"]=="127.0.0.1" for call in calls)

def test_bind_address_accepts_only_loopback_or_private_ip(handler):
    workspace_app.BIND_ADDRESS_FILE.write_text("192.168.178.60\n")
    assert workspace_app._bind_address()=="192.168.178.60"
    workspace_app.BIND_ADDRESS_FILE.write_text("8.8.8.8\n")
    with pytest.raises(workspace_app.WorkspaceUpdateError,match="bind-address-not-local"): workspace_app._bind_address()
    workspace_app.BIND_ADDRESS_FILE.write_text("all-interfaces\n")
    with pytest.raises(workspace_app.WorkspaceUpdateError,match="bind-address-invalid"): workspace_app._bind_address()

def test_health_uses_the_validated_bind_address(handler, monkeypatch):
    workspace_app.BIND_ADDRESS_FILE.write_text("192.168.178.60\n")
    requested=[]
    class Response:
        status=200
        def __enter__(self): return self
        def __exit__(self,*args): return False
    monkeypatch.setattr(workspace_app.urllib.request,"urlopen",lambda url,timeout: requested.append((url,timeout)) or Response())
    assert workspace_app.health()=={"health":"healthy"}
    assert requested==[("http://192.168.178.60:3100/health",3)]

def test_stop_is_managed(handler):
    calls=[]; assert workspace_app.stop(initiator_role="admin",runner=lambda command,**kwargs:calls.append(command))=={"state":"stopped"}
    assert calls==[("docker","compose","-f","docker-compose.yml","stop")]

def test_update_verifies_before_any_command(handler):
    calls=[]
    def runner(command,**kwargs):
        calls.append(command)
        if kwargs.get("stdout_path"): kwargs["stdout_path"].parent.mkdir(parents=True,exist_ok=True); kwargs["stdout_path"].write_bytes(b"dump")
    result=workspace_app.run_update(initiator_role="admin",verifier=release,runner=runner)
    assert result["state"]=="succeeded"
    assert calls[0][:3]==("docker","image","inspect") and calls[1][:3]==("docker","image","inspect")
    assert [json.loads(line)["state"] for line in workspace_app.AUDIT_FILE.read_text().splitlines()][:4]==["requested","locked","resolved","verified"]

@pytest.mark.parametrize("code",["release-candidate-hash-mismatch","release-promotion-invalid","release-trust-key-inactive","cosign-verification-failed"])
def test_verification_failure_has_no_docker_backup_migration_or_switch(handler, code):
    calls=[]
    def deny(): raise workspace_app.WorkspaceUpdateError(code)
    result=workspace_app.run_update(initiator_role="admin",verifier=deny,runner=lambda *args,**kwargs:calls.append(args))
    assert result["state"]=="failed" and result["errorCode"]==code and calls==[]

def test_candidate_must_remain_blocked(handler):
    value=release(); value["promotion"]["status"]="promoted"
    with pytest.raises(workspace_app.WorkspaceUpdateError,match="release-candidate-promotion-invalid"): workspace_app.validate_release(value)

def test_default_verifier_real_cosign_success_and_negatives(handler, monkeypatch, tmp_path):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    candidate=release(); candidate_raw=workspace_app._artifact_canonical(candidate)
    signature=b"synthetic-signature"; key=ec.generate_private_key(ec.SECP256R1()).public_key()
    key_raw=key.public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    der=key.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo); fingerprint=workspace_app._sha(der)
    promotion={"schema":"workspace.release-promotion@1","releaseId":candidate["releaseId"],"revision":candidate["revision"],"candidateSha256":workspace_app._sha(candidate_raw),"signatureSha256":workspace_app._sha(signature),"verification":{"scheme":"cosign-verify-blob@1","trustKeyFingerprint":fingerprint,"verifiedAt":"2026-08-17T10:00:01.000Z"},"status":"promoted"}; promotion_raw=workspace_app._artifact_canonical(promotion)
    trust={"schema":"workspace.cosign-trust-root@1","keys":[{"fingerprint":fingerprint,"publicKeyFile":"primary.pub","status":"active","addedAt":"2026-08-17T09:00:00.000Z","revokedAt":None}]}
    paths={"CANDIDATE_FILE":handler/"candidate.json","CANDIDATE_SHA256_FILE":handler/"candidate.sha","SIGNATURE_FILE":handler/"candidate.sig","PROMOTION_FILE":handler/"promotion.json","PROMOTION_SHA256_FILE":handler/"promotion.sha","TRUST_ROOT_FILE":handler/"trust/trust-root.json","COSIGN_BIN":handler/"cosign"}
    for name,path in paths.items(): monkeypatch.setattr(workspace_app,name,path)
    paths["TRUST_ROOT_FILE"].parent.joinpath("keys").mkdir(parents=True); paths["CANDIDATE_FILE"].write_bytes(candidate_raw); paths["CANDIDATE_SHA256_FILE"].write_text(workspace_app._sha(candidate_raw)); paths["SIGNATURE_FILE"].write_bytes(signature); paths["PROMOTION_FILE"].write_bytes(promotion_raw); paths["PROMOTION_SHA256_FILE"].write_text(workspace_app._sha(promotion_raw)); paths["TRUST_ROOT_FILE"].write_bytes(workspace_app._artifact_canonical(trust)); paths["TRUST_ROOT_FILE"].parent.joinpath("keys/primary.pub").write_bytes(key_raw); paths["COSIGN_BIN"].write_text("fake"); paths["COSIGN_BIN"].chmod(0o700)
    monkeypatch.setattr(workspace_app.subprocess,"run",lambda *a,**k:SimpleNamespace(returncode=0))
    assert workspace_app._verify_artifacts()["releaseId"]==candidate["releaseId"]
    paths["SIGNATURE_FILE"].write_bytes(b"tampered")
    with pytest.raises(workspace_app.WorkspaceUpdateError,match="release-promotion-binding-invalid"): workspace_app._verify_artifacts()
    paths["SIGNATURE_FILE"].write_bytes(signature); trust["keys"][0].update(status="revoked",revokedAt="2026-08-17T10:00:02.000Z"); paths["TRUST_ROOT_FILE"].write_bytes(workspace_app._artifact_canonical(trust))
    with pytest.raises(workspace_app.WorkspaceUpdateError,match="release-trust-key-inactive"): workspace_app._verify_artifacts()

def test_status_is_redacted(handler):
    workspace_app._atomic_json(workspace_app.STATUS_FILE,{"runId":"run","state":"failed","errorCode":"command-failed","output":"secret","backupPath":"private"})
    assert workspace_app.status()=={"runId":"run","state":"failed","errorCode":"command-failed"}
