"""Store behaviour: upsert semantics and durable round-trips."""

from __future__ import annotations

import json
from pathlib import Path

from conso.storage import AccountRecord, Store


def _store(tmp_path: Path) -> Store:
    return Store(str(tmp_path))


def test_add_twice_for_same_email_replaces_not_appends(tmp_path: Path) -> None:
    """Re-adding a refreshed record must not leave a stale duplicate behind.

    The token refresh path re-adds the record with new tokens; appending would
    keep the old row (holding an already-rotated refresh token) alongside it.
    """
    store = _store(tmp_path)
    store.add(AccountRecord(email="a@x.com", refresh_token="OLD"))
    store.add(AccountRecord(email="a@x.com", refresh_token="NEW"))

    accounts = store.all()
    assert len(accounts) == 1
    assert accounts[0].refresh_token == "NEW"


def test_add_different_emails_both_kept(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add(AccountRecord(email="a@x.com", refresh_token="A"))
    store.add(AccountRecord(email="b@x.com", refresh_token="B"))

    assert {a.email for a in store.all()} == {"a@x.com", "b@x.com"}


def test_upsert_persists_to_disk(tmp_path: Path) -> None:
    """A fresh Store over the same dir sees the replaced row, not two."""
    store = _store(tmp_path)
    store.add(AccountRecord(email="a@x.com", refresh_token="OLD"))
    store.add(AccountRecord(email="a@x.com", refresh_token="NEW"))

    raw = json.loads((tmp_path / "accounts.json").read_text())
    assert len(raw) == 1
    assert raw[0]["refresh_token"] == "NEW"

    reopened = _store(tmp_path)
    assert len(reopened.all()) == 1
    assert reopened.all()[0].refresh_token == "NEW"


def test_upsert_keeps_unset_fields_from_new_record(tmp_path: Path) -> None:
    """The newest record wins wholesale — no field merge across generations."""
    store = _store(tmp_path)
    store.add(AccountRecord(email="a@x.com", status="active", consoname="old-name"))
    store.add(AccountRecord(email="a@x.com", status="active", consoname="new-name"))

    account = store.all()[0]
    assert account.consoname == "new-name"
