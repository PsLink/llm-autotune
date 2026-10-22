"""Publish an export to a catalog server (model-catalog's catalog-server/) through
its write API, instead of a zip someone unpacks into a checkout.

    a new model        POST api/v2/models  (metadata + its variants, one commit)
    a model it has     PUT  api/v2/models/<m>/variants/<id>  for each new or
                       updated variant (If-None-Match: * / If-Match: <ETag>),
                       then PUT api/v2/models/<m>/metadata  (If-Match)

Every write goes as a dry run first; the real writes start only when all of
them would pass, so a refused file never leaves the catalog half-updated by
this export. The server keeps the bytes as sent, checks semver and paths, and
does NOT validate the schema: the export's own check (valid) is the gate.

Reports are not published: the server has no write for them. The export is
built without one, so it records no tuning entry (the catalog's schema
requires a report there); the zip download still ships both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.control.catalog_export import CatalogExport

TIMEOUT = httpx.Timeout(30.0, connect=5.0)
YAML = "application/yaml"


@dataclass
class Write:
    path: str
    # add_model | add | replace | metadata
    action: str
    # ok | unchanged | failed | skipped
    status: str = ""
    commit: str | None = None
    version: str = ""
    digest: str = ""
    message: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "action": self.action,
            "status": self.status,
            "commit": self.commit,
            "version": self.version,
            "digest": self.digest,
            "message": self.message,
        }


@dataclass
class Published:
    url: str
    dry_run: bool
    ok: bool
    writes: list[Write] = field(default_factory=list)
    message: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "dry_run": self.dry_run,
            "ok": self.ok,
            "writes": [w.payload() for w in self.writes],
            "message": self.message,
        }


class _Refused(Exception):
    def __init__(self, write: Write):
        super().__init__(write.message)
        self.write = write


def _error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        text = str(body.get("error") or body.get("detail") or body)
    except ValueError:
        text = resp.text
    return f"{resp.status_code}: {text.strip()[:400]}"


def _ascii(text: str) -> str:
    """A header carries ASCII only; the commit message is ours, so drop the rest."""
    return text.encode("ascii", "ignore").decode()[:200]


class _Catalog:
    def __init__(self, client: httpx.AsyncClient, url: str, token: str, message: str):
        self.client = client
        self.url = url
        self.headers = {"Authorization": f"Bearer {token}"}
        if message:
            self.headers["X-Commit-Message"] = _ascii(message)

    async def etag(self, path: str) -> str | None:
        resp = await self.client.get(f"{self.url}/api/v2/{path}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise httpx.HTTPStatusError(_error(resp), request=resp.request, response=resp)
        return resp.headers.get("ETag")

    async def send(self, write: Write, method: str, path: str, dry_run: bool, **kw) -> None:
        params = {"dryRun": "true"} if dry_run else {}
        headers = {**self.headers, **kw.pop("headers", {})}
        resp = await self.client.request(
            method, f"{self.url}/api/v2/{path}", params=params, headers=headers, **kw
        )
        if resp.status_code not in (200, 201):
            write.status = "failed"
            write.message = _error(resp)
            if resp.status_code == 412:
                write.message += " (the catalog changed since this export read it: export again)"
            raise _Refused(write)
        body = resp.json() if resp.content else {}
        write.status = "unchanged" if body.get("unchanged") else "ok"
        write.commit = body.get("commit")
        write.version = str(body.get("version") or write.version)
        write.digest = str(body.get("digest") or "")


async def _writes(
    cat: _Catalog, export: CatalogExport, dry_run: bool, out: list[Write]
) -> list[Write]:
    """Each write, appended to `out` as it goes, so a refusal still shows
    what came before it."""
    name = export.name
    meta = export.file("metadata")
    variants = [f for f in export.files if f.kind == "variant"]
    versions = {v.path: v.version for v in export.variants}
    model_etag = await cat.etag(f"models/{name}/metadata")
    if model_etag is None:
        # A new model: its metadata and first variants in one commit.
        if meta is None or not variants:
            raise ValueError("a new model needs its metadata and at least one variant")
        write = Write(path=f"models/{name}", action="add_model")
        out.append(write)
        await cat.send(
            write, "POST", "models", dry_run,
            json={"metadata": meta.text, "variants": [f.text for f in variants]},
        )
        return out
    for f in variants:
        vid = f.path.rsplit("/", 1)[-1].removesuffix(".yaml")
        etag = await cat.etag(f"models/{name}/variants/{vid}")
        write = Write(path=f.path, action="replace" if etag else "add",
                      version=versions.get(f.path, ""))
        out.append(write)
        cond = {"If-Match": etag} if etag else {"If-None-Match": "*"}
        await cat.send(
            write, "PUT", f"models/{name}/variants/{vid}", dry_run,
            content=f.text.encode(), headers={"Content-Type": YAML, **cond},
        )
    if meta is not None:
        # After the variants: a defaultVariant may name one just added.
        write = Write(path=meta.path, action="metadata")
        out.append(write)
        etag = model_etag
        if not dry_run:
            # The variant commits moved the branch, not this file's digest,
            # but read it again: someone else may have written it meanwhile.
            etag = await cat.etag(f"models/{name}/metadata") or model_etag
        await cat.send(
            write, "PUT", f"models/{name}/metadata", dry_run,
            content=meta.text.encode(), headers={"Content-Type": YAML, "If-Match": etag},
        )
    return out


async def publish(
    export: CatalogExport,
    url: str,
    token: str,
    *,
    dry_run: bool,
    message: str = "",
    transport: httpx.AsyncBaseTransport | None = None,
) -> Published:
    """Dry-run every write; then, unless dry_run, make them. A new variant of
    an existing model checks as a dry run against the catalog as it is, so a
    metadata defaultVariant naming it can only be checked by the real write."""
    url = url.rstrip("/")
    async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
        cat = _Catalog(client, url, token, message)
        checked: list[Write] = []
        try:
            await _writes(cat, export, True, checked)
        except _Refused as exc:
            return Published(url, dry_run, False, checked,
                             message=f"{exc.write.path}: {exc.write.message}")
        except (httpx.HTTPError, ValueError) as exc:
            return Published(url, dry_run, False, message=f"could not reach the catalog: {exc}")
        if dry_run:
            return Published(url, True, True, checked,
                             message=f"{len(checked)} write(s) would pass; nothing committed")
        done: list[Write] = []
        try:
            await _writes(cat, export, False, done)
        except _Refused as exc:
            return Published(url, False, False, done,
                             message=f"{exc.write.path}: {exc.write.message}; the writes "
                             "before it are committed")
        except (httpx.HTTPError, ValueError) as exc:
            return Published(url, False, False, done,
                             message=f"the catalog stopped answering mid-publish: {exc}")
    changed = sum(1 for w in done if w.status == "ok")
    return Published(url, False, True, done,
                     message=f"{changed} file(s) committed" if changed else "already up to date")
