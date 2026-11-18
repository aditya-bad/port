# Café notes (material for the sample explainer)

How a drink goes from the counter to the customer, at a small café with one barista.

## The order
- The customer orders at the counter. The cashier types it into the till.
- The till sends a ticket to the kitchen screen. Example queue when you order a
  flat white: latte, tea, mocha are already waiting, so yours is fourth.
- Tickets are made in the order they arrived (first in, first out).

## Making one drink (typical times, not measured)
| Part | About | Note |
|---|---|---|
| Grind and press the coffee | 10 s | |
| Espresso runs | 30 s | the milk is steamed at the same time |
| Steam the milk | 20 s | during the espresso |
| Pour | 15 s | |
| Total | about 55 s, "about a minute" | done one after the other it would be about 20 s longer |

## Hand-off
- The barista calls the drink and the name ("Flat white for Sam!").
- The customer picks it up at the end of the counter.

## The point
Lots of systems that serve many people (a printer, a web server, a support
desk) work like this: requests wait in a queue and are served in turn.
