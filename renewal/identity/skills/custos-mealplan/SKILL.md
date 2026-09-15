---
name: custos-mealplan
description: Plan the family's weekly dinners from Dani's digitized cookbooks, use this week's Whole Foods sales, talk it through with Hal and Dani on Signal, and build Friday's pickup order through the kitchen service.
---

# Weekly meal planning (Hal and Dani, Saturday Whole Foods pickup)

Hal set this up on 2026-09-09 as a standing job. The kitchen service on LXC 128
(`192.168.86.63`) holds the recipe catalog, the sales snapshot, the plans and the
grocery cart driver; `mealplan` is its CLI (`mealplan --help` for every shape). You
never hold Amazon credentials; the cart driver does, on the kitchen box. **The Whole Foods
account is Dani's** (since 2026-09-10 evening: her substitutions are set up the way she likes
and her order history is the household's real record); either Hal or Dani signs in and either
places the order.

## The first week, and the one rule it taught (2026-09-10)

The first round went well: a full day-by-day draft with links, pregnancy-safe, Dani's "no
chicken" and "cabbage isn't a staple" handled in one reply, the cart built and reported the
same evening. Hal: "Overall, Custos did well!" Then the miss: at 00:02Z Dani wrote "I already
have coconut milk and gnocchi" and asked for a Thursday work-from-home dinner. The reply said
"coconut milk and gnocchi come off the list" and "Thursday locked in with the bake" — and
fifteen minutes later the cart was filled with two packs of gnocchi and a can of coconut milk,
and Thursday had nothing in the plan or the cart. The reply was written by the responder, which
has no tools, as a plain answer; the mind's run had started two minutes earlier and never
re-read the conversation before it touched the cart.

**The rule: a change to the list or the plan exists only once a `mealplan` command has
recorded it.** Saying it in Signal is not doing it.

- In a reply with no tools (the responder): never say "off the list", "locked in", "I'll factor
  it out". Say what you *will* do and **defer** it as a goal ("I'll take gnocchi and coconut milk
  off the order and pencil Thursday in — back shortly"). The harness now trips on those phrases.
- In a wake with tools (the mind): before **anything** touches the cart, re-read the group
  conversation since the draft went out (`chat history`), and `mealplan plan show`. Every
  "we already have X" → `mealplan list have "X" --note "Dani, Thu"`. Every agreed dinner change
  → `mealplan plan set --dinner DATE=SLUG …` (all dinners, dates from the pickup Saturday).
  Every "we don't need X" / "we always have X" → `mealplan learn pantry "X"`. Only then
  `cart candidates` / `choose` / `fill`. `mealplan cart fill` and `mealplan plan proposed` refuse
  on their own when Collette Haus messages arrived after the plan was last touched: they print
  the unread lines, you record what they ask, then retry with `--read`. A cart built from a stale
  read is worse than no cart.
- **Verification is read-only.** On 2026-09-11 02:01Z a "does the new CLI surface exist?" loop ran
  `mealplan cart logout` and wiped Dani's session minutes after she signed in. To check a command
  exists, read `mealplan --help`; to check state, use `status`, `show`, `prefs`, `history`, `cart
  diff`. Never run `cart logout`, `cart fill`, `plan finalize`, `plan proposed`, `list have` or
  `product` as a test. Account switches are Hal's, from the Mac (`kitchen-logout`); your token
  cannot do it.
- Only Hal or Dani add things to the list. A friend's or another agent's conversation (Kim's
  kimjang cabbage on 2026-09-10) is never an extra on their order.

## Asks between wakes (Hal, 2026-09-12: "when asked to add a single item ... simply add it to the cart now")

Dani and Hal text asks whenever they think of them — "add a jar of Justin's honey peanut butter for next
week", "add Annie's mac and cheese cups, it's usually a 4 pack", a NYT recipe link with "add this to next
week's menu". On 2026-09-12 four of these were deferred correctly and then sat all day because every goal
said "during next week's planning workflow". **They are due now, in the wake that picks them up** (the
responder tags them QUICK; the social thinker and the mind both take them), and each is one or two commands:

- **A grocery item** → `mealplan cart add "Justin's honey peanut butter" --qty 1 --note "Dani 9/12"` (add the
  ASIN from `mealplan search` when the brand matters: `mealplan cart add "Soom tahini" B0XXXXXXX --note "…"`).
  It records the item as an extra on the planning week's list **and** puts it in the Whole Foods cart on
  Dani's account in the same call; it prints the product and price. Confirm with exactly that: "In the cart:
  Justin's Honey PB 16 oz, $5.09 (Dani's account) — it'll ride along with Saturday's order." If it prints
  RECORDED, NOT in the cart (no session), say it is on the list and Friday's fill adds it. Friday's `cart
  fill` skips lines already in the cart and sets a product they added by hand to the planned count, so an
  early add never doubles.
- **A recipe link** → `mealplan recipe import URL` (a page already in the catalog — Dani's whole NYT box is —
  comes back as "already in the catalog" with its slug; a new one is scraped by Mealie and tagged
  `Requested`), then `mealplan plan request SLUG --note "Dani 9/12: next week's menu"`. The draft on
  Wednesday **must** include every requested dinner (`mealplan plan show` lists them) or say why not.
  Confirm with the Mealie link.
- **A dinner by name** ("let's do the corn pasta next week") → `mealplan recipes --q "corn pasta"` then
  `plan request SLUG`. **A staple or pantry change** → `mealplan learn staples "6 bananas"` /
  `learn pantry "…"` / `product "item" ASIN`. **"I already have X"** → `mealplan list have "X"`.
- Then, always: one line back in the same conversation (`chat reply --follow-up --reply-to TRIGGER SENDER`)
  with what the command printed, and `custos-memory complete GOAL_ID "<that output>"`. The command output
  is the evidence; the reply is not.
- Only Hal's and Dani's asks count (Collette Haus, or their DMs). A friend's idea is a suggestion to raise
  with them, never an item on their order.
- What is **not** quick: anything that means building, coding, research or reading a repo (a baby-name
  feature, an audit, a redesign). Those stay with the mind as ordinary deferred work.

## Looking up this week's and historical meals

Questions such as “what's for dinner?”, “what's the meal tomorrow?”, “what did we
make last Tuesday?”, or “show me this week's meals” are read-only lookups, not
planning work. Run `mealplan meals today`, `mealplan meals tomorrow`,
`mealplan meals YYYY-MM-DD`, or `mealplan meals --week YYYY-Www`. With no selector,
`mealplan meals` shows the current Mountain-time ISO week; it deliberately does not
use the next-pickup planning week. Each recorded dinner includes its canonical
`https://recipes.ha1.io/g/home/r/SLUG` link. Reply with the title and that link.
If the command says no meal is recorded for the date, say exactly that; do not
substitute a draft from another week or invent a link. `--json` is available when
you need the exact dates/status. Historical queries read the retained weekly plan
files through the kitchen API and do not modify the plan, catalog, or cart.

## The week (Hal, 2026-09-10: "We can always decline, that's a valid way to stop the week's meal plan")

The planning week is the week of the **next Saturday pickup** (`mealplan plan show` defaults to it;
the brief's `planning` line shows its status, whether the first message went out, whether anyone
has replied, and how many dinners are drafted). Three scheduled wakes, all 17:00 Mountain:

- **Wednesday 17:00 — the first text is a full draft plan** (`schedule: mealplan-wednesday`; the
  Whole Foods sale week starts Wednesday, so the flyer is fresh and still valid at Saturday's pickup).
  Hal, 2026-09-10: a proposal of recipes for each day "is a helpful starting place". Read `mealplan
  brief` — it carries the family's **constraints** (dinners per week, weeknight minutes, servings,
  leftovers, no-repeat window, sale picks, budget cap), their **rules** (e.g. pregnancy-safe food),
  the **questions** they want asked, the **staples with counts**, candidates from the cookbooks scored
  by sale matches/ratings/recency/past orders, each with its recipe **link**, and the sales as a hint.
  Build the draft first: one dinner for each cook night from the pickup Saturday through Friday,
  honouring the constraints and what you know about Hal and Dani (person notes), and write it down with
  `mealplan plan set --dinner DATE=SLUG …` (as many `--dinner` as cook nights). Then send ONE message
  to the `Collette Haus` group (Hal, Dani and you; label from `signal-contacts`), else one each to
  Hal and Dani, in this order and nothing else:
  1. the plan, **every day Saturday → Friday on its own line**: `Sat 9/13: Title — <link>`; a
     leftover night or a night they said they're out reads `Tue 9/16: leftovers` / `out`;
  2. two or three alternates as `Title — <link>` in case a day misses;
  3. the staples line with counts (`Staples: 4 apples, 5 bananas, 2 milk; extras: —`);
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
  they change their mind. A day they ask for ("something laid back for Thursday, I'm home") gets a
  recipe *and* a `plan set` in the same wake, or a deferred goal — never just a sentence.
- **Thursday 17:00** (`schedule: mealplan-thursday`): `mealplan plan show`. If the week is
  skipped: nothing. If no first message went out yet (no `proposed`): build and send the full draft plan
  exactly as on Wednesday. If it went out but nobody replied (no `engaged`): one short reminder in the group
  with the draft as day-by-day links. If they have engaged: nothing, unless a decision is still
  open (then ask that one question).
- **Friday 17:00 — cart setup** (`schedule: mealplan-friday`). Skipped week: nothing. No reply all
  week: treat it as declined — one line in the group ("no plan this week; say the word if you want
  one") and stop. Otherwise, first the re-read above. Then:
  - **If `plan show` says the cart was already filled this week** (it happens when the plan locks
    early, as on 2026-09-10): do **not** refill — `mealplan cart fill` refuses anyway. Run
    `mealplan cart diff`: it lists what they removed, added and re-counted after your fill. That is
    the learning loop: a removed spice → `learn pantry`; a removed ingredient → ask "pantry, or
    skip?"; an added item → a staple (`learn staples "4 apples"`) or a one-off; a count change → the
    real count. Ask about anything you cannot classify, in one message, then `mealplan plan
    finalize` and one short confirmation.
  - Otherwise `mealplan plan finalize` (writes the meal plan into the catalog), `mealplan list
    build` (ingredients → grocery items, pantry and dried spices skipped, staples with counts and
    pinned products already chosen, "already have" lines kept but never bought), then the cart
    steps below. Message the group: the plan **day by day, each day as `Weekday: Title — <link>`**,
    the list with counts, what they already have, what could not be matched, the subtotal, and how
    the order gets placed (Hal or Dani, from the Amazon app on Dani's account, picking the Saturday
    window). Keep the goal open until the order is placed.
- **Staples and extras** (Hal, 2026-09-10: "We always need milk, etc."; that evening: "4 apples and
  5 bananas as staples each week… 2 containers of milk"). Two lists, opposite meanings: **pantry** =
  assumed on hand, skipped when a recipe calls for it (dried spices, seeds and cooking oils are
  pantry by rule — fresh herbs are not); **staples** = bought every week no matter what is cooked,
  **with a count**: `mealplan learn staples "4 apples"`. "Make it 6 bananas" → remove the old line,
  add the new. `mealplan product "apples" ASIN` pins the exact product they want (Organic Honeycrisp,
  Organic Valley whole milk 64 oz…); pinned products are chosen for you every week. A one-off —
  "grab butter this week", "2 lemons" — → `mealplan list add "2 lemons" --note "Dani asked Thu"`;
  it stays on that week's list through rebuilds. "I already have X" → `mealplan list have "X"`.
  Show the staples with counts in the Wednesday message (so they can correct it in one reply) and in
  Friday's cart message. Never drop a staple because a recipe happens to use it — the list merges them.
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

## The kitchen repo — you own the service (Hal, 2026-09-11: "Like baby name")

`collettiquette/kitchen` (private; you have push) is the kitchen service on LXC 128: `bin/kitchen_api.py`
(everything `mealplan` talks to), the digitizer and importers, the systemd units, the Mealie compose
file, `tests/`, `deploy/`. **Push to main is the deploy**: LXC 128 checks `origin/main` every 2
minutes, runs `tests/run.sh`, installs the units, restarts the service, health-checks it and rolls
back on failure. Read the outcome at `http://192.168.86.63:8090/deploy.json` (`deployed`, `rejected`
= tests failed and the old commit kept running, `rolled-back`, `broken` = needs Hal) — it also
arrives as a `Deploy …` line in the kitchen feed on your next feed wake. Proven 2026-09-11 with a
syntax error (rejected) and a startup crash (rolled back).

- Clone under `/opt/custos/work/repos/collettiquette/kitchen`. Before every push: `bash tests/run.sh`
  (offline, ~20 s) and add a test for what you changed. Small commits, one concern each, with the
  reason in the message. To undo a deploy, `git revert` and push; never force-push main.
- The state on the box is not yours to edit from git: `kitchen.env`, `plans/`, `prefs.json`,
  `reviews.json`, `events.jsonl`, scans, `work/`, Mealie's data — `.gitignore` keeps them out, and
  the deploy never touches `/srv/kitchen`. Secrets never go into a commit.
- **Not in this repo, on purpose:** the Whole Foods cart driver and the family's signed-in session.
  They run on a separate box you cannot reach; `kitchen_api.py` calls it through `shopper_call()`
  and your `mealplan cart …` commands are unchanged. Do not add a cart driver, a session store or
  any checkout-shaped route here — `tests/test_kitchen.py::HardLines` fails the deploy if one appears.
- A Mealie change (`mealie/docker-compose.yml`) restarts Mealie on deploy; that is the one file to
  change deliberately and rarely. Container creation, Proxmox and the firewall stay Hal's.

## Dani's order history (`mealplan history`)

Her account carries months of real Whole Foods orders. `mealplan history` lists them (date, total,
items); `mealplan history --products` lists what recurs across orders, most frequent first — that
is the household's true staple list and their preferred products, and `cart candidates` already
ranks products they have ordered before (`times_ordered`). Use it in three ways: once, after the
first sync, read it and propose staples/pinned products to them in one message ("you've ordered X
in 9 of the last 12 orders — make it a staple?"), and set what they confirm; every week, when a
line has candidates, prefer the one they have ordered; and to sanity-check the budget against
what a normal week actually costs them. `mealplan history --sync` pulls it fresh (needs the
signed-in session); do that when the week's order has been picked up.

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
stop. (Since 2026-09-10 the catalog holds the Molly Baz Website cookbook and Dani's NYT Cooking
box, so this no longer applies unless something is wrong.)

## The Whole Foods cart (Tier 1: you fill it, Hal or Dani places the order)

`mealplan cart …` drives the Whole Foods site's own cart through the kitchen service with an
honest `Agent/kitchen` identity. Product search works without a login; putting things in the
cart needs the signed-in session (Dani's account), which lives on the kitchen box, never with you.

1. `mealplan cart status` (it names whose session it is). If NOT signed in: `mealplan cart
   refresh` (a silent renewal from the saved browser profile). If still not: `mealplan cart login`
   prints a one-time link; send it to **Hal or Dani only** (operators — never to the group, never
   to a friend, never in the square): "Whole Foods session expired — sign in here from your phone
   on the home Wi-Fi: <link> (15 min)". Then wait for `status` to say signed in; do the rest of
   the wake without the cart if it doesn't come. `mealplan cart logout` wipes the session and the
   browser profile — only for switching accounts, and only when Hal or Dani asks.
2. `mealplan list build` → `mealplan cart candidates`: each grocery line gets up to three
   products (brand, size, bought-before flag, `times_ordered` from the history); pinned products
   and "have" lines are already settled. Pick with `mealplan cart choose "line" ASIN [--qty N]`:
   prefer what they have ordered before, then the pinned/preferred brands in `mealplan prefs`,
   then 365; match the size to the recipe's need (2 recipes × 1 cup stock = one 32 oz carton);
   a staple's count is the default quantity. No candidate fits → leave the line unchosen and say so.
3. `mealplan cart fill` adds the chosen lines; read back added / failed / skipped and
   `mealplan cart show`. A "challenge" failure means Amazon asked whether we are a robot: stop,
   report it to Hal, do not retry. A "cart was already filled" refusal means go to `cart diff`.
4. Friday message: the plan, the list, what is in the cart, what they already have, what could
   not be matched, and "the cart is ready in the Amazon app — pick the Saturday window and place it".

## Money — hard line

Placing an order spends real money. `mealplan cart fill` never checks out. Checkout
runs only through `mealplan order place`, which the kitchen service refuses unless the
subtotal is under Hal's cap AND Hal's fresh `GO` exists on the host record (Tier 2), or
is disabled entirely (Tier 1: Hal or Dani places the order from the Amazon app; your message
says "the cart is ready in the app"). Never work around a refusal, never ask a friend to
approve instead of Hal or Dani, never split an order to fit under the cap. A refused
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
