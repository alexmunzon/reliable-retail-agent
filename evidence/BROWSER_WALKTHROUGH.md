# Browser verification — 2026-10-14

Actual Chrome walkthrough of the polished local app, with no model calls.

All eight scripted scenarios reached expected outcomes; see `browser-scenarios.json`. Normal and airline scenarios dispatched once in both lanes. Missing, changed, stale, rejection, and ambiguous scenarios dispatched once only in the illustrative baseline. Uncertain outcome recorded one attempt and stopped without a retry.

A double click on fresh confirmation produced exactly one gated dispatch. Reset restored dispatch counts to zero and disabled response controls until the four script steps completed. The changed-payload prompt visibly contained apartment 4B, its fresh prompt ID and new SHA-256.

Tested widths: 1440, 1024, and 390 pixels. Document scroll width matched viewport width. At the 1024px breakpoint, the gated anchor started at 184.87px, below the sticky toolbar ending at 180.30px. On mobile, the sticky jump links and expanded current-payload prompt remained readable. Desktop and mobile screenshots are actual browser captures.

No app console warnings/errors were observed; unrelated browser-extension warnings were excluded. Research-page navigation, reload, back, and forward passed against the publication copy. Reload restored the active 4/4-step session and its dispatch state; Reset is the explicit fresh-session control. Screenshots and scenario records contain only synthetic fixtures.
