# Design brief v2: requirements only (you own the UX)

Paste everything below into Claude Design.

---

You are the product designer for this app. That means you own the information architecture, navigation, page structure, interaction design and visual identity. I'm giving you the users, their goals, the data and the actions. I'm deliberately not telling you which pages to make or where things go. Decide that yourself, and briefly explain your reasoning for the structure you chose.

## The product

It's a self-hosted web app for running and monitoring automated options-trading strategies on Indian index derivatives (NIFTY, BANKNIFTY, SENSEX, BANKEX) through the Zerodha Kite broker. It currently paper-trades.

- A **strategy** is a piece of code, and there are about 10 types.
- A **deployment** is one running copy of a strategy with its own capital and settings.
- I run 10 to 25 deployments at once, and many are intentionally near-identical copies with small config differences, to see which variation works best.

## The user

It's me: one trader, plus occasionally one or two trusted people I give logins to. I'm not a designer and I'm not a quant. I know trading terms (P&L, legs, strikes, drawdown, premium) but I don't want to decode ratios myself.

How I use it:

- **During market hours (9:15 to 15:30 IST), mostly on my phone, many times a day.** Quick glances. Is everything fine? What's my P&L? Did anything break or get stuck? Sometimes I need to act fast: pause something, or close its positions.
- **In the evening, on a laptop.** Slower review. Which deployments are doing well or badly, how this week or month went, why a particular trade went wrong, whether to change settings, and starting new deployments.

## What I need to be able to do

Every item below must stay possible and easy to find.

**Monitoring, live:**

- See total P&L today and overall, realized vs unrealized, at a glance.
- Know immediately when something needs me. Examples: a deployment errored, a deployment is paused while still holding open positions, the broker session expired (it expires daily), a losing streak.
- See every open position across all deployments: which deployment owns it, side, quantity, entry price, live price and live P&L. Prices tick every second.
- See the live index prices.
- Get notifications of fills, errors and pauses, both in-app and as phone push.

**Controlling deployments:**

- Pause / resume.
- Close a deployment's open positions while it keeps running.
- Stop it, optionally closing positions first.
- Delete it.
- "Remove current trade": erase the trade from the record entirely. This is irreversible.
- Edit its settings. This is only allowed while it's paused.
- Tag deployments. One special tag, "Strategy Lab", marks experiments that I usually want hidden from my main view.
- Do actions on several deployments at once.
- Use an emergency "close everything everywhere" control. It must be reachable but very hard to trigger by accident.

**Understanding one deployment in depth:**

- What it's holding right now, and the strategy's live internal state (for example: the next adjustment trigger, the profit checkpoint target, days to expiry). That state is sometimes "as of the last checkpoint" rather than live, and that must be clear.
- **Its performance:**
  - win rate, profit factor, average win and loss, max drawdown, return on capital;
  - P&L by month across years;
  - the distribution of trade results;
  - P&L by exit reason;
  - the equity curve with drawdown;
  - a P&L calendar.
- **Replaying a past trade cycle** (entry → adjustments → exit). Show how the underlying index moved alongside the trade's P&L, when each entry, adjustment and exit happened, and, at any moment, each leg's P&L. Use 1-minute data for intraday and coarser for long trades.
- The full history of every position, fill and event. Filterable and exportable.

**Comparing and ranking. This matters a lot to me, and the last redesign lost it:**

- **Leaderboards: who's winning this month, this year, all-time.** Show the top performers per metric (return, drawdown, return/drawdown, win rate, profit factor, and so on) and a ranking of all deployments. Flag deployments that are too new to judge, on a losing streak, or quiet.
- **Compare: pick a few deployments and see them head-to-head.**
  - Overlaid equity curves.
  - A metric-by-metric comparison that says who wins each metric and overall.
  - Plain-English callouts, such as "6.75 return/drawdown, excellent" or "losing streak of 4", so I don't have to interpret numbers myself.

**Reviewing periods:**

- Statement-style reports. Step through day by day, week by week or month by month, with the figures compared to the previous period, broken down by strategy and by deployment. Realized P&L only.
- Whole-account views:
  - combined equity over time;
  - capital deployed vs idle;
  - whether I'm unknowingly stacking exposure to the same index across deployments.

**Starting deployments:**

- Browse the available strategies. Each has a plain description, the instruments it trades, and how many deployments run it.
- **Deploy a new one:**
  1. name it;
  2. choose intraday or positional;
  3. set capital;
  4. fill in its settings (numbers, percentages, times, on/off options, choices, with defaults and help text).
- Copy an existing deployment's settings as a starting point. I do this constantly.

**P&L honesty:**

- One global switch for how P&L is shown everywhere: Gross → After charges → After charges + slippage. I should always be able to tell which one I'm looking at.

**Utilities and admin:**

- Search the broker's instrument list and subscribe or unsubscribe to live prices.
- **Calculators:**
  - a brokerage calculator matching Zerodha's (itemized charges, break-even move);
  - SIP;
  - step-up SIP;
  - lumpsum + step-up;
  - CAGR.
- **Account:**
  - log in, change password, see and end sessions;
  - manage users (admin);
  - manage tags;
  - push notification settings;
  - connect to the broker.

## Hard requirements

1. **Nothing above may become hard to find.** If you combine things, I must still find each one in two clicks or taps. Leaderboards and Compare must be clearly visible destinations; I go to them directly.
2. **Phone-first for monitoring and control, desktop-strong for analysis.** No sideways scrolling on the phone for anything I check during market hours.
3. **Live data** updates every second. Numbers must not make the layout jump.
4. **Green and red mean profit and loss only.** Don't use them for decoration or for buttons.
5. **Risky actions** always say exactly what will happen before I confirm.
6. **Charts** never put two different scales on one y-axis. Stack them instead.
7. **Both themes**: light and dark, designed with equal care.
8. **Buildable** in plain HTML, CSS and vanilla JavaScript with SVG charts. No framework-only components, and fonts must be self-hostable.
9. **Indian rupee formatting** throughout (₹1,23,456 and ₹1.2L).

## The look: do not make it look AI-generated

I rejected the last redesign because it looked like every other AI-made website: generic, soft, interchangeable, and confusing. Specifically, avoid:

- the standard SaaS dashboard template (a sidebar with icons plus a grid of identical rounded white cards with a big number and a tiny label);
- purple or blue gradients, glassmorphism, glowing blobs, and decorative illustrations;
- emoji as icons or section markers;
- Inter or another default "safe" font used without thought;
- everything centered, everything the same size, and every card given equal weight;
- marketing-style hero sections and big friendly headings that waste screen space;
- vague labels ("Insights", "Overview", "Your journey") instead of plain trading words;
- numbered sections like 01 / 02 / 03 when nothing is a sequence.

What I want instead:

- It should feel like a **precise instrument made for this specific job**: a trading desk or a cockpit, not a startup landing page. Think of the clarity of a good terminal (Bloomberg, Zerodha Kite, Sensibull), but more calm and modern.
- **Hierarchy driven by importance.** Problems and money come first and loudest, and routine things stay quiet.
- **Dense but breathable.** Numbers-first, tabular figures and tight alignment, but never cramped.
- **A distinct visual identity.** Make a few deliberate, opinionated choices (typography, color, one signature element) so it couldn't be mistaken for a template. Tell me what those choices are and why they fit trading.

## What I want back

1. **Your proposed structure:** navigation, the list of screens, and where each requirement above lives. Include a short note on why.
2. **A token sheet:** colors (both themes), type, spacing, chart palette.
3. **A component sheet:** tables, status indicators, stat displays, charts, forms, modals, toasts, empty, loading and error states.
4. **Mockups of every screen** at desktop (1440px) and phone (390px) width, in both themes, filled with the realistic data below.

## Sample data

**Deployments:**

| Deployment | Strategy | Status / mode | P&L and legs |
|---|---|---|---|
| Nifty Strangle A | strangle_monthly_v2 | active, positional, ₹10,00,000 capital | today +₹4,820; overall +₹62,340; 4 open legs |
| Nifty Strangle B | strangle_monthly_v2 | paused | still holding 2 legs, needs attention |
| BankNifty DTT Adjusted | intraday_dtt_adjusted | active, intraday | today −₹1,240; losing streak of 3 |
| Sensex Credit Spread | trend_credit_spread | active | — |
| Pivot Supertrend Options | pivot_supertrend_options | stopped | — |
| Bankex Strangle | — | active, tagged Strategy Lab | 4 days old, too new to judge |

**Open legs:**

- NIFTY 26NOV 26000 CE: short 75, entry 120.50, now 98.20, +₹1,672
- NIFTY 26NOV 24000 PE: short 75, entry 115.00, now 131.40, −₹1,230

**Indices:** NIFTY 25,184.30 (+0.42%), BANKNIFTY 55,920.15 (−0.18%), SENSEX 82,410.90 (+0.37%).

**Alert example:** Broker session expired at 08:00. Reconnect needed before market open.
