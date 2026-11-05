"""AI battle commentary.

Combat is computed deterministically by the engine. The narrator only *describes* a finished
battle from its recorded facts; it can never change a die, a casualty or an owner. If the LLM is
not configured or fails, a plainly labelled automated summary is stored instead (source="fallback").
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)
API_URL = "https://api.anthropic.com/v1/messages"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def battle_facts(rec):
    """Compact, verified facts used for the prompt and for the fallback."""
    lines = [f"Battle {rec['battle_no']} (round {rec['round']}): {rec['attacker']} attacked {rec['defender']} "
             f"({rec['from']} -> {rec['to']}), {rec['att_start']} v {rec['def_start']} soldiers."]
    for ph in rec["phases"]:
        pairs = ", ".join(f"{a} v {d}" for a, d in zip(ph["attacker_dice"], ph["defender_dice"]))
        lines.append(f"Phase {ph['no']}: pairs {pairs}; attacker lost {ph['att_loss']}, defender lost "
                     f"{ph['def_loss']}; soldiers now {ph['att_after']} v {ph['def_after']}.")
    outcome = {"conquest": f"{rec['attacker']} conquered {rec['to']} and garrisoned it with {rec.get('gain')} soldiers.",
               "exhausted": "The attacker was stalled at 1 soldier; the defender held.",
               "withdrawn": "The attacker stopped the attack."}[rec["outcome"]]
    lines.append("Outcome: " + outcome)
    return "\n".join(lines)


def fallback_text(rec):
    n = len(rec["phases"])
    target = rec['to'] if rec['defender'] == rec['to'] else f"{rec['defender']}'s {rec['to']}"
    base = (f"{rec['attacker']} attacked {target} from {rec['from']} "
            f"({rec['att_start']} v {rec['def_start']}). Over {n} phase{'s' if n != 1 else ''} the attacker lost "
            f"{rec['att_lost']} and the defender lost {rec['def_lost']}. ")
    if rec["outcome"] == "conquest":
        return base + f"{rec['to']} fell and was garrisoned with {rec.get('gain')} soldiers."
    if rec["outcome"] == "exhausted":
        return base + f"The attack stalled at one soldier and {rec['to']} held."
    return base + "The attacker stopped the assault."


def phase_facts(ctx):
    """Facts for ONE phase of a battle that is still in progress."""
    ph = ctx["phase"]
    pairs = ", ".join(f"{a} v {d}" for a, d in zip(ph["attacker_dice"], ph["defender_dice"]))
    return (f"Battle {ctx['battle_no']} is still in progress: {ctx['attacker']} ({ctx['from']}) is attacking "
            f"{ctx['defender']} ({ctx['to']}); it started {ctx['att_start']} v {ctx['def_start']} soldiers.\n"
            f"Phase {ph['no']}: dice pairs (attacker v defender) {pairs}; attacker lost {ph['att_loss']}, "
            f"defender lost {ph['def_loss']}; soldiers now {ph['att_after']} v {ph['def_after']}.\n"
            f"The battle has NOT ended yet.")


def phase_fallback_text(ctx):
    ph = ctx["phase"]
    return (f"Phase {ph['no']} at {ctx['to']}: {ctx['attacker']} lost {ph['att_loss']}, "
            f"{ctx['defender']} lost {ph['def_loss']}. Now {ph['att_after']} v {ph['def_after']}.")


class CommentaryService:
    def __init__(self, api_key="", model="claude-haiku-5-5", timeout=15, gemini_key="", gemini_model="gemini-3.5-flash"):
        self.api_key, self.model, self.timeout = api_key, model, timeout
        self.gemini_key, self.gemini_model = gemini_key, gemini_model

    @property
    def provider(self):
        # Anthropic is preferred when configured; Gemini (AI Studio free tier) is the no-cost alternative.
        return "anthropic" if self.api_key else ("gemini" if self.gemini_key else None)

    def narrate(self, rec):
        """Returns {"text", "source"} where source is "ai" or "fallback"."""
        if self.provider:
            try:
                return {"text": self._llm(rec), "source": "ai"}
            except Exception as exc:     # network, quota, bad key ... never break the game or leak the key
                log.warning("LLM commentary failed (%s); using automated summary", type(exc).__name__)
        return {"text": fallback_text(rec), "source": "fallback"}

    def narrate_phase(self, ctx):
        """Live line for one phase of an unfinished battle. Same guarantees as narrate()."""
        if self.provider:
            try:
                prompt = ("You are the live play-by-play commentator for a dice-based world-conquest strategy game. "
                          "In ONE vivid sentence (max 35 words) describe the phase below as it happens. Use ONLY the "
                          "facts given; never invent numbers, dice or outcomes and do not say who wins the battle. "
                          "No markdown.\n\n" + phase_facts(ctx))
                return {"text": self._call(prompt, 120), "source": "ai"}
            except Exception as exc:
                log.warning("LLM live commentary failed (%s); using automated summary", type(exc).__name__)
        return {"text": phase_fallback_text(ctx), "source": "fallback"}

    def _llm(self, rec):
        prompt = ("You are the play-by-play commentator for a dice-based world-conquest strategy game. "
                  "In 2-4 vivid sentences (max 90 words) narrate the battle below. Use ONLY the facts given; "
                  "never invent numbers, dice or outcomes. No markdown.\n\n" + battle_facts(rec))
        return self._call(prompt, 250)

    def _call(self, prompt, max_tokens):
        if self.provider == "gemini":
            r = requests.post(GEMINI_URL.format(model=self.gemini_model), params={"key": self.gemini_key},
                              json={"contents": [{"parts": [{"text": prompt}]}],
                                    "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.9}},
                              timeout=self.timeout)
            r.raise_for_status()
            try:
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            except (KeyError, IndexError, TypeError):
                text = ""
            if not text:
                raise ValueError("empty LLM response")
            return text
        r = requests.post(API_URL, headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                            "content-type": "application/json"},
                          json={"model": self.model, "max_tokens": max_tokens,
                                "messages": [{"role": "user", "content": prompt}]},
                          timeout=self.timeout)
        r.raise_for_status()
        text = "".join(p.get("text", "") for p in r.json().get("content", []) if p.get("type") == "text").strip()
        if not text:
            raise ValueError("empty LLM response")
        return text
