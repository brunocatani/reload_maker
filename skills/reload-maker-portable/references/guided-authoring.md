# Guided authoring

For authoring a weapon without planning it. The tools decide the steps and do the geometry. You make only two kinds of choice: a number on a picture, or nothing. Do not work anything out yourself; do not type coordinates.

Without an MCP client, every tool runs from a shell: `python mcp/server.py --call <tool> --arguments '<json>'`. A reply that has a picture names its file in `sheet`; open that file to look at it.

## The loop

1. Open the weapon you were given (`select_testbed`, or `source_browser` for a plugin and weapon).
2. Call `next_step`.
3. Do exactly the one call in its `do` (the tool and the arguments, unchanged).
4. If `then` names a second call, look at the picture, pick a number with the rules below, and make that call:
   - `choose_magazine_fit` with `{"hand": "<the same hand>", "number": <row>}`
   - `choose_grab_point` with `{"number": <dot>}`
5. Go back to 2.

`next_step` decides everything else, including calls this page does not describe (a preset, sounds, magazine models): run them as given. It also decides which hand needs a fit. A hand you did not fit takes the mirror of the other hand's fit, and `next_step` asks for it only if that mirror does not hold; do not fit a hand it did not ask for.

Stop the loop in two cases only:

- A reply has `stop`: do nothing more on the weapon and report its `reason` word for word.
- `next_step` returns `finish_weapon`: call it. `finished: true` ends the weapon. `finished: false` without `stop` means more steps are needed: go back to 2.

`finished: true` means saved and accepted by the export check. It does not mean the weapon works in the game; never say it does.

Never call `save_project`, `configure_magazine`, `set_part_grip` or any other authoring tool on your own. If a call fails with an error, call `next_step` again once; if the same error returns, report it and stop.

## Choosing a hand fit (`solve_magazine_fit` picture)

The picture has one row per candidate and three views per row. The row number starts each panel label. A dot marks each finger end: T thumb, I index, M middle, R ring, L little.

- Green dot: that finger is on the magazine.
- Yellow dot: there is a gap.
- Red dot: it is sunk into the magazine.

A good hold has the thumb and at least two other fingers green. The ring and little finger are often yellow on a small magazine; that is normal.

Pick the row like this, in order:

1. Throw out every row whose label does not say `holds`.
2. Throw out every row with a red dot.
3. Throw out every row where the hand is closed on the end that goes into the weapon. That end is open and shows a cartridge (brass or a coloured tip), or on an energy cell its contacts. The hand belongs on the other end, the closed base.
4. Throw out every row with a finger pointing straight out into the air, or with the wrist passing through the magazine.
5. Throw out every row where the hand only pinches the magazine between its fingertips, if another row wraps the fingers around the body.
6. Of the rows left, take the one with the most green dots.
7. If rows tie, take the lowest number.
8. If no row is left, take row 1.

The rows are numbered best first by the tool's own measurement (`score` in the reply's `candidates`), so the lowest number left after rules 1 to 5 is the tool's preference too. The picture decides what is thrown out; the number decides ties. When the picture is unclear, choose row 1.

## Correcting a saved fit (`nudge_magazine_fit`)

Use it only when `show_magazine_fit` or `next_step` shows a red dot, or fewer than three green dots, on a saved fit, and at most three times per hand.

| What the picture shows | Call |
| --- | --- |
| Yellow dots on the thumb, index or middle finger | `move: closer`, `amount: small` |
| Red dots, hand pressed into the magazine | `move: away`, `amount: small` |
| Hand sits over the cartridges | `move: down`, `amount: medium` |
| Hand hangs off the bottom end | `move: up`, `amount: medium` |
| Fingers reach around the wrong edge | `move: twist left` or `twist right`, `amount: small` |

The reply says `kept: true` or `kept: false`. A move that made the contact worse is undone for you. After three nudges, call `next_step` whatever the result.

## Choosing a grab point (`show_grab_points` picture)

Numbered dots sit on the part a hand works: a charging handle, a bolt handle or a slide. Three panels are close views; the fourth shows the whole weapon.

1. Pick the dot on the handle, knob or serrated end a person would take hold of.
2. Do not pick a dot on the receiver body, the barrel, the stock or the magazine.
3. If several dots are on the handle, pick the lowest number.
4. If you cannot tell, pick 1.

## Naming the part the hand works (`set_operating_part`)

Only when you were told which parts to name, or `next_step` asks for it. One call sets the charging handle, what moves with it, the bolt and the magazine, from the reload clip:

`set_operating_part` with `{"part": "<handle>", "moves_with": ["<part>", ...], "bolt": "<bolt>", "magazine": "<magazine>", "unfolded_seconds": <s>, "pulled_seconds": <s>}`

- `pulled_seconds` is the moment of the reload clip where the handle is farthest back. The tool finds the moment it is home again by itself.
- `unfolded_seconds` is only for a handle that swings or folds out before it is pulled: the moment it is fully out and not yet pulled. Leave it out for a handle that does not fold.
- `bolt` and `magazine` are needed only when the reply says Maker has not found them.

The reply's picture has four rows: the weapon at rest, then a hand holding the part at no pull, half pull and full pull. Continue only if the part is in the same place in rows 1 and 2 or folded out in row 2, and moves toward the stock in rows 3 and 4. Otherwise stop and report.

A folding handle is drawn folded in row 1. In the game it unfolds as a hand comes near it, so the hand takes it already folded out.

## What to report at the end

- The weapon's name.
- The reply of `finish_weapon`, or the `stop` reason, unchanged.
- The row or dot numbers you chose.
