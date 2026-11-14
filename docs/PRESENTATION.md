# Main Street Risk Desk: two-minute demo script

Spoken by one presenter with the Streamlit app on screen and a phone that can show iMessage.
Times are cumulative. Lines in *italics* are actions, not speech.

## Before you go on stage (5 minutes earlier)

```bash
source .venv/bin/activate
python -m decisiondesk.precompute                 # warms the forecast caches (about 2 min cold, seconds warm)
python -m decisiondesk.messaging.worker status    # Photon connected, your phone listed as opted in
streamlit run app/streamlit_app.py
```

- Open the app and pick **Demo restaurant (Austin, TX)**. Open each tab once so nothing loads live on stage.
- In **Analyst**, check the "Active analyst" line. If it says the offline analyst, or Gemini is slow
  (the free tier was returning 503 and slow replies during testing), say so plainly on stage.
- In **Messaging**, choose **Photon iMessage (live)**, tick the alert opt-in for your phone, and
  switch on **Auto-check for replies**. If Photon is down, switch to **Local simulator**. The screen
  then says LOCAL SIMULATOR, and you say that too.

## Script

**0:00 – Problem.**
"Small businesses make money decisions with a spreadsheet and a gut feeling. When food prices
jump, a taqueria owner has to decide by Friday: raise prices, push the supplier, or cut spending.
Main Street Risk Desk turns their own sales history, plus real economic data, into that decision."

**0:15 – Data, honestly labelled.**
*Overview tab.*
"This is Lone Star Taqueria in Austin. It is a synthetic business. We generated three years of
realistic daily sales, costs and bills, and the app labels every number by where it came from.
Synthetic, real, predicted or assumed: you always know which."

**0:30 – Forecast.**
*Forecast tab.*
"We back-test several forecasting models on past data and keep a machine-learning model only if
it beats a simple seasonal baseline out of sample. Here is the next 28 days of revenue with an 80%
range."

**0:45 – Real economic context.**
*Economy tab.*
"These are real Federal Reserve FRED figures, using only what was published on each date, so
there's no hindsight in our tests."

**0:55 – The shock and the decision.**
*Decisions tab: set food costs to +12% and starting cash to a low value, then run.*
"Say food costs rise 12%. We simulate thousands of possible next months for each action against
doing nothing. Each action gets an expected profit change, a range, and the share of scenarios
that run out of cash. Actions that break the owner's own limits, like 'never raise prices more
than 5%', are excluded."

**1:15 – The alert reaches the phone.**
*Messaging tab: Scan risks and send opted-in alerts.*
"The owner opted in to alerts. Each alert is built only from numbers the models just produced,
checked automatically, sent once, and STOP works at any time."
*Show the iMessage arriving on the phone.*

**1:30 – Two-way conversation.**
*Type "What should I do?" on the phone.*
"The owner just replies by text. Gemini reads the question and calls our decision tools. It
doesn't do the math itself. Every number in its answer is checked against the tool outputs before
it is sent."
*The reply arrives, and the conversation also appears in the Messaging tab.*

**1:50 – Close.**
"Forecasts are separate from cause and effect: price and supplier effects are stated assumptions,
not proven results. We haven't validated this on a real business yet; that's the next step.
Main Street Risk Desk gives an owner a quantified decision, by text, in under a minute."

## If something fails on stage

- **Gemini error or timeout.** The reply starts with "(Gemini unavailable: …)" and the offline
  analyst answers with the same tools. Say: "The language model is down, so the rule-based
  analyst answered. The numbers come from the same tools."
- **The phone gets nothing.** Show the Messaging tab. A status of `dispatched` or `accepted`
  means Photon took the message; it is only called delivered once Photon confirms `delivered`.
  Switch to the simulator and continue.
