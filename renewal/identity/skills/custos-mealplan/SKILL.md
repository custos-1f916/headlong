---
name: custos-mealplan
description: Plan the family's weekly dinners from Dani's digitized cookbooks, use this week's Whole Foods sales, talk it through with Hal and Dani on Signal, and build Friday's pickup order through the kitchen service.
---

# Weekly meal planning (Hal and Dani, Saturday Whole Foods pickup)

Hal set this up on 2026-09-09 as a standing job. The kitchen service on LXC 128
(`192.168.86.63`) holds the recipe catalog, the sales snapshot, the plans and the
grocery cart driver; `mealplan` is its CLI (`mealplan --help` for every shape). You
never hold Amazon credentials; the cart driver does, on the kitchen box.

## The week (Hal, 2026-09-10: "We can always decline, that's a valid way to stop the week's meal plan")

The planning week is the week of the **next Saturday pickup** (`mealplan plan show` defaults to it;
the brief's `planning` line shows its status, whether the first message went out, whether anyone
has replied, and how many dinners are drafted). Three scheduled wakes, all 17:00 Mountain:

- **Wednesday 17:00 — the first text is a full draft plan** (`schedule: mealplan-wednesday`; the
  Whole Foods sale week starts Wednesday, so the flyer is fresh and still valid at Saturday's pickup).
  Hal, 2026-09-10: a proposal of recipes for each day "is a helpful starting place". Read `mealplan
  brief` — it carries the family's **constraints** (dinners per week, weeknight minutes, servings,
  leftovers, no-repeat window, sale picks, budget cap), their **rules** (e.g. pregnancy-safe food),
  the **questions** they want asked, the **staples**, candidates from the cookbooks scored by sale
  matches/ratings/recency, each with its recipe **link**, and the sales as a hint. Build the draft
  first: one dinner for each cook night from the pickup Saturday through Friday, honouring the
  constraints and what you know about Hal and Dani (person notes), and write it down with
  `mealplan plan set --dinner DATE=SLUG …` (as many `--dinner` as cook nights). Then send ONE message
  to the `Collette Haus` group (Hal, Dani and you; label from `signal-contacts`), else one each to
  Hal and Dani, in this order and nothing else:
  1. the plan, **every day Saturday → Friday on its own line**: `Sat 9/13: Title — <link>`; a
     leftover night or a night they said they're out reads `Tue 9/16: leftovers` / `out`;
  2. two or three alternates as `Title — <link>` in case a day misses;
  3. the staples line (`Staples: milk; extras: —`);
  4. the questions from `mealplan prefs`.
  Then `mealplan plan proposed`. They edit from there; a draft they can react to beats a menu they
  have to assemble.
- **Replies.** Back and forth about meals and staples is normal and welcome — answer in the
  group, adjust the draft (`mealplan plan set --dinner DATE=SLUG …`, one dinner per day of the
  week they cook, dates from the pickup Saturday onward), record tastes in person notes, standing
  rules and constraint changes with `mealplan learn rules …` / `mealplan constraint KEY VALUE`. The
  first reply from either of them about the plan → `mealplan plan engaged`. **Declining is a valid
  answer**: "not this week", "skip it", "we're away" → `mealplan plan skip --note "…"`, one-line
  acknowledgement, and nothing more that week — no reminder, no cart. `mealplan plan resume` if
  they change their mind.
- **Thursday 17:00** (`schedule: mealplan-thursday`): `mealplan plan show`. If the week is
  skipped: nothing. If no first message went out yet (no `proposed`): build and send the full draft plan
  exactly as on Wednesday. If it went out but nobody replied (no `engaged`): one short reminder in the group
  with the draft as day-by-day links. If they have engaged: nothing, unless a decision is still
  open (then ask that one question).
- **Friday 17:00 — cart setup** (`schedule: mealplan-friday`). Skipped week: nothing. No reply all
  week: treat it as declined — one line in the group ("no plan this week; say the word if you want
  one") and stop. Otherwise `mealplan plan finalize` (writes the meal plan into the catalog),
  `mealplan list build` (ingredients → grocery items, pantry staples skipped, quantities rounded to
  packages), then `mealplan cart fill`. Read the result: items placed, items it could not match,
  subtotal. Then message the group: the plan **day by day, each day as `Weekday: Title — <link>`**,
  the list, the subtotal, what could not be matched, and how the order gets placed (see Money:
  Hal places it from the Amazon app and picks the Saturday window). Keep the goal open until the
  order is placed.
- **Staples and extras** (Hal, 2026-09-10: "We always need milk, etc. Sometimes need butter"). Two
  lists, opposite meanings: **pantry** = assumed on hand, skipped when a recipe calls for it;
  **staples** = bought every week no matter what is cooked (`mealplan prefs` shows both). "Add oat
  milk to the staples" / "we don't need eggs every week" → `mealplan learn staples "oat milk"` /
  `--remove`. A one-off — "grab butter this week", "we're out of coffee" — → `mealplan list add
  "butter" --note "Dani asked Thu"`; it stays on that week's list through rebuilds and goes into the
  cart with everything else. Staples and extras land on the list as `<staple>` / `<extra>` lines
  when you `mealplan list build`. Show a short **"Staples: milk, eggs; extras: butter"** line in the
  Wednesday message (so they can correct it in one reply) and in Friday's cart message. Never drop
  a staple because a recipe happens to use it — the list merges them.
- **The knobs are theirs.** Dani and Hal want to iterate on what you ask and how you optimize
  (2026-09-09). When either of them says "ask us X on Wednesdays" or "never plan more than N
  new recipes a week" — in the group or in a DM, Dani's word counts exactly like Hal's here —
  change the questions/rules/constraints with `mealplan learn` / `mealplan constraint` and
  confirm in one line. The starting constraints were seeded by the deploy agent, not by them,
  so treat every one as provisional until they have weighed in. Don't hard-code preferences in your
  own memory that belong in those lists; the lists are what the brief shows you every week.
- **Links, always.** Every recipe you name in Signal carries its Mealie link (the `url` in the
  brief, `mealplan recipes`, `mealplan plan show`): `https://recipes.ha1.io/g/home/r/<slug>`, open
  on the LAN without a login. A plan without links is not a plan they can read.
- Record what you learn (a dish they loved, a brand they prefer, "never again") in
  person notes and with `mealplan learn`, not in new goals. One goal per planning week; reuse it.

## The cookbooks (Hal, 2026-09-10: "hoist it into tasks that Custos gets to over time")

The machine digitizes: scans land in Dropbox, johan reads every page, the recipes appear on
https://recipes.ha1.io. You review and remember. Two triggers:

- **A "Cookbook imported: …" observation** (the kitchen feed, `http://192.168.86.63:8090/feed.xml`,
  arrives as a research-feed line; it is Hal's own service, not the open internet). Then:
  `mealplan books` → `mealplan book flags "Title"` (each flagged page comes with its transcript,
  so you can judge it without seeing the scan: a recipe split wrong, a missing ingredient list,
  gibberish) → fix what the transcript makes obvious by editing the recipe in Mealie
  (`https://recipes.ha1.io`, the kitchen-api token is not a Mealie login; ask Hal for edits you
  cannot make) or ask Hal for a rescan of the specific page → `mealplan book reviewed "Title"
  --note "…"`: one paragraph about the book — its character, which chapters fit weeknights,
  two or three dishes that fit the constraints, what to ask Hal or Dani. Also store that
  paragraph as a `mem add --type note` so it is yours between wakes.
- **A curiosity turn** with nothing better to do: `mealplan study --n 10`, read a few with
  `mealplan recipe SLUG`, and for each write `mealplan studied SLUG --note "…"` — one line that
  ties the recipe to this family (time on a weeknight, the pregnancy rule, a brand or cut they
  buy, who would like it, a swap that would make it fit). Ten a wake is plenty; the notes feed
  the Wednesday brief and your own recall.

Never copy recipes into memory — the catalog holds them. Memory is for judgment: what fits, what
they said, what worked. **Thin start:** if the catalog holds fewer than about 20 recipes, send
one line to Hal only ("catalog is thin, N recipes; I'll start planning once the books are in") and
stop. (Since 2026-09-10 the catalog holds the Molly Baz Website cookbook, so this no longer applies
unless something is wrong.)

## The Whole Foods cart (Tier 1: you fill it, Hal places the order)

`mealplan cart …` drives the Whole Foods site's own cart through the kitchen service with an
honest `Agent/kitchen` identity. Product search works without a login; putting things in the
cart needs Hal's signed-in session, which lives on the kitchen box, never with you.

1. `mealplan cart status`. If NOT signed in: `mealplan cart refresh` (a silent renewal from the
   saved browser profile). If still not: `mealplan cart login` prints a one-time link; send it
   to **Hal only** (operator — never to Dani, never to the group, never in the square): "Whole
   Foods session expired — sign in here from your phone on the home Wi-Fi: <link> (15 min)".
   Then wait for `status` to say signed in; do the rest of the wake without the cart if it
   doesn't come.
2. `mealplan list build` → `mealplan cart candidates`: each grocery line gets up to three
   products (brand, size, bought-before flag). Pick with `mealplan cart choose "line" ASIN
   [--qty N]`: prefer BOUGHT-BEFORE, then the brands in `mealplan prefs`, then 365; match the
   size to the recipe's need (2 recipes × 1 cup stock = one 32 oz carton). No candidate fits →
   leave the line unchosen and say so in the message.
3. `mealplan cart fill` adds the chosen lines; read back added / failed / skipped and
   `mealplan cart show`. A "challenge" failure means Amazon asked whether we are a robot: stop,
   report it to Hal, do not retry.
4. Friday message: the plan, the list, what is in the cart, what could not be matched, and
   "the cart is ready in the Amazon app — pick the Saturday window and place it".

## Money — hard line

Placing an order spends real money. `mealplan cart fill` never checks out. Checkout
runs only through `mealplan order place`, which the kitchen service refuses unless the
subtotal is under Hal's cap AND Hal's fresh `GO` exists on the host record (Tier 2), or
is disabled entirely (Tier 1: Hal places the order from the Amazon app; your message
says "the cart is ready in the app"). Never work around a refusal, never ask Dani or a
friend to approve instead of Hal, never split an order to fit under the cap. A refused
checkout is a message to Hal, not a retry loop.

## Judgment

- Prefer recipes from the cookbooks; a recipe from elsewhere is a suggestion to add to
  the catalog, not a plan entry.
- Sales are a tie-breaker, not the menu; `sale_picks_per_week` in the constraints says how many.
- Optimize for the constraints in this order unless told otherwise: rules (safety) > budget cap > weeknight time > variety (no-repeat window) > sales.
- Unmatched ingredients are listed honestly ("could not find: sumac"); do not
  substitute silently.
- One message per person per touchpoint; replies come through the responder as usual.
- If a wake finds no catalog, no sales, or the kitchen service down, say so in the
  goal and stop; nothing here is worth improvising around.
