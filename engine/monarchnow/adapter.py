"""THE data source. Everything that knows Monarch's API is in this one file; the rest of the
engine sees only `fetch_snapshot()` and `verify()` and the normalised shape below. Swapping to
an official API, should Monarch ever publish one, means rewriting this file and nothing else.

Client: monarchmoneycommunity 1.6.0 (a maintained fork of hammem/monarchmoney), pinned by hash
in requirements.txt. It targets https://api.monarch.com, so no domain patch is carried.
UNOFFICIAL: Monarch documents no public API. This is the web app's own GraphQL endpoint, and it
can change or stop working without notice.

🔴 READ-ONLY, BY CONSTRUCTION AND BY GUARD.
   1. This module sends four GraphQL documents, all written here, all `query`.
   2. `ReadOnlyMonarch.gql_call` parses every document it is handed and refuses anything that is
      not a query (a mutation or a subscription raises ReadOnlyViolation before any request).
   3. The client's REST paths (login, uploads, session files) are overridden to raise.
   A Monarch session cookie can do everything the web app can; this is what keeps the bar from
   ever changing Monarch.

The queries select only what the model needs. In particular: no balances, no notes, no
attachments, no merchant on a transaction.

NORMALISED SNAPSHOT (also the cache's shape; amounts are decimal strings)
    schema, source, fetched_at (epoch), window_start, window_end (YYYY-MM-DD)
    transactions[] {id, date, amount, category_id, category, group, group_type, tags[], pending,
                    hidden, account_id}
    accounts[]     {id, name, updated_at (ISO or null), hidden, manual, needs_reconnect, sync_disabled}
    recurring[]    {date, amount, name, is_past, approximate}
"""
import asyncio
import datetime
import time

SCHEMA = 1
SOURCE = "monarchmoneycommunity 1.6.0 (api.monarch.com)"
PAGE = 500
MAX_PAGES = 40
CALL_TIMEOUT = 30


class FetchError(Exception):
    """kind: 'auth' (signed out or refused), 'network', 'shape' (an answer this file cannot read)."""

    def __init__(self, kind, detail=""):
        super().__init__("%s%s" % (kind, (": " + detail) if detail else ""))
        self.kind = kind
        self.detail = detail


class ReadOnlyViolation(Exception):
    pass


Q_CATEGORIES = """
query GetCategories {
  categories { id name group { id name type } }
}
"""

Q_ACCOUNTS = """
query GetAccounts {
  accounts {
    id displayName syncDisabled deactivatedAt isHidden hideTransactionsFromReports isManual
    displayLastUpdatedAt
    credential { id updateRequired disconnectedFromDataProviderAt }
  }
}
"""

Q_TRANSACTIONS = """
query GetTransactionsList($offset: Int, $limit: Int, $filters: TransactionFilterInput, $orderBy: TransactionOrdering) {
  allTransactions(filters: $filters) {
    totalCount
    results(offset: $offset, limit: $limit, orderBy: $orderBy) {
      id amount pending date hideFromReports
      category { id name }
      account { id }
      tags { id name }
    }
  }
}
"""

Q_RECURRING = """
query Web_GetUpcomingRecurringTransactionItems($startDate: Date!, $endDate: Date!, $filters: RecurringTransactionFilter) {
  recurringTransactionItems(startDate: $startDate, endDate: $endDate, filters: $filters) {
    stream { id isApproximate merchant { id name } }
    date isPast amount
  }
}
"""

Q_VERIFY = """
query GetHouseholdTransactionTags {
  householdTransactionTags { id }
}
"""


def assert_query_only(document):
    """Raise ReadOnlyViolation unless every operation in a parsed GraphQL document is a query."""
    from graphql import OperationDefinitionNode, OperationType
    # gql 4 wraps the parsed document in a GraphQLRequest; older versions hand over the DocumentNode.
    node = getattr(document, "document", document)
    definitions = getattr(node, "definitions", None)
    if definitions is None:
        raise ReadOnlyViolation("cannot inspect the document, so it is refused")
    ops = [d for d in definitions if isinstance(d, OperationDefinitionNode)]
    if not ops:
        raise ReadOnlyViolation("no operation in the document")
    for op in ops:
        if op.operation != OperationType.QUERY:
            raise ReadOnlyViolation("refused a %s: this engine only reads" % op.operation.value)


def _client(cookies):
    from monarchmoney import MonarchMoney

    class ReadOnlyMonarch(MonarchMoney):
        async def gql_call(self, operation, graphql_query, variables={}):
            assert_query_only(graphql_query)
            return await super().gql_call(operation, graphql_query, variables)

        # The client's write and file paths. None is reachable from this module; they are closed
        # anyway, so a later edit cannot reach them by accident.
        def save_session(self, *a, **k):
            raise ReadOnlyViolation("no session file: the session lives in the keyring")

        def load_session(self, *a, **k):
            raise ReadOnlyViolation("no session file: the session lives in the keyring")

        async def login(self, *a, **k):
            raise ReadOnlyViolation("sign in with `monarch-now login`")

        async def _upload_form_data(self, *a, **k):
            raise ReadOnlyViolation("refused an upload: this engine only reads")

    mm = ReadOnlyMonarch(timeout=CALL_TIMEOUT)
    mm.set_cookies(dict(cookies))
    return mm


def _classify(exc):
    """An exception from a call -> FetchError. Says which kind, never echoes a body or a header."""
    name = type(exc).__name__
    code = getattr(exc, "code", None)
    text = str(exc).lower()
    authish = any(w in text for w in ("unauthorized", "not authenticated", "authentication", "forbidden", "csrf"))
    if code in (401, 403):
        return FetchError("auth", "HTTP %s" % code)
    if name == "TransportQueryError":
        # Monarch answered, with GraphQL errors: signed out, or a field this file asks for is gone.
        return FetchError("auth" if authish else "shape", name)
    if name == "TransportServerError":
        return FetchError("network", "HTTP %s" % code if code else name)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, OSError)) or any(w in name for w in ("Client", "Timeout", "Connect", "Transport")):
        return FetchError("network", name)
    return FetchError("shape", name)


async def _call(mm, operation, text, variables=None):
    from gql import gql
    try:
        return await mm.gql_call(operation, gql(text), variables or {})
    except ReadOnlyViolation:
        raise
    except Exception as e:      # noqa: BLE001 - classified, then re-raised as ours
        raise _classify(e) from None


async def _fetch(cookies, start, end, today):
    mm = _client(cookies)
    cats = await _call(mm, "GetCategories", Q_CATEGORIES)
    accounts = await _call(mm, "GetAccounts", Q_ACCOUNTS)
    raw = []
    total = None
    for page in range(MAX_PAGES):
        variables = {"offset": page * PAGE, "limit": PAGE, "orderBy": "date",
                     "filters": {"search": "", "categories": [], "accounts": [], "tags": [],
                                 "transactionVisibility": "all_transactions",
                                 "startDate": start.isoformat(), "endDate": end.isoformat()}}
        doc = await _call(mm, "GetTransactionsList", Q_TRANSACTIONS, variables)
        try:
            block = doc["allTransactions"]
            total = int(block["totalCount"])
            results = block["results"]
        except (KeyError, TypeError, ValueError):
            raise FetchError("shape", "transactions")
        raw.extend(results)
        if not results or len(raw) >= total:
            break
    else:
        raise FetchError("shape", "transactions did not end in %d pages" % MAX_PAGES)
    if total is None or len(raw) != total:
        # A short read would silently understate spending. Refuse it.
        raise FetchError("shape", "transaction count mismatch")
    recurring = await _call(mm, "Web_GetUpcomingRecurringTransactionItems", Q_RECURRING,
                            {"startDate": today.isoformat(),
                             "endDate": (today + datetime.timedelta(days=45)).isoformat()})
    return normalise(cats, accounts, raw, recurring, start, end)


def normalise(cats, accounts, raw_transactions, recurring, start, end):
    """Monarch's answers -> the snapshot. Raises FetchError('shape') on anything unexpected."""
    try:
        cat_map = {}
        for c in cats["categories"]:
            g = c.get("group") or {}
            cat_map[c["id"]] = (c["name"], g.get("name") or "", g.get("type") or "")
        seen = set()
        txns = []
        for t in raw_transactions:
            if t["id"] in seen:
                continue                      # a page boundary can repeat a row; count it once
            seen.add(t["id"])
            cid = (t.get("category") or {}).get("id") or ""
            name, group, gtype = cat_map.get(cid, ((t.get("category") or {}).get("name") or "Uncategorized", "", ""))
            txns.append({
                "id": t["id"],
                "date": t["date"][:10],
                "amount": str(t["amount"]),
                "category_id": cid,
                "category": name,
                "group": group,
                "group_type": gtype,
                "tags": sorted(tag["name"] for tag in (t.get("tags") or [])),
                "pending": bool(t.get("pending")),
                "hidden": bool(t.get("hideFromReports")),
                "account_id": (t.get("account") or {}).get("id") or "",
            })
        accts = []
        for a in accounts["accounts"]:
            cred = a.get("credential") or {}
            accts.append({
                "id": a["id"],
                "name": a.get("displayName") or "",
                "updated_at": a.get("displayLastUpdatedAt"),
                "hidden": bool(a.get("hideTransactionsFromReports")),
                "manual": bool(a.get("isManual")),
                "needs_reconnect": bool(cred.get("updateRequired") or cred.get("disconnectedFromDataProviderAt")),
                "sync_disabled": bool(a.get("syncDisabled")) or bool(a.get("deactivatedAt")),
            })
        rec = []
        for r in recurring["recurringTransactionItems"]:
            stream = r.get("stream") or {}
            rec.append({
                "date": r["date"][:10],
                "amount": str(r["amount"]),
                "name": ((stream.get("merchant") or {}).get("name")) or "",
                "is_past": bool(r.get("isPast")),
                "approximate": bool(stream.get("isApproximate")),
            })
    except (KeyError, TypeError, AttributeError) as e:
        raise FetchError("shape", type(e).__name__)
    return {"schema": SCHEMA, "source": SOURCE, "fetched_at": time.time(),
            "window_start": start.isoformat(), "window_end": end.isoformat(),
            "transactions": txns, "accounts": accts, "recurring": rec}


def fetch_snapshot(cookies, start, end, today):
    """One read of Monarch: categories, accounts, the window's transactions, upcoming recurring."""
    return asyncio.run(_fetch(cookies, start, end, today))


def verify(cookies):
    """One tiny read-only query. True when Monarch accepts the session; FetchError otherwise."""
    async def go():
        mm = _client(cookies)
        doc = await _call(mm, "GetHouseholdTransactionTags", Q_VERIFY)
        if "householdTransactionTags" not in doc:
            raise FetchError("shape", "verify")
        return True
    return asyncio.run(go())
