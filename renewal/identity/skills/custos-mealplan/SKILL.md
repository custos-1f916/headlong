---
name: custos-mealplan
description: Plan the family's weekly dinners from Dani's digitized cookbooks, use this week's Whole Foods sales, talk it through with Hal and Dani on Signal, and build Friday's pickup order through the kitchen service.
---

# Weekly meal planning (Hal and Dani, Saturday Whole Foods pickup)

Hal set this up on 2026-09-09 as a standing job. The kitchen service on LXC 128
(`192.168.86.63`) holds the recipe catalog, the sales snapshot, the plans and the
grocery cart driver; `mealplan` is its CLI (`mealplan --help` for every shape). You
never hold Amazon credentials; the cart driver does, on the kitchen box.

## The week

- **Monday wake** (`schedule: mealplan-monday`): read `mealplan brief` — it carries the
  family's **constraints** (dinners per week, weeknight minutes, servings, leftovers, no-repeat
  window, sale picks, budget cap), their **rules** (e.g. pregnancy-safe food), the **questions**
  they want asked, candidates from the cookbooks scored by sale matches/ratings/recency, and the
  sales as a hint (the sale week changes Wednesday). Pick candidates that satisfy the constraints
  and what you know about Hal and Dani (person notes), then send ONE message to the Kitchen group
  if it exists in `signal-contacts`, else one each to Hal and Dani: the candidates in a few words
  each, then the questions from `mealplan prefs`, nothing else. Record answers: tastes into person
  notes, standing rules and constraint changes into `mealplan learn rules …` /
  `mealplan constraint KEY VALUE`, and the draft with `mealplan plan set`.
- **The knobs are theirs.** Dani and Hal want to iterate on what you ask and how you optimize
  (2026-09-09). When either of them says "ask us X on Mondays" or "never plan more than N
  new recipes a week", change the questions/rules/constraints with `mealplan learn` /
  `mealplan constraint` and confirm in one line. Don't hard-code preferences in your own
  memory that belong in those lists; the lists are what the brief shows you every week.
- **Wednesday** (`schedule: mealplan-wednesday`, no message): `mealplan sales --refresh`.
  If a sale changes a pick for the better, swap it in the draft; do not message unless a
  swap needs a decision.
- **Thursday wake** (`schedule: mealplan-thursday`): send the draft plan (day → dish,
  one line each) and the shopping list summary (`mealplan list preview`), ask for last
  changes. One message per person.
- **Friday wake** (`schedule: mealplan-friday`): `mealplan plan finalize` (writes the
  meal plan into the catalog), `mealplan list build` (ingredients → grocery items,
  pantry staples skipped, quantities rounded to packages), then `mealplan cart fill`.
  Read the result: items placed, items it could not match, subtotal. Then message Hal
  and Dani: the plan, the list, the subtotal, and what could not be matched — and how
  the order gets placed this week (see Money). Keep the goal open until the order
  receipt exists (`mealplan order status`).
- Record what you learn (a dish they loved, a brand they prefer, "never again") in
  person notes and with `mealplan learn`, not in new goals.

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
  the Monday brief and your own recall.

Never copy recipes into memory — the catalog holds them. Memory is for judgment: what fits, what
they said, what worked. If the catalog holds fewer than about 20 recipes on a Monday, one line
to Hal ("catalog is thin, N recipes; I'll start planning once the books are in") and stop.

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
