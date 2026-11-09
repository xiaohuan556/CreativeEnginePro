"""Exercise the desktop client against a fresh, isolated Duo runtime."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

from PIL import Image

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from core.duo_delivery import DELIVERY_API, REQUEST_API, DeliveryJournal, DuoClient, DuoError, inspect_media


def main(root, node):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    address = f"http://127.0.0.1:{port}"
    if (root / "config.json").exists() or (root / "data").exists():
        raise RuntimeError("Use a fresh extracted Duo copy, never a running workspace.")
    (root / "config.json").write_text(json.dumps({"host": "127.0.0.1", "port": port,
        "publicUrl": address, "dataDir": "./data"}), encoding="utf-8")
    password = "Synthetic-local-test-" + uuid4().hex
    env = {**os.environ, "DUO_INIT_ACCOUNT": "local.admin", "DUO_INIT_NAME": "Test Admin",
           "DUO_INIT_EMAIL": "admin@example.test", "DUO_INIT_PASSWORD": password}
    hidden = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    init = subprocess.run([node, "scripts/init.mjs"], cwd=root, env=env, capture_output=True,
                          text=True, encoding="utf-8", creationflags=hidden)
    if init.returncode:
        raise RuntimeError(init.stderr or init.stdout)
    with (root / "delivery-server.log").open("wb") as log:
        process = subprocess.Popen([node, "server.mjs"], cwd=root, stdout=log,
                                   stderr=subprocess.STDOUT, creationflags=hidden)
        try:
            admin = DuoClient(address)
            for _ in range(80):
                try:
                    if admin._call("GET", "/health")["status"] == "ok":
                        break
                except DuoError:
                    time.sleep(0.1)
                if process.poll() is not None:
                    raise RuntimeError((root / "delivery-server.log").read_text(encoding="utf-8"))
            else:
                raise RuntimeError("Duo test server did not start")
            exercise(root, address, admin, password, hidden)
        finally:
            process.terminate()
            process.wait(timeout=10)


def exercise(root, address, admin, password, hidden):
    admin_id = admin.login("local.admin", password)["user"]["id"]
    for role, name, code in (("designer", "Test Designer", "DA003"), ("optimizer", "Test Optimizer", "MID004")):
        admin._call("POST", "/api/accounts", json={"email": f"{role}@example.test", "account": f"local.{role}",
            "name": name, "code": code, "role": role, "password": password})
    workspace = admin._call("GET", "/api/state")
    catalog = workspace["data"]["catalog"]
    catalog.update(optimizers={"Test Optimizer": "MID004"}, designers={"Test Designer": "DA003"},
                   channels={"Google": "GG"}, tags=[{"id": "test-tag", "name": "Campaign"},
                                                      {"id": "off-tag", "name": "Inactive", "disabled": True}])
    product = next(row for row in catalog["products"] if row["platform"] == "IOS" and not row.get("disabled"))
    request = {"ownerId": admin_id, "description": "Delivery test", "requester": "Test Optimizer",
               "product": product["id"], "platform": "IOS", "channel": "Google", "designer": "Test Designer", "files": []}
    workspace["data"]["requests"] = [dict(request, id=101), dict(request, id=102, designer="Another Designer")]
    admin._call("PUT", "/api/state", json=workspace)
    designer = DuoClient(address)
    assert [row["id"] for row in designer.login("local.designer", password)["requests"]] == [101]
    optimizer = DuoClient(address)
    assert optimizer.login("local.optimizer", password)["requests"] == []

    image, video = root / "cover.png", root / "clip.mp4"
    Image.new("RGB", (16, 16), "blue").save(image)
    subprocess.run([str(REPO / "ffmpeg.exe"), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=32x32:d=0.2", "-c:v", "libx264", str(video)],
                   check=True, creationflags=hidden)
    journal = DeliveryJournal(root / "local-delivery.json")
    for invalid in (999, 102):
        try:
            journal.start(designer, invalid, [image], root / "archive")
        except DuoError as error:
            assert error.status == 404 and journal.read() is None
        else:
            raise AssertionError("Accepted missing or unassigned requirement")
    batch = journal.start(designer, 101, [image, video], root / "archive")
    commit = designer.commit

    def lose_reply(value):
        commit(value)
        raise DuoError("Simulated lost reply")

    designer.commit = lose_reply
    try:
        journal.resume(batch, designer)
    except DuoError as error:
        assert str(error) == "Simulated lost reply"
    else:
        raise AssertionError("Expected the commit reply to be lost")
    designer.commit = commit
    receipt, folder = journal.resume(journal.read(), designer)
    assert len(receipt["files"]) == 2 and journal.read() is None
    for file, original in zip(receipt["files"], [image, video]):
        assert "_IOS_GG_MID004_DA003_" in file["name"]
        assert (folder / file["name"]).read_bytes() == original.read_bytes()

    assert designer.requests()["requests"] == []
    assert rejected(409, designer.requests, 101).code == "already_delivered"
    workspace = admin._call("GET", "/api/state")
    workspace["data"]["requests"][0]["files"] = []
    admin._call("PUT", "/api/state", json=workspace)
    assert designer.requests()["requests"] == []
    assert designer.commit(batch) == receipt

    options = designer.publication_form()
    assert options["requesters"] == ["Test Optimizer"] and options["designers"] == ["Test Designer"]
    assert options["tags"] == [{"id": "test-tag", "name": "Campaign"}]
    assert any("BOTH" in item["platforms"] for item in options["products"])
    fields = {"description": "New desktop request", "product": product["name"], "platform": "IOS",
              "channel": "Google", "priority": "紧急", "requester": "Test Optimizer",
              "designer": "Test Designer", "scheduleEnd": options["today"], "tag": "test-tag"}
    draft = {"publicationId": str(uuid4()), "uploadIds": [], **fields}
    rejected(403, designer._call, "POST", REQUEST_API, json={**draft, "designer": "Another Designer"})
    rejected(403, optimizer._call, "POST", REQUEST_API, json={**draft, "requester": "Another Optimizer"})
    rejected(400, designer._call, "POST", REQUEST_API, json={**draft, "scheduleEnd": "2000-01-01"})
    rejected(400, designer._call, "POST", REQUEST_API, json={**draft, "tag": "off-tag"})
    upload = admin.upload(image, image.name, "image/png")
    rejected(403, designer._call, "POST", REQUEST_API, json={**draft, "uploadIds": [upload]})
    attachments = [inspect_media(image)]
    publication_id = str(uuid4())
    request_id = designer.publish_request(publication_id, fields, attachments)
    assert designer.publish_request(publication_id, fields, attachments) == request_id
    assert [row["id"] for row in designer.requests()["requests"]] == [request_id]
    assert optimizer.requests()["requests"] == []
    rejected(403, designer._call, "POST", DELIVERY_API,
             json={"requestId": request_id, "batchId": str(uuid4()), "uploadIds": [upload]})
    second = DuoClient(address)
    second.login("local.designer", password)
    clients = (designer, second)
    bodies = [{"requestId": request_id, "batchId": str(uuid4()),
               "uploadIds": [client.upload(image, image.name, "image/png")]}
              for client in clients]
    accepted, conflicts = [], []
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(client._call, "POST", DELIVERY_API, json=value) for client, value in zip(clients, bodies)]
        for job in jobs:
            try:
                accepted.append(job.result())
            except DuoError as error:
                conflicts.append((error.status, error.code))
    assert len(accepted) == 1 and conflicts == [(409, "already_delivered")]
    actual = admin._call("GET", "/api/state")["data"]["requests"]
    published = [item for item in actual if item["id"] == request_id]
    assert len(published) == 1
    assert published[0]["description"] == fields["description"]
    assert published[0]["scheduleStart"] == options["today"] and published[0]["scheduleEnd"] == options["today"]
    assert published[0]["attachments"][0]["url"] == "/api/files/" + attachments[0]["upload_id"]
    assert published[0]["files"] == accepted[0]["files"] and designer.requests()["requests"] == []
    # Files attached through the website also complete the requirement.
    web_id = optimizer.publish_request(str(uuid4()), fields, [])
    workspace = admin._call("GET", "/api/state")
    next(item for item in workspace["data"]["requests"] if item["id"] == web_id)["files"] = [
        {"name": image.name, "type": "image/png", "url": "/api/files/" + upload},
    ]
    admin._call("PUT", "/api/state", json=workspace)
    assert designer.requests()["requests"] == []
    assert rejected(409, designer.requests, web_id).code == "already_delivered"
    print("PASS: publish and delivery permissions, reference attachment, idempotency, completed filtering, concurrent rejection")


def rejected(status, call, *args, **kwargs):
    try:
        call(*args, **kwargs)
    except DuoError as error:
        assert error.status == status, (error.status, str(error))
        return error
    raise AssertionError(f"Expected request to be rejected with {status}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), sys.argv[2])
