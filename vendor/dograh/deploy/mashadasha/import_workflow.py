"""Import the versioned Oasis graph through Dograh's authenticated API."""
import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:18080/api/v1")
    p.add_argument("--credentials", type=Path, default=Path("/var/lib/dograh/bootstrap.json"))
    p.add_argument("--bundle", type=Path, default=Path(__file__).parent / "workflow")
    p.add_argument("--record-name", default="workflow-install.json")
    args = p.parse_args()
    if Path(args.record_name).name != args.record_name:
        p.error("record-name must be a filename, not a path")
    credentials = json.loads(args.credentials.read_text())
    token = None

    def call(method, path, body=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(args.base_url + path, method=method, headers=headers,
                          data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(request, timeout=90) as response:
                return json.load(response)
        except HTTPError as exc:
            # Import requests contain no credentials except the login. Keep
            # login diagnostics opaque; graph errors are useful and non-secret.
            detail = "" if path.startswith("/auth") else exc.read().decode()[:8000]
            raise RuntimeError(f"{method} {path}: HTTP {exc.code} {detail}") from None

    token = call("POST", "/auth/login", {
        "email": credentials["email"], "password": credentials["password"]
    })["token"]
    create = json.loads((args.bundle / "create-request.json").read_text())
    update = json.loads((args.bundle / "update-request.json").read_text())
    matches = [w for w in call("GET", "/workflow/fetch") if w["name"] == create["name"]]
    if len(matches) > 1:
        raise RuntimeError("More than one matching workflow; refusing an ambiguous update")
    workflow = matches[0] if matches else call("POST", "/workflow/create/definition", create)
    workflow_id = workflow["id"]
    call("PUT", f"/workflow/{workflow_id}", update)
    validation = call("POST", f"/workflow/{workflow_id}/validate", {})
    if not validation.get("is_valid"):
        raise RuntimeError(json.dumps(validation, ensure_ascii=False))
    published = call("POST", f"/workflow/{workflow_id}/publish", {})
    record = {"workflow_id": workflow_id, "name": create["name"],
              "validation": validation, "published": True,
              "node_count": len(create["workflow_definition"]["nodes"]),
              "edge_count": len(create["workflow_definition"]["edges"])}
    (args.credentials.parent / args.record_name).write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
