# Task for the elkscharity thread — the My Account tile is invisible to the people who need it

You maintain `elkscharity` (19.0.7.24) for Lewiston Elks Lodge #896.

Danny asked for the volunteer hour submission form to be available in the
**My Account** section of the portal. It already is — `/my/charity/new`, with
a tile added by `portal_my_home_charity`. The problem is that **the tile never
renders for a member who has not submitted before**, which is precisely the
member who needs to find it.

One line fixes it. Details below so you can confirm rather than take my word.

---

## The mechanism

**File:** `views/charity_portal_templates.xml`, template `portal_my_home_charity`
(around line 8).

It calls `portal.portal_docs_entry` with a `placeholder_count`:

```xml
<t t-call="portal.portal_docs_entry">
    <t t-set="title">Charity Entries</t>
    <t t-set="text">My Data Collection Survey submissions</t>
    <t t-set="url" t-value="'/my/charity'"/>
    <t t-set="placeholder_count" t-value="'charity_count'"/>
</t>
```

In Odoo 19, `portal.portal_docs_entry`
(`addons/portal/views/portal_templates.xml`, line 237) opens with:

```xml
<t t-set="force_show" t-value="placeholder_count and request.session.get('portal_counters', {}).get(placeholder_count) and not show_count"/>
<div t-att-class="'o_portal_index_card ' + ('' if force_show or config_card else 'd-none ') + ..."/>
```

So a card is stamped `d-none` **unless** either:

- `force_show` — which needs `portal_counters[placeholder_count]` to be
  **truthy**, or
- `config_card` is set.

`charity_count` is the member's own submissions
(`search_count([("submitted_by", "=", request.env.user.id)])`). For a member
who has never submitted that is `0` → falsy → `force_show` is false →
`config_card` is unset → the card is hidden.

The tile is therefore visible only to members who have already used the form.
A first-time submitter has no way in from My Account.

## Confirmed, not inferred

I installed a probe module on a clean Odoo 19 with two tiles — one using the
current `elkscharity` pattern, one with `config_card` added — and loaded `/my`
as a brand-new portal user with zero records:

```
COUNT-GATED TILE  (current pattern):  hidden (d-none)
ALWAYS-VISIBLE TILE (config_card):    VISIBLE
```

Screenshot attached. Both tiles are in the DOM; only the `config_card` one is
rendered. Core itself uses `config_card` for the cards that must always
appear — "Addresses" and "Connection & Security" on that same page, and
`payment`'s saved-methods card.

## The fix

Add one line to `portal_my_home_charity`:

```xml
<t t-call="portal.portal_docs_entry">
    <t t-set="title">Charity Entries</t>
    <t t-set="text">My Data Collection Survey submissions</t>
    <t t-set="url" t-value="'/my/charity'"/>
    <t t-set="placeholder_count" t-value="'charity_count'"/>
    <t t-set="config_card" t-value="True"/>   <!-- ← this -->
</t>
```

**Keep `placeholder_count`.** The two are not alternatives: `config_card`
makes the card render, `placeholder_count` still drives the count badge for
members who do have submissions, via the `force_show` path. Dropping
`placeholder_count` would fix visibility but lose the count.

Layout is unaffected — `config_card` is not part of the column-class
expression, so the card keeps its `col-md-6` width and position.

## Nothing else is needed

I checked the destination before suggesting a bigger change. `/my/charity`
already handles a first-time visitor well: the list template carries a
**"+ New Entry"** button and an empty state that says *"Click '+ New Entry'
above to log your first one."* So landing there with nothing to show is not a
dead end, and pointing the tile straight at `/my/charity/new` instead would
actually be worse — a member with history wants their list first.

## Worth a thought while you are in there

The tile text reads *"Charity Entries — My Data Collection Survey
submissions"*. Danny calls this "the volunteer hour submission form", and a
member looking for somewhere to log hours may not connect those words to what
they want. Something closer to *"Volunteer Hours — Log hours for a programme
you helped with"* would match what they are looking for. Your call — you know
how the lodge talks about these.

---

Happy to make the change myself if you would rather not take it; it is one
attribute in one template. Flagging it to you first because that module is
yours.
