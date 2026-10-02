"""Monarch category -> spending group: the rows of the card.

The card shows a handful of GROUPS, not Monarch's dozens of categories. The defaults below sort
Monarch's stock categories into eight everyday groups. Both the groups and the map are yours to
change: copy config.example.toml to ~/.config/monarch-now/config.toml and edit its [[groups]]
tables (cli.load_config reads them, `Grouping` holds them).

A category that is not listed lands in the LAST group, and the engine reports its name in
`unmapped` (`monarch-now status` prints the list), so a new or renamed Monarch category is
visible instead of silently miscounted.
"""

ORDER = ["Food", "Home & utilities", "Shopping", "Fun & travel", "Transport", "Kids & education", "Health", "Other"]

# Swatches, by position in the order. Color follows the group, never its rank this month. Six
# are Okabe-Ito steps (blue, sky blue, reddish purple, yellow) and two neutrals; orange is left
# out because amber is a STATUS color on this card (over a usual month), as green is (under), and
# nothing here may be red: red must never read as overspending. Two violets fill the gap.
# Checked against a dark popup surface (#1a1b26), adjacent pairs in this order: color-blind
# separation worst 9.6 (target 8), normal vision worst 20.0 (floor 15), contrast >= 3:1 for all
# eight. With fewer than eight groups the first ones are used.
PALETTE = ["#0072B2", "#56B4E9", "#CC79A7", "#F0E442", "#9085E9", "#D9D9D9", "#7A5FD0", "#9A9A9A"]
MAX_GROUPS = len(PALETTE)
assert len(ORDER) == MAX_GROUPS == 8

CATEGORY_GROUP = {
    # Food
    "Groceries": "Food",
    "Restaurants & Bars": "Food",
    "Coffee Shops": "Food",
    # Home & utilities
    "Mortgage": "Home & utilities",
    "Rent": "Home & utilities",
    "Home Improvement": "Home & utilities",
    "Gas & Electric": "Home & utilities",
    "Water": "Home & utilities",
    "Garbage": "Home & utilities",
    "Internet & Cable": "Home & utilities",
    "Software & Subscriptions": "Home & utilities",
    "Phone": "Home & utilities",
    "Insurance": "Home & utilities",
    # Shopping
    "Shopping": "Shopping",
    "Clothing": "Shopping",
    "Electronics": "Shopping",
    "Furniture & Housewares": "Shopping",
    # Fun & travel
    "Entertainment & Recreation": "Fun & travel",
    "Travel & Vacation": "Fun & travel",
    "Fun Money": "Fun & travel",
    # Transport
    "Gas": "Transport",
    "Auto Payment": "Transport",
    "Auto Maintenance": "Transport",
    "Parking & Tolls": "Transport",
    "Public Transit": "Transport",
    "Taxi & Ride Shares": "Transport",
    # Kids & education
    "Child Care": "Kids & education",
    "Child Activities": "Kids & education",
    "Education": "Kids & education",
    "Student Loans": "Kids & education",
    # Health
    "Medical": "Health",
    "Dentist": "Health",
    "Fitness": "Health",
    "Personal": "Health",
    # Other (fees, taxes, giving, pets, misc): also where anything unlisted lands
    "Charity": "Other",
    "Gifts": "Other",
    "Pets": "Other",
    "Taxes": "Other",
    "Financial Fees": "Other",
    "Financial & Legal Services": "Other",
    "Loan Repayment": "Other",
    "Cash & ATM": "Other",
    "Check": "Other",
    "Postage & Shipping": "Other",
    "Miscellaneous": "Other",
    "Uncategorized": "Other",
}
assert set(CATEGORY_GROUP.values()) <= set(ORDER)


class Grouping:
    """An ordered list of group names and a category -> group map. The defaults, or the config's."""

    def __init__(self, order=None, category_group=None):
        self.order = list(ORDER if order is None else order)
        self.category_group = dict(CATEGORY_GROUP if category_group is None else category_group)
        self.swatch = dict(zip(self.order, PALETTE))
        self.catch_all = self.order[-1]

    def group_of(self, category_name):
        """(group, mapped?)"""
        g = self.category_group.get(category_name)
        return (g, True) if g else (self.catch_all, False)


def from_config(tables):
    """The config's [[groups]] tables -> (Grouping, "") or (None, why it was not used).

    Each table: name = "Food", categories = ["Groceries", ...]. One to eight groups, in card
    order; the last one also takes every category that is not listed anywhere."""
    if not isinstance(tables, list) or not tables:
        return None, "groups must be a list of [[groups]] tables"
    if len(tables) > MAX_GROUPS:
        return None, "at most %d groups (there are %d)" % (MAX_GROUPS, len(tables))
    order, mapping = [], {}
    for table in tables:
        if not isinstance(table, dict):
            return None, "each group must be a [[groups]] table"
        name = table.get("name")
        if not isinstance(name, str) or not name.strip():
            return None, "every group needs a name"
        name = name.strip()
        if name in order:
            return None, "group %r is listed twice" % name
        categories = table.get("categories", [])
        if not isinstance(categories, list) or not all(isinstance(c, str) and c.strip() for c in categories):
            return None, "group %r: categories must be a list of category names" % name
        order.append(name)
        for c in categories:
            c = c.strip()
            if c in mapping:
                return None, "category %r is in two groups (%r and %r)" % (c, mapping[c], name)
            mapping[c] = name
    return Grouping(order, mapping), ""
