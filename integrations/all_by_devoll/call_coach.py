"""Live prospect call coaching. Audio and transcripts are transient until an operator saves notes."""
from html import escape
import json
from pathlib import Path
import re

import office_automation as office
import shop_ui
import stock
from stock_ui import field, hidden

ASSETS = Path(__file__).resolve().parent / "call_coach_assets"

# Pick a short approved line from the current utterance. The model only
# transcribes speech, so a remark on the call cannot change prices or terms.
RESPONSES = (
    (r"not interested|stop calling|do not call|take us off|remove .*list",
     "Understood. Thanks for your time."),
    (r"already (?:have|use)|have (?:a |our )?(?:vendor|contractor|someone|company)|under contract",
     "Who handles backup when your current contractor is booked? We can start with one property."),
    (r"send (?:me |us )?(?:something|an? email|information|a quote)|email (?:me|us)",
     "What email should I use, and when would be a good time to follow up?"),
    (r"how much|what .*charge|price|rate|cost|estimate|quote",
     "What address and scope should we price? We can give you a firm quote after we see it."),
    (r"garage|basement|cleanout|clean out|move everything|haul .*away",
     "We do the lifting, loading, and disposal. Could you send photos and the property address?"),
    (r"snow|plow|salt|sidewalk",
     "Which properties need plowing, and do they need sidewalks or salting too?"),
    (r"scrap|appliance|metal|recycling",
     "What material is it, where is it located, and how often does it accumulate?"),
    (r"trash|garbage|overflow|dumpster",
     "How many stops or bins are there, and how often do they need service?"),
    (r"multiple|locations|properties|portfolio|rentals",
     "Which properties are close together? We can quote them together on one monthly bill."),
    (r"who handles|wrong person|property manager|maintenance manager",
     "Who handles outside service vendors, and what’s the best way to reach them?"),
    (r"busy|call back|later|next week|in a meeting",
     "When is a better time for a quick call?"),
)
DEFAULT = "Which property should we look at first, and what service do you need there?"


def suggestion(utterance):
    text = " ".join(utterance.casefold().split())
    return next((line for pattern, line in RESPONSES if re.search(pattern, text)), DEFAULT)


def native():
    from java import jclass
    return jclass("com.tomeygeneral.elgrande.CallSpeech")


def speech_action(action, data):
    engine = native()
    if action == "prepare":
        engine.prepare()
        return {"ready": True}
    if action == "start":
        return {"session": str(engine.start())}
    session = data.get("session", "")
    if not re.fullmatch(r"[0-9a-f-]{36}", session):
        raise ValueError("Choose an active coaching session.")
    if action == "stop":
        result = json.loads(str(engine.stop(session)))
    else:
        index = data.get("index", "")
        audio = data.get("audio", "")
        if (not re.fullmatch(r"[0-9]{1,8}", index) or len(audio) > 21500
                or not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", audio)):
            raise ValueError("Invalid call audio frame.")
        result = json.loads(str(engine.audio(session, int(index), audio)))
    latest = result.get("latest") or result.get("lastFinal")
    if latest:
        result["suggestion"] = suggestion(latest)
    return result


def account_id(raw):
    return stock.identifier(raw)


def page(con, raw):
    lead = office.account(con, account_id(raw))
    number = office.phone_key(lead["phone"])
    destination = "+1" + number if number else ""
    choices = "".join(f'<option value="{stage}"{" selected" if stage == lead["stage"] else ""}>'
                      f'{stage.title()}</option>' for stage in office.STAGES)
    config = (
        field("server", "SIP WebSocket address (wss://)", required=True)
        + field("aor", "SIP account (sip:user@domain)", required=True)
        + field("username", "SIP authorization username")
        + field("password", "SIP password", kind="password", required=True)
        + field("domain", "Outbound dial domain (defaults to SIP account domain)")
    )
    body = f"""
<section class="panel" id="call-coach">
  <h2>Call coach · {escape(lead["name"])}</h2>
  <p><a href="/office?account={lead["id"]}#account">Back to lead</a></p>
  <details open><summary>VoIP account</summary>
    <form id="dial" method="post" action="/office/call-credentials" autocomplete="off">
      <div class="fields">{config}{field("destination", "Number or SIP address", destination, required=True)}</div>
      <label><input type="checkbox" name="register"> Register before dialing if the provider requires it</label>
      <button type="submit">Place call</button>
    </form>
  </details>
  <audio id="remote" autoplay></audio>
  <p id="call-status" role="status">Ready</p>
  <button type="button" id="hangup" disabled>Hang up</button>
<form id="keypad">
  <label>Phone menu digit <input name="tone" maxlength="1" inputmode="tel" pattern="[0-9*#]" required></label>
  <button type="submit" disabled>Send digit</button>
</form>
  <button type="button" id="prepare">Prepare English speech model</button>
  <p id="speech-status" role="status">The speech model downloads once. Transcription runs on this phone.</p>
  <p>Tell the prospect you’d like to use live transcription and get their permission before starting it.</p>
  <button type="button" id="transcribe" disabled>Start live coaching</button>
  <button type="button" id="stop-coach" disabled>Stop coaching</button>
  <h3>Say next</h3>
  <p id="suggestion" class="price" aria-live="polite">Who handles snow, hauling, or cleanouts for your properties?</p>
  <h3>Prospect transcript</h3><p id="transcript"></p>
</section>
<section class="panel">
  <h2>Call notes</h2>
  <form method="post" action="/office/call">
    {hidden("account_id", lead["id"])}
    <label>Outcome<select name="stage">{choices}</select></label>
    {field("follow_up", "Next follow-up", kind="date")}
    <label>Notes<textarea name="notes" id="call-notes" maxlength="500" required></textarea></label>
    <button type="button" id="copy-notes">Copy recent transcript into notes</button>
    <button type="submit">Save call and follow-up</button>
  </form>
</section>
<script src="/office/call-assets/sip.js" defer></script>
<script src="/office/call-assets/coach.js" defer></script>
"""
    return shop_ui.page_shell(body)
